"""Base class of the floating in-place editors (M2 field editor, M3 annotation editor).

``FloatingEditorOverlay`` shows one Qt editor widget over an *anchor* (any object with
``page`` and ``rect`` in page space) as a child of the PageView's viewport, keeps it
over the anchor while the view scrolls, zooms or resizes, hides it while the anchor is
scrolled out of view, and reports the result through ``committed``/``cancelled``. It
never touches the document: the owner of the result pushes the undo command.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import shiboken6
from PySide6.QtCore import QEvent, QObject, QRect, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QFocusEvent, QFont, QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPlainTextEdit, QWidget

if TYPE_CHECKING:
    from pdfeditor.core.document import PdfDocument
    from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)

MIN_FONT_PX = 9

_UNDO = QKeySequence.StandardKey.Undo
_REDO = QKeySequence.StandardKey.Redo
ENTER_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
#: Keys an open editor claims on ShortcutOverride (window shortcuts must not steal them).
HANDLED_KEYS = (*ENTER_KEYS, Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Escape)
#: Focus changes that must not commit: a popup (combo list, context menu, menu bar)
#: took focus, or the whole window was deactivated (focus comes back on reactivation).
_IGNORED_FOCUS_REASONS = (
    Qt.FocusReason.PopupFocusReason,
    Qt.FocusReason.ActiveWindowFocusReason,
)


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


class FloatingEditorOverlay(QObject):
    """One floating editor widget over an anchor ``(page, rect)`` of ``view``.

    Subclasses implement :meth:`_create_editor` (a hidden widget, child of the viewport),
    :meth:`_editor_value`, :meth:`_initial_value` (``committed`` is emitted only when the
    value differs, see :meth:`_should_commit`) and may override :meth:`_key_press`,
    :meth:`_font_px`, :meth:`_editor_geometry`, :meth:`_popup_open`,
    :meth:`_before_teardown` and :meth:`_on_document_switch`; they open an editor with
    :meth:`_open_anchor`.

    Behaviour shared by every editor: focus-out to anything outside the editor commits
    (not for popups, window switches, or while hidden because scrolled out of view; the
    focus is restored when it scrolls back); editor keys are claimed on
    ``ShortcutOverride``; Ctrl+Z / Ctrl+Y undo the typing first, then pass through to the
    window's Undo/Redo. ``committed(anchor, value)`` is emitted **after** teardown
    (``is_open`` is False, so a handler may open another editor). The overlay commits on
    the document's ``structure_changed``/``path_changed``/``reloaded`` and on
    ``page_changed`` of its page when that page's size or rotation changed (else it
    repositions). ``pages_remapped`` (right before ``structure_changed``) first moves the
    anchor to its page's new index (:meth:`_remap_anchor`), so the committed anchor
    names the right page after pages were inserted, deleted or moved; an editor whose
    page is gone closes silently (there is nothing left to write the value to).
    """

    committed = Signal(object, object)  # (anchor, value)
    cancelled = Signal()

    def __init__(
        self,
        view: PageView,
        document: PdfDocument | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent if parent is not None else view)
        self._view = view
        self._viewport = view.viewport()
        self._document: PdfDocument | None = None
        self._anchor: Any = None
        self._editor: QWidget | None = None
        self._page_size: QSizeF | None = None
        self._page_rotation: int | None = None
        self._hiding = False  # editor hidden because scrolled out of view
        self._restore_focus = False
        view.horizontalScrollBar().valueChanged.connect(self.reposition)
        view.verticalScrollBar().valueChanged.connect(self.reposition)
        view.zoom_changed.connect(self.reposition)
        # The viewport's Resize reaches our filter before QGraphicsView re-aligns the
        # scene, so reposition on the next event-loop turn.
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(0)
        self._resize_timer.timeout.connect(self.reposition)
        self._viewport.installEventFilter(self)
        if document is not None:
            self.set_document(document)

    # -- state ----------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._anchor is not None

    @property
    def editor(self) -> QWidget | None:
        """The live editor widget while open (tests, focus handling)."""
        return self._editor

    @property
    def document(self) -> PdfDocument | None:
        return self._document

    @property
    def view(self) -> PageView:
        return self._view

    def anchor_rect(self) -> QRectF | None:
        """Page-space rect the editor covers (None when closed)."""
        anchor = self._anchor
        return None if anchor is None else QRectF(anchor.rect)

    # -- document binding -------------------------------------------------------
    def set_document(self, document: PdfDocument | None) -> None:
        """Follow ``document``; a pending edit on the previous one is resolved first
        (:meth:`_on_document_switch`: committed by default)."""
        if document is self._document:
            return
        self._on_document_switch()
        old = self._document
        if old is not None:
            try:
                old.page_changed.disconnect(self._on_page_changed)
                old.pages_remapped.disconnect(self._on_pages_remapped)
                old.structure_changed.disconnect(self._commit_on_change)
                old.path_changed.disconnect(self._commit_on_change)
                old.reloaded.disconnect(self._commit_on_change)
            except (RuntimeError, TypeError):
                pass
        self._document = document
        if document is not None:
            document.page_changed.connect(self._on_page_changed)
            document.pages_remapped.connect(self._on_pages_remapped)
            document.structure_changed.connect(self._commit_on_change)
            document.path_changed.connect(self._commit_on_change)
            document.reloaded.connect(self._commit_on_change)

    def _on_document_switch(self) -> None:
        self.commit()

    # -- public API -------------------------------------------------------------
    def commit(self) -> bool:
        """Close the editor and emit ``committed`` if :meth:`_should_commit` says so.

        Returns True when ``committed`` was emitted. A no-op when closed (so a focus-out
        caused by closing never commits twice).
        """
        anchor = self._anchor
        if anchor is None:
            return False
        if self._editor is None or not shiboken6.isValid(self._editor):
            self._teardown()  # the view (and the editor with it) was destroyed
            return False
        value = self._editor_value()
        self._teardown()
        if not self._should_commit(anchor, value):
            return False
        self.committed.emit(anchor, value)
        return True

    def cancel(self) -> None:
        """Close the editor, discard the edit and emit ``cancelled``."""
        if self._anchor is None:
            return
        self._teardown()
        self.cancelled.emit()

    def close(self) -> None:
        """Close the editor silently (no commit, no signal)."""
        if self._anchor is not None:
            self._teardown()

    def reposition(self, *_args: object) -> None:
        """Move/resize the editor over its anchor; hide it while out of view."""
        anchor, editor = self._anchor, self._editor
        if anchor is None or editor is None or not self._alive():
            return
        view = self._view
        page_rect = self.anchor_rect()
        if page_rect is None or not 0 <= anchor.page < view.page_count:
            return
        font = QFont(editor.font())
        font.setPixelSize(self._font_px())
        editor.setFont(font)
        rect = self._editor_geometry(view.page_rect_to_viewport(anchor.page, page_rect))
        editor.setGeometry(rect)
        visible = rect.intersects(view.viewport().rect())
        if visible and editor.isHidden():
            editor.show()
            if self._restore_focus:
                self._restore_focus = False
                editor.setFocus(Qt.FocusReason.OtherFocusReason)
        elif not visible and not editor.isHidden():
            self._restore_focus = self._has_focus()
            self._hiding = True
            try:
                editor.hide()
            finally:
                self._hiding = False

    # -- hooks ----------------------------------------------------------------------
    def _create_editor(self, anchor: Any) -> QWidget:
        """A new, hidden editor widget (child of the viewport) for ``anchor``."""
        raise NotImplementedError

    def _editor_value(self) -> Any:
        raise NotImplementedError

    def _initial_value(self, anchor: Any) -> Any:
        raise NotImplementedError

    def _should_commit(self, anchor: Any, value: Any) -> bool:
        return value != self._initial_value(anchor)

    def _font_px(self) -> int:
        """Editor font pixel size at the current zoom."""
        return MIN_FONT_PX

    def _editor_geometry(self, rect: QRect) -> QRect:
        """Final viewport geometry, given the anchor rect mapped to the viewport."""
        return rect

    def _popup_open(self) -> bool:
        return False

    def _before_teardown(self, editor: QWidget) -> None:
        """Release widget-specific connections (the editor is still valid)."""

    def _remap_anchor(self, anchor: Any, page: int) -> Any:
        """``anchor`` with its page index replaced by ``page`` (a frozen dataclass with a
        ``page`` field by default)."""
        return replace(anchor, page=page)

    def _key_press(self, event: QKeyEvent) -> bool:
        """Default keys: Enter commits (Ctrl+Enter in a QPlainTextEdit), Escape cancels."""
        if self._popup_open():
            return False
        key = event.key()
        if key in ENTER_KEYS:
            if isinstance(self._editor, QPlainTextEdit) and not (
                event.modifiers() & Qt.KeyboardModifier.ControlModifier
            ):
                return False  # newline
            self.commit()
            return True
        if key == Qt.Key.Key_Escape:
            self.cancel()
            return True
        return False

    # -- opening --------------------------------------------------------------------
    def _open_anchor(self, anchor: Any) -> None:
        """Show an editor for ``anchor`` (a pending edit is committed first)."""
        if self.is_open:
            self.commit()
        if not 0 <= anchor.page < self._view.page_count:
            raise ValueError(f"page {anchor.page} is not shown")
        editor = self._create_editor(anchor)
        self._anchor = anchor
        self._editor = editor
        doc = self._document
        self._page_size = doc.page_size(anchor.page) if doc is not None else None
        self._page_rotation = doc.page_rotation(anchor.page) if doc is not None else None
        for w in (editor, *editor.findChildren(QWidget)):
            w.installEventFilter(self)
        self.reposition()  # shows the editor unless its anchor is out of view
        if not editor.isHidden():
            editor.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self._restore_focus = True

    # -- teardown ---------------------------------------------------------------------
    def _has_focus(self) -> bool:
        editor = self._editor
        if editor is None or not shiboken6.isValid(editor):
            return False
        focus = QApplication.focusWidget()
        return focus is not None and (focus is editor or editor.isAncestorOf(focus))

    def _teardown(self) -> None:
        editor = self._editor
        had_focus = self._has_focus()
        # Mark closed first: hiding the focused editor sends FocusOut, which must not
        # commit again (re-entrancy guard).
        self._anchor = None
        self._editor = None
        self._page_size = None
        self._page_rotation = None
        self._restore_focus = False
        if editor is None or not shiboken6.isValid(editor):
            return
        for w in (editor, *editor.findChildren(QWidget)):
            w.removeEventFilter(self)
        self._before_teardown(editor)
        if had_focus and self._alive():
            self._view.setFocus(Qt.FocusReason.OtherFocusReason)
        editor.hide()
        editor.deleteLater()

    # -- slots --------------------------------------------------------------------------
    def _commit_on_change(self, *_args: object) -> None:
        self.commit()

    def _on_pages_remapped(self, mapping: object) -> None:
        """Move the anchor to its page's new index (``structure_changed`` then commits
        it); close silently when the page is gone."""
        anchor = self._anchor
        if anchor is None:
            return
        old_to_new = list(mapping)  # type: ignore[call-overload]
        page = anchor.page
        new = old_to_new[page] if 0 <= page < len(old_to_new) else None
        if new is None:
            log.info("closing the editor of page %d: the page is gone", page + 1)
            self.close()
        elif new != page:
            self._anchor = self._remap_anchor(anchor, new)

    def _on_page_changed(self, i: int) -> None:
        anchor = self._anchor
        if anchor is None or i != anchor.page:
            return
        doc = self._document
        size = rotation = None
        if doc is not None and i < doc.page_count:
            size, rotation = doc.page_size(i), doc.page_rotation(i)
        if (
            size is None
            or self._page_size is None
            or size != self._page_size
            or rotation != self._page_rotation
        ):
            # Rotation (even of a square page) or a vanished page: the snapshot rect is
            # stale. (UI rotations commit first, through ``DocumentView.push``.)
            self.commit()
        else:
            self.reposition()

    # -- events -------------------------------------------------------------------------
    def _alive(self) -> bool:
        # While the view is being destroyed its children still send events (FocusOut,
        # Hide) that reach this filter; the PageView wrapper is then already invalid.
        return shiboken6.isValid(self._view) and shiboken6.isValid(self._viewport)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if not self._alive():
            return False
        etype = event.type()
        if watched is self._viewport:
            if etype == QEvent.Type.Resize and self._anchor is not None:
                self._resize_timer.start()
            return False
        if self._anchor is None:
            return False
        if etype == QEvent.Type.ShortcutOverride:
            # Keep window shortcuts (e.g. Escape, Return) from stealing our keys.
            assert isinstance(event, QKeyEvent)
            if event.key() in HANDLED_KEYS and not self._popup_open():
                event.accept()
                return True
            # Ctrl+Z / Ctrl+Y undo the typing first (the text editor's own history);
            # with nothing left to undo locally, let the window's Undo/Redo run (it
            # commits this editor, then undoes the document).
            for redo in (False, True):
                if event.matches(_REDO if redo else _UNDO) and not self._local_history(redo):
                    return True  # filtered but not accepted: the shortcut fires
            return False
        if etype == QEvent.Type.KeyPress:
            assert isinstance(event, QKeyEvent)
            return self._key_press(event)
        if etype == QEvent.Type.FocusOut:
            assert isinstance(event, QFocusEvent)
            self._focus_out(event)
        return False

    def _local_history(self, redo: bool) -> bool:
        """The text editor has typing to undo (``redo``: to redo) of its own."""
        editor = self._editor
        if isinstance(editor, QComboBox):
            editor = editor.lineEdit()  # None for a non-editable combo
        if isinstance(editor, QLineEdit):
            return editor.isRedoAvailable() if redo else editor.isUndoAvailable()
        if isinstance(editor, QPlainTextEdit):
            document = editor.document()
            return document.isRedoAvailable() if redo else document.isUndoAvailable()
        return False

    def _focus_out(self, event: QFocusEvent) -> None:
        if self._hiding or event.reason() in _IGNORED_FOCUS_REASONS:
            return
        if self._popup_open() or self._has_focus():
            return  # focus moved inside the editor (e.g. to an editable combo's line edit)
        self.commit()


__all__ = ["ENTER_KEYS", "HANDLED_KEYS", "MIN_FONT_PX", "FloatingEditorOverlay"]
