"""Text selection and text markup tools (M6b, docs/M6_PLAN.md D11).

``TextSelectTool`` (Select Text, Shift+T) selects page text into
``DocumentView.text_selection``: a press on a character anchors the range and dragging
extends it in reading (content) order; past the text the nearest character of the nearest
line is taken, so a drag may start in the margin or leave the page. A double-click
selects a word, a triple-click a line; Shift+click or Shift+drag extends the current
selection; Escape clears it. Edit ▸ Copy Text (Ctrl+C, ``MainWindow.copy_text``) copies it.

``MarkupTool(kind)`` (Highlight Shift+H, Underline Shift+U, Strike Through Shift+S)
selects text the same way and, when the mouse button is released over a non-empty
selection, pushes **one** ``AddAnnotCommand`` of its kind over the selected line quads in
its default colour (``Settings.markup_color``), selects the new markup and clears the
text selection. A double-click marks the word, a triple-click the line (the word is
marked once the double-click interval has passed without a third click). Like the other
annotation tools it selects existing annotations with a click (a markup is never moved;
dragging from one selects text instead), deletes the selection with Delete and recolours
it with the toolbar colour button. A page without any text says so in the status bar
("No selectable text here (scanned page?).").

Invisible text (render mode 3, OCR) is selected, marked and copied like any other text.
Neither tool mutates the document: markups are commands pushed through
``DocumentView.push`` (via ``AnnotToolBase.add``).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QKeyEvent, QPainter
from PySide6.QtWidgets import QApplication

from pdfeditor.core.annotations import AnnotInfo, AnnotKind, Color, markup_spec
from pdfeditor.core.document import DocumentError, PdfDocument
from pdfeditor.core.pagetext import HIT_TOLERANCE, CharRef, PageText
from pdfeditor.ui.tools.annot_tools import AnnotToolBase, DragMode
from pdfeditor.ui.tools.base import Tool, ToolEvent, event_button, viewport_pos

if TYPE_CHECKING:
    from pdfeditor.core.settings import Settings
    from pdfeditor.ui.document_view import DocumentView
    from pdfeditor.ui.overlays.text_selection import TextSelection
    from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)

#: Markup kinds the tools create (Squiggly is only read, recoloured and deleted).
TOOL_KINDS = (AnnotKind.HIGHLIGHT, AnnotKind.UNDERLINE, AnnotKind.STRIKEOUT)

#: Latin ligature code points (U+FB00–U+FB06) → their letters, for copied text only (the
#: page text model keeps one char per glyph: M7 needs it).
_LIGATURES = str.maketrans(
    {
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "ﬅ": "st",  # long s + t
        "ﬆ": "st",
    }
)


def no_text_message() -> str:
    return QCoreApplication.translate("MarkupTools", "No selectable text here (scanned page?).")


def markup_text(document: PdfDocument, info: AnnotInfo) -> str:
    """The page text under the markup ``info``: the characters whose box centre lies in
    one of its quads, in content order (newline between lines)."""
    try:
        pt = document.page_text(info.page)
    except (DocumentError, IndexError):
        return ""
    refs: set[CharRef] = set()
    for quad in info.quads:
        refs.update(pt.chars_in_rect(quad))
    return pt.text_of(refs)


def expand_ligatures(text: str) -> str:
    """``text`` with the Latin ligature code points (U+FB00–U+FB06) spelt out as letters."""
    return text.translate(_LIGATURES)


def selected_text(document_view: DocumentView) -> str:
    """What Edit ▸ Copy Text copies: the text selection, else the text under the
    selected markup ("" when there is neither), ligatures spelt out
    (:func:`expand_ligatures`)."""
    sel = document_view.text_selection
    if not sel.is_empty:
        return expand_ligatures(sel.text())
    info = document_view.annot_selection.current
    doc = document_view.document
    if info is None or not info.is_markup or doc is None or not doc.is_open:
        return ""
    return expand_ligatures(markup_text(doc, info))


@dataclass
class _TextDrag:
    """A left press that selects text: the range starts at ``anchor`` once the pointer
    moved (``active``); ``anchor`` is None until a character is known."""

    page: int
    start: QPointF  # page space
    start_px: QPoint  # viewport
    anchor: CharRef | None
    active: bool = False


class _TextSelecting:
    """Text selection by mouse, shared by :class:`TextSelectTool` and
    :class:`MarkupTool` (mixed in before their ``Tool`` base). Subclasses provide
    ``view``, ``document_view`` and ``message``; :meth:`selection_done` is called when a
    press/drag/double-click/triple-click finished a non-empty selection."""

    view: PageView | None
    document_view: DocumentView
    message: Signal

    def _init_text(self) -> None:
        self._text_drag: _TextDrag | None = None
        # (time, viewport position, page) of the last double-click: a press right after
        # it at the same spot is a triple-click.
        self._last_double: tuple[float, QPoint, int] | None = None

    @property
    def text_selection(self) -> TextSelection:
        return self.document_view.text_selection

    def _text_doc(self) -> PdfDocument | None:
        doc = self.document_view.document
        return doc if doc is not None and doc.is_open else None

    def _page_text(self, page: int) -> PageText | None:
        doc = self._text_doc()
        if doc is None:
            return None
        try:
            return doc.page_text(page)
        except (DocumentError, IndexError):
            return None

    def _drag_pos(self, page: int, event: ToolEvent) -> QPointF:
        """``event`` in the page space of ``page`` (even outside that page)."""
        assert self.view is not None
        return self.view.page_item(page).mapFromScene(event.scene_pos)

    # -- press / move / release ------------------------------------------------------
    def text_press(self, event: ToolEvent) -> bool:
        """A left press on a page: start a text selection (or extend it with Shift, or
        select the line on a triple-click)."""
        page, pos = event.page_index, event.page_pos
        sel = self.text_selection
        if page is None or pos is None:
            sel.clear()
            return False  # outside the pages: the view pans
        pt = self._page_text(page)
        if pt is None:
            return True
        if pt.is_empty:
            sel.clear()
            self.message.emit(no_text_message())
            return True
        px = viewport_pos(event)
        if self._is_triple(page, px):
            self._last_double = None
            ref = pt.hit(pos, math.inf)
            if ref is not None:
                first, last = pt.line_range(ref)
                sel.set(page, first, last)
                self.selection_done()
            return True
        shift = bool(event.modifiers & Qt.KeyboardModifier.ShiftModifier)
        anchor = sel.anchor if shift and sel.page == page else None
        if anchor is not None:
            focus = pt.hit(pos, math.inf)
            if focus is not None:
                sel.set(page, anchor, focus)
            self._text_drag = _TextDrag(page, pos, px, anchor, active=True)
            return True
        sel.clear()
        self._text_drag = _TextDrag(page, pos, px, pt.hit(pos, HIT_TOLERANCE))
        return True

    def text_move(self, event: ToolEvent) -> bool:
        drag = self._text_drag
        if drag is None:
            return False
        if self.view is None or not 0 <= drag.page < self.view.page_count:
            self._text_drag = None
            return True
        pt = self._page_text(drag.page)
        if pt is None or pt.is_empty:
            return True
        pos = self._drag_pos(drag.page, event)
        if not drag.active:
            moved = viewport_pos(event) - drag.start_px
            if moved.manhattanLength() < QApplication.startDragDistance():
                return True
            if drag.anchor is None:
                # Started away from the text: select once the drag reaches some text.
                area = QRectF(drag.start, pos).normalized()
                if pt.hit(pos, HIT_TOLERANCE) is None and not pt.chars_in_rect(area):
                    return True
                drag.anchor = pt.hit(drag.start, math.inf)
                if drag.anchor is None:
                    return True
            drag.active = True
        focus = pt.hit(pos, math.inf)
        if focus is not None and drag.anchor is not None:
            try:
                self.text_selection.set(drag.page, drag.anchor, focus)
            except IndexError:  # the page text changed under the drag
                self._text_drag = None
        return True

    def text_release(self, event: ToolEvent) -> bool:
        drag = self._text_drag
        if drag is None:
            return False
        if event_button(event) != Qt.MouseButton.LeftButton:
            return True
        self._text_drag = None
        if drag.active and not self.text_selection.is_empty:
            self.selection_done()
        elif self._dragged(drag, event):
            self.no_text_dragged()
        return True

    def text_double_click(self, event: ToolEvent) -> bool:
        """Select the word under the pointer."""
        self._text_drag = None
        page, pos = event.page_index, event.page_pos
        if page is None or pos is None:
            return False
        pt = self._page_text(page)
        if pt is None or pt.is_empty:
            return True
        self._last_double = (time.monotonic(), viewport_pos(event), page)
        word = pt.word_at(pos)
        if word is None:
            return True
        self.text_selection.set(page, *word)
        self.word_selected()
        return True

    def text_escape(self) -> bool:
        """Escape: cancel a drag and clear the selection (False when there was none)."""
        had = self._text_drag is not None or not self.text_selection.is_empty
        self._text_drag = None
        self.text_selection.clear()
        return had

    def _dragged(self, drag: _TextDrag, event: ToolEvent) -> bool:
        moved = viewport_pos(event) - drag.start_px
        return moved.manhattanLength() >= QApplication.startDragDistance()

    def _is_triple(self, page: int, px: QPoint) -> bool:
        last = self._last_double
        if last is None:
            return False
        when, where, last_page = last
        interval = QApplication.doubleClickInterval() / 1000.0
        near = (px - where).manhattanLength() <= 2 * QApplication.startDragDistance()
        return last_page == page and near and time.monotonic() - when <= interval

    # -- hooks -------------------------------------------------------------------------
    def selection_done(self) -> None:
        """A drag, Shift+click or triple-click finished a non-empty selection."""

    def word_selected(self) -> None:
        """A double-click selected a word."""
        self.selection_done()

    def no_text_dragged(self) -> None:
        """A drag covered no character."""


class TextSelectTool(_TextSelecting, Tool):
    """Select page text (copied with Edit ▸ Copy Text)."""

    name = "select_text"
    message = Signal(str)

    def __init__(self, document_view: DocumentView, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.document_view = document_view
        self._init_text()
        document_view.document_changed.connect(self._reset)

    @property
    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.IBeamCursor)

    def activate(self, view: PageView) -> None:
        super().activate(view)
        self.document_view.annot_selection.clear()

    def deactivate(self) -> None:
        self._reset()
        self.text_selection.clear()
        super().deactivate()

    def _reset(self) -> None:
        self._text_drag = None
        self._last_double = None

    def mouse_press(self, event: ToolEvent) -> bool:
        if event_button(event) != Qt.MouseButton.LeftButton:
            return self._text_drag is not None
        self.document_view.commit_pending_edits()
        self.document_view.annot_selection.clear()
        return self.text_press(event)

    def mouse_move(self, event: ToolEvent) -> bool:
        return self.text_move(event)

    def mouse_release(self, event: ToolEvent) -> bool:
        return self.text_release(event)

    def mouse_double_click(self, event: ToolEvent) -> bool:
        if event_button(event) != Qt.MouseButton.LeftButton:
            return self._text_drag is not None
        return self.text_double_click(event)

    def key_press(self, event: ToolEvent) -> bool:
        qt_event = event.qt_event
        if isinstance(qt_event, QKeyEvent) and qt_event.key() == Qt.Key.Key_Escape:
            return self.text_escape()
        return False

    def paint_overlay(self, painter: QPainter) -> None:
        self.text_selection.paint(painter)


class MarkupTool(_TextSelecting, AnnotToolBase):
    """Create text markups of ``kind`` (highlight, underline or strike-out) over the
    text selected with the mouse; select, recolour and delete existing annotations."""

    def __init__(
        self,
        document_view: DocumentView,
        settings: Settings,
        kind: AnnotKind,
        parent: QObject | None = None,
    ) -> None:
        if kind not in TOOL_KINDS:
            raise ValueError(f"no markup tool for {kind!r}")
        super().__init__(document_view, settings, parent)
        self.kind = kind
        self.name = kind.value
        self._init_text()
        # A double-click marks its word once the double-click interval passed without a
        # third click (which marks the line instead).
        self._word_timer = QTimer(self)
        self._word_timer.setSingleShot(True)
        self._word_timer.timeout.connect(self.commit_selection)

    @property
    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.IBeamCursor)

    # -- style -------------------------------------------------------------------------
    def markup_color(self) -> Color:
        """Colour of new markups of this tool's kind (``Settings.markup_color``)."""
        color = QColor(self.settings.markup_color(self.kind.value))
        return (color.redF(), color.greenF(), color.blueF())

    def store_defaults(self, font_size: float | None, color: Color | None) -> None:
        """Without a selection the colour button sets this tool's markup colour (the font
        size is inert); with a text box or stamp selected it styles that as usual."""
        if self.selection.current is not None:
            super().store_defaults(font_size, color)
            return
        if color is not None:
            self.settings.set_markup_color(self.kind.value, QColor.fromRgbF(*color).name())

    # -- state ---------------------------------------------------------------------------
    def deactivate(self) -> None:
        self.flush_word()
        self._text_drag = None
        self._last_double = None
        self.text_selection.clear()
        super().deactivate()

    def _reset(self) -> None:
        if hasattr(self, "_word_timer"):
            self._word_timer.stop()
            self._text_drag = None
            self._last_double = None
        super()._reset()

    def flush_word(self) -> None:
        """Mark a double-clicked word now (instead of after the double-click interval)."""
        if self._word_timer.isActive():
            self._word_timer.stop()
            self.commit_selection()

    def flush_pending(self) -> None:
        """A pending double-clicked word is marked before a save, close, export or push."""
        self.flush_word()

    # -- mouse ---------------------------------------------------------------------------
    def mouse_press(self, event: ToolEvent) -> bool:
        if event_button(event) != Qt.MouseButton.LeftButton:
            return self._drag is not None or self._text_drag is not None
        doc = self._doc()
        if doc is None or self.view is None:
            return False
        triple = (
            event.page_index is not None
            and self._word_timer.isActive()
            and self._is_triple(event.page_index, viewport_pos(event))
        )
        if triple:
            self._word_timer.stop()  # before commit_pending_edits would mark the word
        # A click soon after a double-click: the word is marked first (flush_pending).
        self.document_view.commit_pending_edits()
        if not triple:
            self._last_double = None
        shift = bool(event.modifiers & Qt.KeyboardModifier.ShiftModifier)
        if not triple and not shift:
            info = self.annot_at(event.page_index, event.page_pos)
            if info is not None:
                return super().mouse_press(event)  # select (and maybe move) it
        self.selection.clear()
        self._armed = None
        self._set_preview(None)
        return self.text_press(event)

    def mouse_move(self, event: ToolEvent) -> bool:
        if self._text_drag is not None:
            return self.text_move(event)
        drag = self._drag
        if (
            drag is not None
            and drag.mode is DragMode.PENDING
            and not drag.info.movable
            and self.view is not None
        ):
            moved = viewport_pos(event) - drag.start_px
            if moved.manhattanLength() >= QApplication.startDragDistance():
                # Dragging from a markup selects the text under it (markups never move).
                self._drag = None
                self.selection.clear()
                self._armed = None
                page = drag.info.page
                pt = self._page_text(page)
                if pt is not None and not pt.is_empty:
                    self._text_drag = _TextDrag(
                        page, drag.start, drag.start_px, pt.hit(drag.start, HIT_TOLERANCE)
                    )
                    return self.text_move(event)
                return True
        return super().mouse_move(event)

    def mouse_release(self, event: ToolEvent) -> bool:
        if self._text_drag is not None:
            return self.text_release(event)
        return super().mouse_release(event)

    def mouse_double_click(self, event: ToolEvent) -> bool:
        if event_button(event) != Qt.MouseButton.LeftButton:
            return self._drag is not None or self._text_drag is not None
        info = self.annot_at(event.page_index, event.page_pos)
        if info is not None and not (info.is_markup and not info.movable):
            self._text_drag = None
            return super().mouse_double_click(event)
        # Text already marked: the double-click marks its word (a markup is selected by
        # a single click; a third click then marks the line).
        self._drag = None
        if info is not None:
            self.selection.clear()
            self._armed = None
            self._set_preview(None)
        return self.text_double_click(event)

    def key_press(self, event: ToolEvent) -> bool:
        qt_event = event.qt_event
        if (
            isinstance(qt_event, QKeyEvent)
            and qt_event.key() == Qt.Key.Key_Escape
            and not self.editor.is_open
            and (self._text_drag is not None or not self.text_selection.is_empty)
        ):
            self._word_timer.stop()
            return self.text_escape()
        return super().key_press(event)

    def paint_overlay(self, painter: QPainter) -> None:
        self.text_selection.paint(painter)
        super().paint_overlay(painter)

    # -- markups -------------------------------------------------------------------------
    def selection_done(self) -> None:
        self.commit_selection()

    def word_selected(self) -> None:
        self._word_timer.start(QApplication.doubleClickInterval())

    def no_text_dragged(self) -> None:
        self.message.emit(no_text_message())

    def commit_selection(self) -> AnnotInfo | None:
        """Mark the selected text (one undo step), select the new markup and clear the
        text selection."""
        sel = self.text_selection
        page, quads = sel.page, sel.quads()
        if page is None or not quads:
            return None
        doc = self._doc()
        if doc is None or not doc.can_annotate:
            return None
        sel.clear()
        return self.add(markup_spec(page, self.kind, quads, self.markup_color()))

    # -- AnnotToolBase hooks -------------------------------------------------------------
    def create_at(self, page: int, pos: QPointF, alt: bool) -> None:
        """Never called: a press on empty space selects text."""


__all__ = [
    "TOOL_KINDS",
    "MarkupTool",
    "TextSelectTool",
    "expand_ligatures",
    "markup_text",
    "no_text_message",
    "selected_text",
]
