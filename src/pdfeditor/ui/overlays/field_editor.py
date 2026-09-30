"""Floating in-place editor for text/choice form fields (M2).

``FieldEditorOverlay`` shows one Qt editor widget over a field, as a child of the
PageView's viewport, and reports the result through signals. It never touches the
document: the form tool listens to ``committed`` and pushes a ``SetFieldValueCommand``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import shiboken6
from PySide6.QtCore import QEvent, QObject, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QFocusEvent, QFont, QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QWidget,
)

from pdfeditor.core.forms import FieldKind, WidgetInfo

if TYPE_CHECKING:
    from pdfeditor.core.document import PdfDocument
    from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)

#: Field kinds the overlay can edit (checkboxes/radios toggle without an editor).
EDITABLE_KINDS = (FieldKind.TEXT, FieldKind.COMBO, FieldKind.LIST)
MIN_FONT_PX = 9
DEFAULT_FONT_PT = 10.0
EDITOR_STYLE = "border: 1px solid rgb(0, 120, 215); padding: 0px; background: white;"

_ENTER_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
_HANDLED_KEYS = (*_ENTER_KEYS, Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Escape)
#: Focus changes that must not commit: a popup (combo list, context menu, menu bar)
#: took focus, or the whole window was deactivated (focus comes back on reactivation).
_IGNORED_FOCUS_REASONS = (
    Qt.FocusReason.PopupFocusReason,
    Qt.FocusReason.ActiveWindowFocusReason,
)


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


class _ChoiceCombo(QComboBox):
    """QComboBox that knows whether ``activated`` comes from its popup.

    ``activated`` is also emitted by Up/Down on a closed combo; only a choice made in the
    popup should commit. Qt emits ``activated`` right after ``hidePopup()`` in the same
    call, so the flag is cleared on the next event-loop turn.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.popup_active = False

    def showPopup(self) -> None:  # noqa: N802 (Qt override)
        self.popup_active = True
        super().showPopup()

    def hidePopup(self) -> None:  # noqa: N802 (Qt override)
        super().hidePopup()
        QTimer.singleShot(0, self._popup_closed)

    def _popup_closed(self) -> None:
        self.popup_active = False


class FieldEditorOverlay(QObject):
    """One floating editor over a text, combo or list field of ``view``.

    ``open(info)`` shows a QLineEdit (text; ``setMaxLength`` when ``info.max_len > 0``),
    QPlainTextEdit (multiline: Enter inserts a newline, Ctrl+Enter commits), QComboBox
    (display strings shown, export value committed; editable combos commit free text as
    typed) or QListWidget (single selection, export value committed), prefilled with
    ``info.value``. Keys: Enter commits, Tab/Shift+Tab commit then emit ``navigate``,
    Escape cancels; losing focus to anything outside the editor commits. The editor
    follows scrolling, zoom and viewport resizes and hides while the field is scrolled
    out of view. Rotated pages: the editor stays horizontal.

    ``committed(info, value)`` is emitted **after** the editor is closed (``is_open`` is
    False, so a handler may open another field) and **only when the value changed**;
    committing an unchanged value just closes. ``value`` is always a ``str``: the text
    (newlines ``"\\n"``), the chosen export value, or ``""`` for no selection.
    ``info`` is the snapshot passed to ``open`` (its document is ``self.document`` at
    emission time, even on a document change).
    """

    committed = Signal(object, object)  # (WidgetInfo, str)
    cancelled = Signal()
    navigate = Signal(bool)  # backwards

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
        self._info: WidgetInfo | None = None
        self._editor: QWidget | None = None
        self._page_size: QSizeF | None = None
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
        return self._info is not None

    @property
    def current_info(self) -> WidgetInfo | None:
        return self._info

    @property
    def editor(self) -> QWidget | None:
        """The live editor widget while open (tests, focus handling)."""
        return self._editor

    @property
    def document(self) -> PdfDocument | None:
        return self._document

    # -- document binding -------------------------------------------------------
    def set_document(self, document: PdfDocument | None) -> None:
        """Follow ``document``; a pending edit on the previous one is committed first."""
        if document is self._document:
            return
        self.commit()
        old = self._document
        if old is not None:
            try:
                old.page_changed.disconnect(self._on_page_changed)
                old.structure_changed.disconnect(self._commit_on_change)
                old.path_changed.disconnect(self._commit_on_change)
                old.reloaded.disconnect(self._commit_on_change)
            except (RuntimeError, TypeError):
                pass
        self._document = document
        if document is not None:
            document.page_changed.connect(self._on_page_changed)
            document.structure_changed.connect(self._commit_on_change)
            document.path_changed.connect(self._commit_on_change)
            document.reloaded.connect(self._commit_on_change)

    # -- public API -------------------------------------------------------------
    def open(self, info: WidgetInfo) -> None:
        """Show an editor for ``info`` (a pending edit is committed first)."""
        if info.kind not in EDITABLE_KINDS:
            raise ValueError(f"no editor for {info.kind} fields")
        if self.is_open:
            self.commit()
        if not 0 <= info.page < self._view.page_count:
            raise ValueError(f"page {info.page} is not shown")
        editor = self._create_editor(info)
        self._info = info
        self._editor = editor
        doc = self._document
        self._page_size = doc.page_size(info.page) if doc is not None else None
        for w in (editor, *editor.findChildren(QWidget)):
            w.installEventFilter(self)
        self.reposition()  # shows the editor unless its field is out of view
        if not editor.isHidden():
            editor.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self._restore_focus = True

    def commit(self) -> bool:
        """Close the editor and emit ``committed`` if the value changed.

        Returns True when ``committed`` was emitted. A no-op when closed (so a focus-out
        caused by closing never commits twice).
        """
        info = self._info
        if info is None:
            return False
        if self._editor is None or not shiboken6.isValid(self._editor):
            self._teardown()  # the view (and the editor with it) was destroyed
            return False
        value = self._editor_value()
        self._teardown()
        if value == self._initial_value(info):
            return False
        self.committed.emit(info, value)
        return True

    def cancel(self) -> None:
        """Close the editor, discard the edit and emit ``cancelled``."""
        if self._info is None:
            return
        self._teardown()
        self.cancelled.emit()

    def close(self) -> None:
        """Close the editor silently (no commit, no signal)."""
        if self._info is not None:
            self._teardown()

    def reposition(self, *_args: object) -> None:
        """Move/resize the editor over its field; hide it while out of view."""
        info, editor = self._info, self._editor
        if info is None or editor is None or not self._alive():
            return
        view = self._view
        if not 0 <= info.page < view.page_count:
            return
        rect = view.page_rect_to_viewport(info.page, info.rect)
        editor.setGeometry(rect)
        font = QFont(editor.font())
        font.setPixelSize(self._font_px(info))
        editor.setFont(font)
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

    # -- editor construction ------------------------------------------------------
    def _font_px(self, info: WidgetInfo) -> int:
        size = info.font_size or DEFAULT_FONT_PT
        return max(MIN_FONT_PX, round(size * self._view.view_scale))

    def _create_editor(self, info: WidgetInfo) -> QWidget:
        parent = self._view.viewport()
        editor: QWidget
        if info.kind is FieldKind.TEXT and info.multiline:
            edit = QPlainTextEdit(parent)
            edit.setPlainText(_normalize_newlines(info.value))
            edit.setTabChangesFocus(True)
            edit.setStyleSheet(EDITOR_STYLE)
            edit.moveCursor(QTextCursor.MoveOperation.End)
            editor = edit
        elif info.kind is FieldKind.TEXT:
            line = QLineEdit(parent)
            if info.max_len > 0:
                line.setMaxLength(info.max_len)
            line.setText(info.value)
            line.setStyleSheet(EDITOR_STYLE)
            line.selectAll()
            editor = line
        elif info.kind is FieldKind.COMBO:
            combo = _ChoiceCombo(parent)
            combo.setEditable(info.editable_combo)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            for export, display in info.choices:
                combo.addItem(display, export)
            index = self._choice_index(info)
            combo.setCurrentIndex(index)
            if index < 0 and info.editable_combo:
                combo.setEditText(info.value)
            combo.activated.connect(self._on_combo_activated)
            editor = combo
        else:
            lst = QListWidget(parent)
            lst.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            for export, display in info.choices:
                item = QListWidgetItem(display, lst)
                item.setData(Qt.ItemDataRole.UserRole, export)
            index = self._choice_index(info)
            if index >= 0:
                lst.setCurrentRow(index)
                lst.scrollToItem(lst.item(index))
            else:
                lst.setCurrentRow(-1)
                lst.clearSelection()
            lst.itemDoubleClicked.connect(lambda _item: self.commit())
            editor = lst
        editor.setObjectName("fieldEditor")
        editor.hide()
        return editor

    @staticmethod
    def _choice_index(info: WidgetInfo) -> int:
        if not info.value:
            return -1
        for i, (export, _display) in enumerate(info.choices):
            if export == info.value:
                return i
        for i, (_export, display) in enumerate(info.choices):
            if display == info.value:
                return i
        return -1

    # -- values ---------------------------------------------------------------------
    @staticmethod
    def _initial_value(info: WidgetInfo) -> str:
        return _normalize_newlines(info.value) if info.multiline else info.value

    def _editor_value(self) -> str:
        editor, info = self._editor, self._info
        assert editor is not None and info is not None
        if isinstance(editor, QPlainTextEdit):
            return editor.toPlainText()
        if isinstance(editor, QLineEdit):
            return editor.text()
        if isinstance(editor, QComboBox):
            if editor.isEditable():
                text = editor.currentText()
                for export, display in info.choices:
                    if display == text:
                        return export
                return text
            data = editor.currentData()
            return "" if data is None else str(data)
        assert isinstance(editor, QListWidget)
        items = editor.selectedItems()
        if not items:
            return ""
        return str(items[0].data(Qt.ItemDataRole.UserRole))

    # -- teardown ---------------------------------------------------------------------
    def _has_focus(self) -> bool:
        editor = self._editor
        if editor is None or not shiboken6.isValid(editor):
            return False
        focus = QApplication.focusWidget()
        return focus is not None and (
            focus is editor or editor.isAncestorOf(focus)
        )

    def _teardown(self) -> None:
        editor = self._editor
        had_focus = self._has_focus()
        # Mark closed first: hiding the focused editor sends FocusOut, which must not
        # commit again (re-entrancy guard).
        self._info = None
        self._editor = None
        self._page_size = None
        self._restore_focus = False
        if editor is None or not shiboken6.isValid(editor):
            return
        for w in (editor, *editor.findChildren(QWidget)):
            w.removeEventFilter(self)
        if isinstance(editor, _ChoiceCombo):
            try:
                editor.activated.disconnect(self._on_combo_activated)
            except (RuntimeError, TypeError):
                pass
            if editor.popup_active:
                editor.hidePopup()
        if had_focus and self._alive():
            self._view.setFocus(Qt.FocusReason.OtherFocusReason)
        editor.hide()
        editor.deleteLater()

    # -- slots --------------------------------------------------------------------------
    def _commit_on_change(self, *_args: object) -> None:
        self.commit()

    def _on_page_changed(self, i: int) -> None:
        info = self._info
        if info is None or i != info.page:
            return
        doc = self._document
        size = doc.page_size(i) if doc is not None and i < doc.page_count else None
        if size is None or self._page_size is None or size != self._page_size:
            # Rotation (or a vanished page): the snapshot rect is stale.
            self.commit()
        else:
            self.reposition()

    def _on_combo_activated(self, _index: int) -> None:
        editor = self._editor
        if isinstance(editor, _ChoiceCombo) and editor.popup_active:
            self.commit()

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
            if etype == QEvent.Type.Resize and self._info is not None:
                self._resize_timer.start()
            return False
        if self._info is None:
            return False
        if etype == QEvent.Type.ShortcutOverride:
            # Keep window shortcuts (e.g. Escape, Return) from stealing our keys.
            assert isinstance(event, QKeyEvent)
            if event.key() in _HANDLED_KEYS and not self._popup_open():
                event.accept()
                return True
            return False
        if etype == QEvent.Type.KeyPress:
            assert isinstance(event, QKeyEvent)
            return self._key_press(event)
        if etype == QEvent.Type.FocusOut:
            assert isinstance(event, QFocusEvent)
            self._focus_out(event)
        return False

    def _popup_open(self) -> bool:
        editor = self._editor
        return isinstance(editor, _ChoiceCombo) and editor.view().isVisible()

    def _key_press(self, event: QKeyEvent) -> bool:
        if self._popup_open():
            return False  # the combo popup handles its own keys
        key = event.key()
        mods = event.modifiers()
        if key in _ENTER_KEYS:
            if isinstance(self._editor, QPlainTextEdit) and not (
                mods & Qt.KeyboardModifier.ControlModifier
            ):
                return False  # newline
            self.commit()
            return True
        if key == Qt.Key.Key_Escape:
            self.cancel()
            return True
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            backwards = key == Qt.Key.Key_Backtab or bool(
                mods & Qt.KeyboardModifier.ShiftModifier
            )
            self.commit()
            self.navigate.emit(backwards)
            return True
        return False

    def _focus_out(self, event: QFocusEvent) -> None:
        if self._hiding or event.reason() in _IGNORED_FOCUS_REASONS:
            return
        if self._popup_open() or self._has_focus():
            return  # focus moved inside the editor (e.g. to an editable combo's line edit)
        self.commit()


__all__ = ["EDITABLE_KINDS", "FieldEditorOverlay"]
