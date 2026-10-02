"""Hover feedback of the Edit Page Text tool (M7, docs/M7_PLAN.md §1.5).

``TextHoverItem`` is a purely visual child of a ``PageItem`` (page space, z = 2): a light
frame around the line under the cursor and a stronger frame around the word. ``TextHover``
(owned by ``DocumentView.text_hover``) keeps at most one such item for the document shown
by the view: the tool calls :meth:`TextHover.hover` on mouse moves and :meth:`TextHover.clear`
when it is deactivated. Nothing is shown over invisible text (OCR layer), text drawn by a
Form XObject (``Span.in_xobject``) or where there are no characters: the core refuses
editing them anyway.

The selected run itself is not modelled here: the tool reuses ``DocumentView.text_selection``
(``TextSelection``, M6b) and turns it into a ``textedit.Run`` with :func:`selection_run`
(clamped to the span of its anchor, so that the core never sees a multi-span run).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import shiboken6
from PySide6.QtCore import QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from pdfeditor.core.pagetext import CharRef, PageText, Quad
from pdfeditor.core.textedit import Run

if TYPE_CHECKING:
    from pdfeditor.core.document import PdfDocument
    from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)

HOVER_Z = 2.0
#: Light frame of the hovered line, stronger frame (and faint fill) of the word.
LINE_PEN_COLOR = QColor(0, 120, 215, 110)
WORD_PEN_COLOR = QColor(0, 120, 215, 230)
WORD_FILL = QColor(0, 120, 215, 28)


@dataclass(frozen=True)
class HoverTarget:
    """What the cursor is over: the char ``ref`` of ``page``, its line's quad and the word
    (``None`` over a space) as a run clamped to the char's span."""

    page: int
    ref: CharRef
    line_quad: Quad
    word: Run | None
    word_quad: Quad | None


def editable(text: PageText, index: int) -> bool:
    """The char ``index`` may be edited: painted text of the page's own content."""
    return not text.span_of(index).in_xobject and not text.is_invisible(index)


def span_run(text: PageText, index: int) -> Run:
    """The chars of the span (within its line) holding the char ``index``."""
    span = text.span_of(index)
    first, last = text.line_range(index)
    a = index
    while a > first.index and text.span_of(a - 1) is span:
        a -= 1
    b = index
    while b < last.index and text.span_of(b + 1) is span:
        b += 1
    return Run(a, b)


def clamp_to_span(text: PageText, run: Run, index: int) -> Run:
    """``run`` reduced to the span holding the char ``index`` (which must be in it)."""
    span = span_run(text, index)
    return Run(max(run.first, span.first), min(run.last, span.last))


def selection_run(text: PageText, anchor: CharRef | int, focus: CharRef | int) -> Run:
    """The selection ``anchor``..``focus`` as a run inside the span of ``anchor``."""
    a = anchor.index if isinstance(anchor, CharRef) else int(anchor)
    return clamp_to_span(text, Run.from_refs(anchor, focus), a)


def hover_target(text: PageText, page: int, point: QPointF) -> HoverTarget | None:
    """The line/word frames for ``point`` (page space), or None (no editable char)."""
    if text.is_empty:
        return None
    ref = text.hit(point, 0.0)
    if ref is None or not editable(text, ref.index):
        return None
    first, last = text.line_range(ref)
    line_quads = text.range_quads(first, last)
    if not line_quads:
        return None
    word: Run | None = None
    word_quad: Quad | None = None
    if not text.char(ref).c.isspace():
        w0, w1 = text.word_range(ref)
        word = clamp_to_span(text, Run.from_refs(w0, w1), ref.index)
        quads = text.range_quads(word.first, word.last)
        word_quad = quads[0] if quads else None
    return HoverTarget(page, ref, line_quads[0], word, word_quad)


class TextHoverItem(QGraphicsItem):
    """Frames of the hovered line and word, in page space (child of a ``PageItem``).

    Cosmetic 1 px pens of width 0, so the bounding rect is exactly the line's quad bounds;
    it accepts no mouse buttons nor hover (events reach the view's tool).
    """

    def __init__(self, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        self._line: QPolygonF = QPolygonF()
        self._word: QPolygonF | None = None
        self._bounds = QRectF()
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.setZValue(HOVER_Z)

    def set_quads(self, line: Quad, word: Quad | None) -> None:
        self.prepareGeometryChange()
        self._line = line.polygon()
        self._word = word.polygon() if word is not None else None
        bounds = line.bounding_rect()
        if word is not None:
            bounds = bounds.united(word.bounding_rect())
        self._bounds = bounds
        self.update()

    def line_polygon(self) -> QPolygonF:
        return QPolygonF(self._line)

    def word_polygon(self) -> QPolygonF | None:
        return None if self._word is None else QPolygonF(self._word)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(self._bounds)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        painter.save()
        pen = QPen(LINE_PEN_COLOR, 0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(self._line)
        if self._word is not None:
            pen = QPen(WORD_PEN_COLOR, 0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(QBrush(WORD_FILL))
            painter.drawPolygon(self._word)
        painter.restore()


class TextHover(QObject):
    """At most one :class:`TextHoverItem` over the document shown by ``view``.

    Cleared on any change that may move or replace the text or the ``PageItem`` (its
    page's ``page_changed``, ``pages_remapped``, ``structure_changed``, ``reloaded``, a
    document switch); the next mouse move shows it again.
    """

    def __init__(self, view: PageView, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._document: PdfDocument | None = None
        self._item: TextHoverItem | None = None
        self._target: HoverTarget | None = None

    @property
    def target(self) -> HoverTarget | None:
        return self._target

    @property
    def item(self) -> TextHoverItem | None:
        """The live item while something is hovered (tests)."""
        return self._item if self._item_alive() else None

    def set_document(self, document: PdfDocument | None) -> None:
        if document is self._document:
            return
        self.clear()
        old = self._document
        if old is not None:
            for signal, slot in (
                (old.page_changed, self._on_page_changed),
                (old.pages_remapped, self.clear),
                (old.structure_changed, self.clear),
                (old.reloaded, self.clear),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._document = document
        if document is not None:
            document.page_changed.connect(self._on_page_changed)
            document.pages_remapped.connect(self.clear)
            document.structure_changed.connect(self.clear)
            document.reloaded.connect(self.clear)

    def hover(self, page: int | None, point: QPointF | None) -> HoverTarget | None:
        """Show the frames for ``point`` of ``page`` (page space); clear them when there
        is no editable char there. Returns the target shown."""
        doc = self._document
        if (
            doc is None
            or page is None
            or point is None
            or not doc.is_open
            or not 0 <= page < min(doc.page_count, self._view.page_count)
        ):
            self.clear()
            return None
        target = hover_target(doc.page_text(page), page, point)
        if target is None:
            self.clear()
            return None
        if target == self._target and self._item_alive():
            return target
        item = self._item if self._item_alive() else None
        parent = self._view.page_item(page)
        if item is None or item.parentItem() is not parent:
            self._remove_item()
            item = TextHoverItem(parent)
            self._item = item
        item.set_quads(target.line_quad, target.word_quad)
        item.show()
        self._target = target
        return target

    def clear(self, *_args: object) -> None:
        self._target = None
        self._remove_item()

    # -- internals -----------------------------------------------------------------------
    def _item_alive(self) -> bool:
        return self._item is not None and shiboken6.isValid(self._item)

    def _remove_item(self) -> None:
        item, self._item = self._item, None
        if item is None or not shiboken6.isValid(item):
            return
        scene = item.scene()
        if scene is not None:
            scene.removeItem(item)
        else:
            item.setParentItem(None)

    def _on_page_changed(self, page: int) -> None:
        if self._target is not None and self._target.page == page:
            self.clear()


__all__ = [
    "HOVER_Z",
    "HoverTarget",
    "TextHover",
    "TextHoverItem",
    "clamp_to_span",
    "editable",
    "hover_target",
    "selection_run",
    "span_run",
]
