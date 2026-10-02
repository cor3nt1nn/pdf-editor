"""Form tool: fill AcroForm fields (M2).

Clicking a checkbox or radio button toggles it; clicking a text, combo or list field
opens the document view's floating editor (``DocumentView.field_editor``). Clicks
outside fields fall through to the view's default panning. Tab/Shift+Tab walk the
editable fields in geometric reading order (``core.forms.tab_order``) across pages.
The tool never mutates the document: every change is a ``SetFieldValueCommand``.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QKeyEvent, QPainter, QPen

from pdfeditor.core.commands import SetFieldValueCommand
from pdfeditor.core.document import DocumentError, PdfDocument
from pdfeditor.core.forms import (
    FF_NO_TOGGLE_TO_OFF,
    RECT_TOLERANCE,
    FieldKind,
    WidgetInfo,
    tab_order,
    text_fits,
)
from pdfeditor.ui.overlays.field_editor import EDITABLE_KINDS
from pdfeditor.ui.tools.base import Tool, ToolEvent

if TYPE_CHECKING:
    from pdfeditor.core.settings import Settings
    from pdfeditor.ui.document_view import DocumentView
    from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)

BUTTON_KINDS = (FieldKind.CHECKBOX, FieldKind.RADIO)
# Margin (viewport pixels) kept around a field scrolled into view.
SCROLL_MARGIN_PX = 40
FOCUS_PEN = QColor(0, 120, 215)


def _same_widget(a: WidgetInfo, b: WidgetInfo) -> bool:
    """Same widget across saves: page, name and rect decide (full saves renumber xrefs,
    so an old xref may name another widget, even a same-name sibling)."""
    return (
        a.page == b.page
        and a.name == b.name
        and all(
            abs(x - y) <= RECT_TOLERANCE
            for x, y in zip(a.unrotated_rect, b.unrotated_rect, strict=True)
        )
    )


class FormTool(Tool):
    """Fill form fields of ``document_view``'s document.

    ``message(str)`` carries translated, non-blocking notices for the status bar
    (characters the form's font cannot show, a field that could not be updated).
    """

    name = "form"
    message = Signal(str)

    def __init__(
        self,
        document_view: DocumentView,
        settings: Settings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.document_view = document_view
        self.settings = settings
        self.editor = document_view.field_editor
        # Last field that had the focus (editor or keyboard focus on a button).
        self._last: WidgetInfo | None = None
        # Checkbox/radio showing the keyboard focus frame (Space toggles it).
        self._focused_button: WidgetInfo | None = None
        # /DA font size of single-line text fields before auto-shrink set it to 0
        # (field name -> size), restored when a later value fits again.
        self._shrunk_from: dict[str, float] = {}
        self._document: PdfDocument | None = None
        self.editor.committed.connect(self._on_committed)
        self.editor.navigate.connect(self._on_navigate)
        document_view.document_changed.connect(self._on_document_changed)
        self._on_document_changed()

    # -- state ----------------------------------------------------------------
    @property
    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.OpenHandCursor)

    @property
    def document(self) -> PdfDocument | None:
        return self.document_view.document

    @property
    def focused_button(self) -> WidgetInfo | None:
        return self._focused_button

    def activate(self, view: PageView) -> None:
        super().activate(view)
        self._reset()

    def deactivate(self) -> None:
        self.commit_pending()
        self._reset()
        super().deactivate()

    def commit_pending(self) -> None:
        """Commit the open editor, if any."""
        self.editor.commit()

    def _reset(self) -> None:
        self._last = None
        self._set_button_focus(None)

    def _on_document_changed(self) -> None:
        old, new = self._document, self.document
        if old is not None:
            for signal, slot in (
                (old.reloaded, self._on_reloaded),
                (old.pages_remapped, self._on_pages_remapped),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._document = new
        if new is not None:
            new.reloaded.connect(self._on_reloaded)
            new.pages_remapped.connect(self._on_pages_remapped)
        self._shrunk_from.clear()
        self._reset()

    def _on_reloaded(self) -> None:
        """A save swapped the document (full saves renumber xrefs): re-resolve our
        snapshots so that Space/Tab act on the same widgets."""
        if self._last is not None:
            self._last = self._fresh(self._last) or self._last
        if self._focused_button is not None:
            self._set_button_focus(self._fresh(self._focused_button))

    def _on_pages_remapped(self, mapping: object) -> None:
        """Pages were inserted, deleted or moved: follow the focused button and the last
        focused field to their new page (dropped when their page is gone), so that
        Space/Tab never act on a field of another page (docs/ARCHITECTURE.md
        Deviation 101)."""
        old_to_new = list(mapping)  # type: ignore[call-overload]

        def moved(info: WidgetInfo | None) -> WidgetInfo | None:
            if info is None or not 0 <= info.page < len(old_to_new):
                return None
            page = old_to_new[info.page]
            if page is None:
                return None
            return replace(info, page=page)

        last = moved(self._last)
        self._last = (self._fresh(last) or last) if last is not None else None
        button = moved(self._focused_button)
        self._set_button_focus(self._fresh(button) if button is not None else None)

    # -- hit testing ------------------------------------------------------------
    def widget_at(self, page: int | None, pos: QPointF | None) -> WidgetInfo | None:
        """The editable widget under ``pos`` on ``page`` (the last one in /Annots order)."""
        doc = self.document
        if doc is None or page is None or pos is None or not doc.is_open:
            return None
        found = None
        try:
            widgets = doc.widgets(page)
        except (DocumentError, IndexError):
            return None
        for info in widgets:
            if info.editable and info.rect.contains(pos):
                found = info
        return found

    # -- mouse -------------------------------------------------------------------
    def mouse_move(self, event: ToolEvent) -> bool:
        view = self.view
        if view is None or event.buttons != Qt.MouseButton.NoButton:
            return False
        over = self.widget_at(event.page_index, event.page_pos) is not None
        shape = Qt.CursorShape.PointingHandCursor if over else self.cursor.shape()
        if view.viewport().cursor().shape() != shape:
            view.viewport().setCursor(QCursor(shape))
        return False  # the view keeps its default behaviour

    def mouse_press(self, event: ToolEvent) -> bool:
        if not event.buttons & Qt.MouseButton.LeftButton:
            return False
        info = self.widget_at(event.page_index, event.page_pos)
        # Commit first: the pending value may belong to the clicked field (another
        # widget of it), whose snapshot is then stale.
        self.commit_pending()
        if info is None:
            self._set_button_focus(None)
            return False  # ScrollHandDrag pans
        info = self._fresh(info) or info
        if info.kind in BUTTON_KINDS:
            self.toggle(info)
            self._last = info
            self._set_button_focus(info)
            return True
        if info.kind in EDITABLE_KINDS:
            self.focus(info)
            return True
        return False

    # -- keyboard ------------------------------------------------------------------
    def key_press(self, event: ToolEvent) -> bool:
        qt_event = event.qt_event
        if not isinstance(qt_event, QKeyEvent) or self.editor.is_open:
            return False
        key = qt_event.key()
        mods = qt_event.modifiers()
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            if mods & ~(Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier):
                return False
            backwards = key == Qt.Key.Key_Backtab or bool(mods & Qt.KeyboardModifier.ShiftModifier)
            if self._focused_button is not None:
                self._on_navigate(backwards)
            else:
                self._focus_from_current_page(backwards)
            return True
        button = self._focused_button
        if button is not None and key == Qt.Key.Key_Space:
            fresh = self._fresh(button)
            if fresh is not None:
                self.toggle(fresh)
            return True
        if button is not None and key == Qt.Key.Key_Escape:
            self._set_button_focus(None)
            return True
        return False

    def _focus_from_current_page(self, backwards: bool) -> None:
        """Tab with nothing focused: first (Shift+Tab: last) field of the current page."""
        order = self._tab_order()
        if not order or self.view is None:
            return
        current = max(0, self.view.current_page)
        if backwards:
            target = next((w for w in reversed(order) if w.page <= current), order[-1])
        else:
            target = next((w for w in order if w.page >= current), order[0])
        self.focus(target)

    # -- focus and navigation ---------------------------------------------------------
    def focus(self, info: WidgetInfo) -> None:
        """Scroll ``info`` into view, then open its editor (or focus the button)."""
        view = self.view
        if view is None or not 0 <= info.page < view.page_count:
            return
        # Commit the pending edit, then re-read the field: the edit may have changed it
        # (e.g. ``info`` is another widget of the field being edited).
        self.commit_pending()
        info = self._fresh(info) or info
        self._ensure_visible(view, info)
        self._last = info
        if info.kind in EDITABLE_KINDS:
            self._set_button_focus(None)
            self.editor.open(info)
        else:
            self._set_button_focus(info)
            view.setFocus(Qt.FocusReason.TabFocusReason)

    @staticmethod
    def _ensure_visible(view: PageView, info: WidgetInfo) -> None:
        if info.page != view.current_page:
            view.scroll_to_page(info.page)  # emits current_page_changed
        scene_rect = view.page_item(info.page).mapRectToScene(info.rect)
        view.ensureVisible(scene_rect, SCROLL_MARGIN_PX, SCROLL_MARGIN_PX)

    def _tab_order(self) -> list[WidgetInfo]:
        doc = self.document
        if doc is None or not doc.is_open:
            return []
        return tab_order(doc.all_widgets())

    def _on_navigate(self, backwards: bool) -> None:
        # The editor is already closed here (current_info is None): use our own record.
        last = self._last
        order = self._tab_order()
        if self.view is None or not order:
            return
        if last is None:
            self._focus_from_current_page(backwards)
            return
        index = next((i for i, w in enumerate(order) if _same_widget(w, last)), None)
        if index is None:
            # The field vanished: continue from its position in reading order.
            key = (last.page, last.rect.top())
            after = next(
                (i for i, w in enumerate(order) if (w.page, w.rect.top()) >= key), len(order)
            )
            index = after if backwards else after - 1
        step = -1 if backwards else 1
        self.focus(order[(index + step) % len(order)])

    def _fresh(self, info: WidgetInfo) -> WidgetInfo | None:
        doc = self.document
        if doc is None or not doc.is_open:
            return None
        try:
            return next((w for w in doc.widgets(info.page) if _same_widget(w, info)), None)
        except (DocumentError, IndexError):
            return None

    def _set_button_focus(self, info: WidgetInfo | None) -> None:
        if info is self._focused_button:
            return
        self._focused_button = info
        if self.view is not None:
            self.view.viewport().update()

    def paint_overlay(self, painter: QPainter) -> None:
        info = self._focused_button
        view = self.view
        if info is None or view is None or not 0 <= info.page < view.page_count:
            return
        rect = view.page_item(info.page).mapRectToScene(info.rect)
        pad = 2.0 / max(view.view_scale, 1e-6)
        pen = QPen(FOCUS_PEN, 0, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(-pad, -pad, pad, pad))
        painter.restore()

    # -- edits ---------------------------------------------------------------------------
    def toggle(self, info: WidgetInfo) -> None:
        """Click on a checkbox/radio: checkbox flips; radio selects (or turns off)."""
        doc = self.document
        if doc is None:
            return
        if info.kind is FieldKind.CHECKBOX:
            new_value = not info.is_on
        elif info.kind is FieldKind.RADIO:
            if info.is_on:
                if info.flags & FF_NO_TOGGLE_TO_OFF:
                    return
                new_value = False
            else:
                new_value = True
        else:
            return
        self._push(doc, info, new_value, None)

    def _on_committed(self, info: WidgetInfo, value: object) -> None:
        doc = self.document
        if doc is None or not doc.is_open or self.editor.document is not doc:
            # Emitted while switching documents: the edit belongs to a closing document.
            log.info("dropping an edit of %r: its document is gone", info.name)
            return
        text = str(value)
        font_size = self._auto_font_size(doc, info, text)
        try:
            text.encode("cp1252")
        except UnicodeEncodeError:
            self.message.emit(
                QCoreApplication.translate(
                    "FormTool",
                    "Some characters cannot be displayed with this form’s font; they are stored but may not print.",  # noqa: E501
                )
            )
        self._push(doc, info, text, font_size)

    def _auto_font_size(self, doc: PdfDocument, info: WidgetInfo, text: str) -> float | None:
        """Font size to write with ``text`` (``None``: keep the field's).

        Auto-shrink: a single-line value that does not fit at the field's size is written
        with size 0 (auto); the original size is remembered and restored when a later
        value fits in it again.
        """
        if info.kind is not FieldKind.TEXT or info.multiline or not self.settings.auto_shrink_text:
            return None
        original = self._shrunk_from.get(info.name, info.font_size)
        if original <= 0:
            return None  # authored auto size: leave it to MuPDF
        x0, _y0, x1, _y1 = info.unrotated_rect
        with doc.lock:
            fits = text_fits(text, original, x1 - x0)
        if not fits:
            if info.font_size > 0:
                self._shrunk_from[info.name] = info.font_size
                return 0
            return None  # already auto-sized
        if info.font_size == 0 and info.name in self._shrunk_from:
            return original
        return None

    def _push(
        self, doc: PdfDocument, info: WidgetInfo, value: str | bool, font_size: float | None
    ) -> None:
        """Apply the change now (reporting a vanished field) and push it for undo."""
        self.commit_pending()  # no-op from ``committed`` (the editor is already closed)
        try:
            command = SetFieldValueCommand(doc, info, value, font_size)
            command.apply_now()
        except (DocumentError, IndexError) as exc:
            log.warning("cannot set form field %r: %s", info.name, exc)
            self.message.emit(
                QCoreApplication.translate("FormTool", "The form field could not be updated.")
            )
            return
        self.document_view.push(command)


__all__ = ["FormTool"]
