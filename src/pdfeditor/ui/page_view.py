"""DocumentScene and PageView: continuous-scroll, zoomable page display."""

from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QWidget

from pdfeditor.constants import (
    BASE_SCALE,
    MARGIN_PT,
    PAGE_GAP_PT,
    ZOOM_MAX,
    ZOOM_MIN,
    ZOOM_STEPS,
    ZoomMode,
)
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.geometry import quantize_scale, zoom_to_scale
from pdfeditor.render.renderer import Priority, RenderKind
from pdfeditor.render.service import RenderService
from pdfeditor.ui.page_item import PageItem
from pdfeditor.ui.tools.base import ToolEvent, ToolManager

log = logging.getLogger(__name__)

BACKGROUND = QColor(128, 128, 128)
RENDER_DEBOUNCE_MS = 30


def clamp_zoom(percent: float) -> float:
    return max(ZOOM_MIN, min(ZOOM_MAX, float(percent)))


@dataclass(frozen=True)
class RemapTarget:
    """Page to show after a structural change. ``same_spot``: it is the former current
    page (keep the scroll offset inside it); ``inserted``: it is a newly added page."""

    page: int
    same_spot: bool = False
    inserted: bool = False


def remapped_current_page(mapping: list[int | None], current: int, count: int) -> RemapTarget:
    """Where the view goes after ``pages_remapped(mapping)`` when ``current`` was shown:
    the first page that did not exist before (insertion, undo of a deletion), else the
    former current page at its new index, else the nearest surviving page (the next one
    first, which took its place, then the previous one)."""
    survivors = {new for new in mapping if new is not None}
    added = [i for i in range(count) if i not in survivors]
    if added:
        return RemapTarget(added[0], inserted=True)
    if 0 <= current < len(mapping) and mapping[current] is not None:
        return RemapTarget(mapping[current], same_spot=True)
    for old in range(current + 1, len(mapping)):
        if mapping[old] is not None:
            return RemapTarget(mapping[old])
    for old in range(min(current, len(mapping)) - 1, -1, -1):
        if mapping[old] is not None:
            return RemapTarget(mapping[old])
    return RemapTarget(0)


class DocumentScene(QGraphicsScene):
    """Holds one PageItem per page, stacked vertically and centered on the widest page."""

    def __init__(self, service: RenderService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._document: PdfDocument | None = None
        self.page_items: list[PageItem] = []
        self._tops: list[float] = []
        self.setBackgroundBrush(BACKGROUND)
        self.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)

    @property
    def document(self) -> PdfDocument | None:
        return self._document

    def set_document(self, document: PdfDocument | None) -> None:
        for item in self.page_items:
            self.removeItem(item)
        self.page_items = []
        self._document = document
        if document is not None:
            for i in range(document.page_count):
                item = PageItem(self._service, i, document.page_size(i))
                self.addItem(item)
                self.page_items.append(item)
        self.relayout()

    def relayout(self) -> None:
        """Recompute page offsets (scene space) from the document's page sizes."""
        doc = self._document
        if doc is None or not self.page_items:
            self._tops = []
            self.setSceneRect(QRectF(0, 0, 1, 1))
            return
        sizes = [doc.page_size(i) for i in range(len(self.page_items))]
        max_w = max(s.width() for s in sizes)
        y = MARGIN_PT
        self._tops = []
        for item, size in zip(self.page_items, sizes, strict=True):
            item.set_size(size)
            item.setPos((max_w - size.width()) / 2.0 + MARGIN_PT, y)
            self._tops.append(y)
            y += size.height() + PAGE_GAP_PT
        height = y - PAGE_GAP_PT + MARGIN_PT
        self.setSceneRect(QRectF(0, 0, max_w + 2 * MARGIN_PT, height))

    def page_offset(self, i: int) -> QPointF:
        return self.page_items[i].pos()

    def page_scene_rect(self, i: int) -> QRectF:
        item = self.page_items[i]
        return item.boundingRect().translated(item.pos())

    def max_page_width(self) -> float:
        return max((it.size.width() for it in self.page_items), default=0.0)

    def page_at_y(self, y: float) -> int | None:
        """Index of the page whose vertical extent contains scene ``y``, else None."""
        if not self._tops:
            return None
        i = bisect.bisect_right(self._tops, y) - 1
        if i < 0:
            return None
        if y <= self._tops[i] + self.page_items[i].size.height():
            return i
        return None

    def page_at(self, scene_pos: QPointF) -> int | None:
        i = self.page_at_y(scene_pos.y())
        if i is not None and self.page_scene_rect(i).contains(scene_pos):
            return i
        return None

    def pages_in(self, rect: QRectF) -> list[int]:
        if not self._tops:
            return []
        first = max(0, bisect.bisect_right(self._tops, rect.top()) - 1)
        result = []
        for i in range(first, len(self.page_items)):
            if self._tops[i] > rect.bottom():
                break
            if self.page_scene_rect(i).intersects(rect):
                result.append(i)
        return result


class PageView(QGraphicsView):
    """Zoomable continuous view of a PdfDocument."""

    zoom_changed = Signal(float, object)  # (zoom percent, ZoomMode)
    current_page_changed = Signal(int)

    def __init__(self, service: RenderService | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.service = service if service is not None else RenderService(self)
        self._scene = DocumentScene(self.service, self)
        self.setScene(self._scene)
        self._document: PdfDocument | None = None
        self._zoom = 100.0
        self._mode = ZoomMode.FIT_WIDTH
        self._current = -1
        self._suppress_current = False
        # old_to_new of the latest ``pages_remapped``, consumed by _on_structure_changed
        self._pending_remap: list[int | None] | None = None
        self.tool_manager: ToolManager | None = None  # set by ToolManager(view)

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setBackgroundBrush(BACKGROUND)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(RENDER_DEBOUNCE_MS)
        self._render_timer.timeout.connect(self.request_visible_renders)

        self.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self.horizontalScrollBar().valueChanged.connect(self._on_scrolled)
        self.service.pixmap_ready.connect(self._on_pixmap_ready)
        self._apply_transform()

    # -- document -----------------------------------------------------------
    @property
    def document(self) -> PdfDocument | None:
        return self._document

    @property
    def doc_scene(self) -> DocumentScene:
        return self._scene

    def set_document(self, document: PdfDocument | None) -> None:
        if self._document is not None:
            try:
                self._document.page_changed.disconnect(self._on_page_changed)
                self._document.structure_changed.disconnect(self._on_structure_changed)
                self._document.pages_remapped.disconnect(self._on_pages_remapped)
            except (RuntimeError, TypeError):
                pass
        self._document = document
        self._pending_remap = None
        self.service.set_document(document)
        if document is not None:
            document.page_changed.connect(self._on_page_changed)
            document.pages_remapped.connect(self._on_pages_remapped)
            document.structure_changed.connect(self._on_structure_changed)
        self._scene.set_document(document)
        self._current = -1
        if self._mode != ZoomMode.CUSTOM:
            self._apply_fit()
        self._apply_transform()
        if document is not None:
            self.scroll_to_page(0)
        self.viewport().update()

    def relayout(self) -> None:
        self._scene.relayout()
        if self._mode != ZoomMode.CUSTOM:
            self._apply_fit()
        self._schedule_render()

    @property
    def page_count(self) -> int:
        return len(self._scene.page_items)

    def page_item(self, i: int) -> PageItem:
        return self._scene.page_items[i]

    def _on_page_changed(self, i: int) -> None:
        """Content-only change (same size): re-render in place, keeping the old pixmap as
        placeholder and the scroll position. Size change (rotation...): relayout, keeping
        the view anchored on the same spot of the current page."""
        if self._document is None or not 0 <= i < self.page_count:
            return
        item = self.page_item(i)
        if self._document.page_size(i) == item.size:
            self.service.invalidate_page(i, keep_stale=True)
            item.update()
            self._schedule_render()
            return
        anchor = self._capture_anchor()
        self.service.invalidate_page(i)
        self.relayout()
        item.update()
        if anchor is not None:
            self._restore_anchor(*anchor)
        self._update_current_page()

    def _capture_anchor(self) -> tuple[int, float] | None:
        """(current page, viewport top as a fraction of that page's height from its top)."""
        if self._current < 0 or not self.page_count:
            return None
        page = self._current
        top = self.mapToScene(self.viewport().rect().topLeft()).y()
        height = max(1.0, self.page_item(page).size.height())
        return page, (top - self._scene.page_offset(page).y()) / height

    def _restore_anchor(self, page: int, fraction: float) -> None:
        page = min(page, self.page_count - 1)
        top = self._scene.page_offset(page).y() + fraction * self.page_item(page).size.height()
        vh = self.viewport().height() / self.view_scale
        center_x = self.mapToScene(self.viewport().rect().center()).x()
        self._suppress_current = True
        try:
            self.centerOn(QPointF(center_x, top + vh / 2.0))
        finally:
            self._suppress_current = False
        self._schedule_render()

    def _on_pages_remapped(self, mapping: list[int | None]) -> None:
        """Move the cached pixmaps right away (before any other ``structure_changed``
        listener, e.g. the thumbnail model, asks for them) and remember the mapping."""
        self.service.remap_pages(mapping)
        self._pending_remap = list(mapping)

    def _on_structure_changed(self) -> None:
        mapping, self._pending_remap = self._pending_remap, None
        anchor = self._capture_anchor()
        if mapping is None:
            self.service.reset()
        self._scene.set_document(self._document)
        # Always re-announce the current page: the thumbnail model was reset and the
        # page shown at the same index may be another one.
        self._current = -1
        self.relayout()
        if not self.page_count:
            return
        if mapping is None or anchor is None:
            current = 0 if anchor is None else anchor[0]
            self.scroll_to_page(max(0, min(current, self.page_count - 1)))
            return
        page, fraction = anchor
        target = remapped_current_page(mapping, page, self.page_count)
        if target.inserted:
            self.scroll_to_page(target.page)
            return
        if not target.same_spot:
            fraction = 0.0
        self._restore_anchor(target.page, fraction)
        self._set_current(target.page)

    def _on_pixmap_ready(self, page: int, kind: str) -> None:
        if kind == RenderKind.PAGE and 0 <= page < self.page_count:
            self.page_item(page).update()

    # -- zoom -------------------------------------------------------------------
    @property
    def zoom_percent(self) -> float:
        return self._zoom

    @property
    def zoom_mode(self) -> ZoomMode:
        return self._mode

    @property
    def view_scale(self) -> float:
        return zoom_to_scale(self._zoom)

    def set_zoom_percent(self, percent: float, anchor_under_mouse: bool = False) -> None:
        self._mode = ZoomMode.CUSTOM
        self._set_zoom(clamp_zoom(percent), anchor_under_mouse)

    def set_zoom_mode(self, mode: ZoomMode) -> None:
        self._mode = ZoomMode(mode)
        if self._mode == ZoomMode.CUSTOM:
            self.zoom_changed.emit(self._zoom, self._mode)
            return
        self._apply_fit(force_emit=True)

    def zoom_in(self, anchor_under_mouse: bool = False) -> None:
        nxt = next((z for z in ZOOM_STEPS if z > self._zoom + 0.01), ZOOM_MAX)
        self.set_zoom_percent(nxt, anchor_under_mouse)

    def zoom_out(self, anchor_under_mouse: bool = False) -> None:
        prev = next((z for z in reversed(ZOOM_STEPS) if z < self._zoom - 0.01), ZOOM_MIN)
        self.set_zoom_percent(prev, anchor_under_mouse)

    def fit_zoom(self, mode: ZoomMode) -> float:
        """Zoom percent that fits the widest page (FIT_WIDTH) or the current page (FIT_PAGE)."""
        if not self.page_count:
            return self._zoom
        vw = max(1, self.viewport().width())
        vh = max(1, self.viewport().height())
        width_pt = self._scene.max_page_width() + 2 * MARGIN_PT
        zoom_w = vw / (width_pt * BASE_SCALE) * 100.0
        if mode == ZoomMode.FIT_WIDTH:
            return clamp_zoom(zoom_w)
        size = self.page_item(max(0, self._current)).size
        zoom_w = vw / ((size.width() + 2 * MARGIN_PT) * BASE_SCALE) * 100.0
        zoom_h = vh / ((size.height() + 2 * PAGE_GAP_PT) * BASE_SCALE) * 100.0
        return clamp_zoom(min(zoom_w, zoom_h))

    def _apply_fit(self, force_emit: bool = False) -> None:
        zoom = self.fit_zoom(self._mode)
        if abs(zoom - self._zoom) > 1e-6 or force_emit:
            self._set_zoom(zoom, False, keep_page=True)

    def _set_zoom(self, zoom: float, anchor_under_mouse: bool, keep_page: bool = False) -> None:
        changed = abs(zoom - self._zoom) > 1e-9
        current = self._current
        self._zoom = zoom
        if anchor_under_mouse:
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._apply_transform()
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        if keep_page and self._mode == ZoomMode.FIT_PAGE and current >= 0:
            self.scroll_to_page(current)
        if changed:
            self.service.new_generation(RenderKind.PAGE)
        self.zoom_changed.emit(self._zoom, self._mode)
        self._update_current_page()
        self._schedule_render()

    def _apply_transform(self) -> None:
        s = self.view_scale
        self.setTransform(QTransform.fromScale(s, s))

    # -- navigation ---------------------------------------------------------
    @property
    def current_page(self) -> int:
        return self._current

    def scroll_to_page(self, i: int) -> None:
        if not self.page_count:
            return
        i = max(0, min(int(i), self.page_count - 1))
        top = self._scene.page_offset(i).y() - PAGE_GAP_PT / 2.0
        vh = self.viewport().height() / self.view_scale
        center_x = self.mapToScene(self.viewport().rect().center()).x()
        self._suppress_current = True
        try:
            self.centerOn(QPointF(center_x, top + vh / 2.0))
        finally:
            self._suppress_current = False
        self._set_current(i)
        self._schedule_render()

    def next_page(self) -> None:
        self.scroll_to_page(self._current + 1)

    def previous_page(self) -> None:
        self.scroll_to_page(self._current - 1)

    def _set_current(self, i: int) -> None:
        if i != self._current:
            self._current = i
            self.current_page_changed.emit(i)

    def _visible_scene_rect(self) -> QRectF:
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def compute_current_page(self) -> int:
        """Page containing the viewport-center y; fallback: largest visible intersection."""
        if not self.page_count:
            return -1
        bar = self.verticalScrollBar()
        if bar.maximum() > 0 and bar.value() >= bar.maximum():
            return self.page_count - 1
        if bar.value() <= bar.minimum():
            return 0
        center = self.mapToScene(self.viewport().rect().center())
        page = self._scene.page_at_y(center.y())
        if page is not None:
            return page
        visible = self._visible_scene_rect()
        best, best_area = 0, -1.0
        for i in self._scene.pages_in(visible):
            r = self._scene.page_scene_rect(i).intersected(visible)
            area = r.width() * r.height()
            if area > best_area:
                best, best_area = i, area
        return best

    def _update_current_page(self) -> None:
        if not self._suppress_current and self.page_count:
            self._set_current(self.compute_current_page())

    def _on_scrolled(self, _value: int) -> None:
        self._update_current_page()
        self._schedule_render()

    # -- rendering ------------------------------------------------------------
    def visible_pages(self) -> list[int]:
        return self._scene.pages_in(self._visible_scene_rect())

    def _schedule_render(self) -> None:
        self._render_timer.start()

    def request_visible_renders(self) -> None:
        """Queue visible pages (priority 0) and their neighbours (priority 1)."""
        if not self.page_count:
            return
        dpr = self.viewport().devicePixelRatioF()
        scale = quantize_scale(self.view_scale * dpr)
        visible = self.visible_pages()
        for i in visible:
            self.service.request(i, scale, RenderKind.PAGE, Priority.VISIBLE)
        if visible:
            for i in (visible[0] - 1, visible[-1] + 1):
                if 0 <= i < self.page_count:
                    self.service.request(i, scale, RenderKind.PAGE, Priority.NEIGHBOR)

    # -- coordinate mapping (overlay hooks for M2+) ---------------------------
    def page_rect_to_viewport(self, page_index: int, rect: QRectF) -> QRect:
        """Map a rect in page space (points) to viewport pixel coordinates."""
        scene_rect = QRectF(rect).translated(self._scene.page_offset(page_index))
        return self.mapFromScene(scene_rect).boundingRect()

    def viewport_to_page(self, pos: QPoint) -> tuple[int, QPointF] | None:
        """Map a viewport position to (page index, point in page space), or None."""
        scene_pos = self.mapToScene(pos)
        i = self._scene.page_at(scene_pos)
        if i is None:
            return None
        return i, scene_pos - self._scene.page_offset(i)

    # -- tool forwarding --------------------------------------------------------
    def _tool_event(self, event: QMouseEvent | QKeyEvent) -> ToolEvent:
        if isinstance(event, QMouseEvent):
            pos = event.position().toPoint()
            buttons, modifiers = event.buttons(), event.modifiers()
        else:
            pos = self.viewport().mapFromGlobal(self.cursor().pos())
            buttons, modifiers = Qt.MouseButton.NoButton, event.modifiers()
        scene_pos = self.mapToScene(pos)
        hit = self.viewport_to_page(pos)
        page_index, page_pos = hit if hit is not None else (None, None)
        return ToolEvent(page_index, page_pos, scene_pos, buttons, modifiers, event)

    def _forward(self, handler: str, event: QMouseEvent | QKeyEvent) -> bool:
        tool = self.tool_manager.active_tool if self.tool_manager is not None else None
        if tool is None or self._document is None:
            return False
        if getattr(tool, handler)(self._tool_event(event)):
            event.accept()
            return True
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._forward("mouse_press", event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._forward("mouse_move", event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self._forward("mouse_release", event):
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if not self._forward("mouse_double_click", event):
            super().mouseDoubleClickEvent(event)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawForeground(painter, rect)
        tool = self.tool_manager.active_tool if self.tool_manager is not None else None
        if tool is not None:
            tool.paint_overlay(painter)

    # -- events ---------------------------------------------------------------
    def event(self, event: QEvent) -> bool:
        # Tab/Shift+Tab never reach keyPressEvent (QWidget.event moves the focus first):
        # offer them to the active tool (form field navigation).
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
            and self._forward("key_press", event)
        ):
            return True
        return super().event(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        current = self._current
        super().resizeEvent(event)
        if self._mode != ZoomMode.CUSTOM:
            self._apply_fit()
            if current >= 0:
                self.scroll_to_page(current)
        self._schedule_render()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in(anchor_under_mouse=True)
            elif delta < 0:
                self.zoom_out(anchor_under_mouse=True)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._forward("key_press", event):
            return
        key = event.key()
        if event.modifiers() in (
            Qt.KeyboardModifier.NoModifier,
            Qt.KeyboardModifier.KeypadModifier,
        ):
            if key == Qt.Key.Key_PageDown:
                self.next_page()
                return
            if key == Qt.Key.Key_PageUp:
                self.previous_page()
                return
            if key == Qt.Key.Key_Home:
                self.scroll_to_page(0)
                return
            if key == Qt.Key.Key_End:
                self.scroll_to_page(self.page_count - 1)
                return
        super().keyPressEvent(event)
