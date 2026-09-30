"""Selection overlay of a FreeText annotation: frame, 8 resize handles, drag ghost (M3).

``AnnotHandleItem`` is a purely visual child of a ``PageItem`` (page space, points);
the annotation tools hit-test its handles with :meth:`AnnotHandleItem.handle_at`.
``AnnotSelection`` keeps the selected annotation (by ``(page, name)``) and its item in
sync with the document.
"""

from __future__ import annotations

import logging
from enum import IntEnum
from typing import TYPE_CHECKING

import shiboken6
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QTransform
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from pdfeditor.core.annotations import AnnotInfo

if TYPE_CHECKING:
    from pdfeditor.core.document import PdfDocument
    from pdfeditor.ui.page_view import PageView

log = logging.getLogger(__name__)

SELECTION_COLOR = QColor(0, 120, 215)
GHOST_COLOR = QColor(0, 120, 215, 200)
GHOST_FILL = QColor(0, 120, 215, 30)
#: Handle side in device pixels, whatever the zoom.
HANDLE_PX = 7
#: Extra area (points) around the rects in ``boundingRect`` (handles are drawn at a fixed
#: device size and stick out of the frame; 3.5 px ≤ 10.5 pt at the 25 % minimum zoom).
BOUNDS_MARGIN = 20.0
ANNOT_HANDLE_Z = 2.0


class Handle(IntEnum):
    """The 8 resize handles, clockwise from the top-left corner."""

    TOP_LEFT = 0
    TOP = 1
    TOP_RIGHT = 2
    RIGHT = 3
    BOTTOM_RIGHT = 4
    BOTTOM = 5
    BOTTOM_LEFT = 6
    LEFT = 7


def handle_points(rect: QRectF) -> dict[Handle, QPointF]:
    """Centre of every handle of ``rect``."""
    left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
    cx, cy = rect.center().x(), rect.center().y()
    return {
        Handle.TOP_LEFT: QPointF(left, top),
        Handle.TOP: QPointF(cx, top),
        Handle.TOP_RIGHT: QPointF(right, top),
        Handle.RIGHT: QPointF(right, cy),
        Handle.BOTTOM_RIGHT: QPointF(right, bottom),
        Handle.BOTTOM: QPointF(cx, bottom),
        Handle.BOTTOM_LEFT: QPointF(left, bottom),
        Handle.LEFT: QPointF(left, cy),
    }


class AnnotHandleItem(QGraphicsItem):
    """Selection frame + 8 handles around ``rect`` and an optional dashed ghost.

    Child of a ``PageItem``, in page space. Accepts no mouse buttons nor hover (the
    active tool handles the mouse); z = 2, above the field highlights. The frame and
    ghost use cosmetic pens; handles are ``HANDLE_PX`` device pixels at any zoom.
    """

    def __init__(self, rect: QRectF, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        self._rect = QRectF(rect)
        self._ghost: QRectF | None = None
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.setZValue(ANNOT_HANDLE_Z)

    @property
    def rect(self) -> QRectF:
        return QRectF(self._rect)

    @property
    def ghost(self) -> QRectF | None:
        return None if self._ghost is None else QRectF(self._ghost)

    def set_rect(self, rect: QRectF) -> None:
        if rect != self._rect:
            self.prepareGeometryChange()
            self._rect = QRectF(rect)
        self.update()

    def set_ghost(self, rect: QRectF | None) -> None:
        """Dashed preview of a move/resize in progress (None removes it)."""
        if rect == self._ghost:
            return
        self.prepareGeometryChange()
        self._ghost = None if rect is None else QRectF(rect)
        self.update()

    def handle_at(self, page_pos: QPointF, tol_pt: float) -> Handle | None:
        """The handle nearest ``page_pos`` within ``tol_pt`` (both axes), or None.

        The tools pass ``6 px / view_scale`` so the tolerance is constant on screen.
        Inside the rect the tolerance is capped at a quarter of the rect's width
        (horizontally) and height (vertically): the body of a box that is small on
        screen (zoomed out) stays movable, while its corners and edges still resize.
        """
        tol_x = tol_y = tol_pt
        if self._rect.contains(page_pos):
            tol_x = min(tol_pt, self._rect.width() / 4)
            tol_y = min(tol_pt, self._rect.height() / 4)
        best: Handle | None = None
        best_dist = float("inf")
        for handle, centre in handle_points(self._rect).items():
            dx, dy = abs(page_pos.x() - centre.x()), abs(page_pos.y() - centre.y())
            if dx <= tol_x and dy <= tol_y:
                dist = max(dx, dy)
                if dist < best_dist:
                    best, best_dist = handle, dist
        return best

    def boundingRect(self) -> QRectF:  # noqa: N802 (Qt override)
        rect = QRectF(self._rect)
        if self._ghost is not None:
            rect = rect.united(self._ghost)
        m = BOUNDS_MARGIN
        return rect.adjusted(-m, -m, m, m)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        painter.save()
        try:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            frame = QPen(SELECTION_COLOR, 0)
            frame.setCosmetic(True)
            painter.setPen(frame)
            painter.drawRect(self._rect)
            if self._ghost is not None:
                ghost = QPen(GHOST_COLOR, 0, Qt.PenStyle.DashLine)
                ghost.setCosmetic(True)
                painter.setPen(ghost)
                painter.setBrush(QBrush(GHOST_FILL))
                painter.drawRect(self._ghost)
            # Handles: fixed device size, centred on the mapped handle points.
            transform = painter.worldTransform()
            centres = [transform.map(p) for p in handle_points(self._rect).values()]
            painter.setWorldTransform(QTransform())
            border = QPen(SELECTION_COLOR, 1)
            border.setCosmetic(True)
            painter.setPen(border)
            painter.setBrush(QBrush(Qt.GlobalColor.white))
            half = HANDLE_PX // 2
            side = HANDLE_PX - 1  # an aliased 1 px outline covers side + 1 pixels
            for c in centres:
                painter.drawRect(round(c.x()) - half, round(c.y()) - half, side, side)
        finally:
            painter.restore()


class AnnotSelection(QObject):
    """The selected annotation of the document shown by ``view`` and its handle item.

    Owned by DocumentView (docs/M3_PLAN.md). ``select(info)`` shows an
    :class:`AnnotHandleItem` on ``info.page``; the selection is re-resolved by
    ``(page, name)`` on the page's ``page_changed`` (rect or content changed → updated;
    gone or no longer editable → cleared) and on ``structure_changed``/``reloaded``
    (PageItems replaced, caches dropped). ``changed()`` is emitted whenever ``current``
    changes. ``set_document`` must be called after ``PageView.set_document`` (its slots
    must run after the view's, which rebuilds the PageItems).
    """

    changed = Signal()

    def __init__(self, view: PageView, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._document: PdfDocument | None = None
        self._current: AnnotInfo | None = None
        self._item: AnnotHandleItem | None = None
        self._ghost: QRectF | None = None

    # -- state ----------------------------------------------------------------
    @property
    def current(self) -> AnnotInfo | None:
        return self._current

    @property
    def item(self) -> AnnotHandleItem | None:
        """The live handle item while something is selected."""
        return self._item if self._item_valid() else None

    @property
    def document(self) -> PdfDocument | None:
        return self._document

    # -- public API -------------------------------------------------------------
    def select(self, info: AnnotInfo) -> None:
        """Select ``info`` (a snapshot of the current document)."""
        if not 0 <= info.page < self._view.page_count:
            raise ValueError(f"page {info.page} is not shown")
        same = info == self._current
        if self._current is None or self._current.page != info.page:
            self._ghost = None
        self._current = info
        self._sync_item()
        if not same:
            self.changed.emit()

    def clear(self) -> None:
        """Deselect (no-op when nothing is selected)."""
        if self._current is None:
            return
        self._current = None
        self._ghost = None
        self._remove_item()
        self.changed.emit()

    def set_ghost(self, rect: QRectF | None) -> None:
        """Show (or remove, None) the dashed drag preview of the selection."""
        self._ghost = None if rect is None or self._current is None else QRectF(rect)
        item = self.item
        if item is not None:
            item.set_ghost(self._ghost)

    # -- document binding -------------------------------------------------------
    def set_document(self, document: PdfDocument | None) -> None:
        """Follow ``document`` (the selection is cleared)."""
        if document is self._document:
            return
        old = self._document
        if old is not None:
            try:
                old.page_changed.disconnect(self._on_page_changed)
                old.structure_changed.disconnect(self._refresh)
                old.reloaded.disconnect(self._refresh)
            except (RuntimeError, TypeError):
                pass
        self._document = document
        if document is not None:
            document.page_changed.connect(self._on_page_changed)
            document.structure_changed.connect(self._refresh)
            document.reloaded.connect(self._refresh)
        self.clear()
        self._remove_item()

    # -- internals -------------------------------------------------------------
    def _item_valid(self) -> bool:
        return self._item is not None and shiboken6.isValid(self._item)

    def _sync_item(self) -> None:
        info = self._current
        if info is None:
            self._remove_item()
            return
        page_item = self._view.page_item(info.page)
        if not self._item_valid():
            self._item = AnnotHandleItem(info.rect, page_item)
        else:
            assert self._item is not None
            if self._item.parentItem() is not page_item:
                self._item.setParentItem(page_item)
            self._item.set_rect(info.rect)
        self._item.set_ghost(self._ghost)

    def _remove_item(self) -> None:
        item, self._item = self._item, None
        if item is None or not shiboken6.isValid(item):
            return  # died with its PageItem (structure change)
        scene = item.scene()
        if scene is not None:
            scene.removeItem(item)  # detaches from the PageItem; Python then frees it
        else:
            item.setParentItem(None)

    def _resolve(self) -> AnnotInfo | None:
        info, doc = self._current, self._document
        if info is None or doc is None:
            return None
        if not 0 <= info.page < min(doc.page_count, self._view.page_count):
            return None
        fresh = doc.annot(info.page, info.name)
        return fresh if fresh is not None and fresh.editable else None

    def _refresh(self, *_args: object) -> None:
        if self._current is None:
            return
        fresh = self._resolve()
        if fresh is None:
            self.clear()
            return
        same = fresh == self._current
        self._current = fresh
        self._sync_item()
        if not same:
            self.changed.emit()

    def _on_page_changed(self, i: int) -> None:
        if self._current is not None and i == self._current.page:
            self._refresh()


__all__ = [
    "ANNOT_HANDLE_Z",
    "BOUNDS_MARGIN",
    "HANDLE_PX",
    "AnnotHandleItem",
    "AnnotSelection",
    "Handle",
    "handle_points",
]
