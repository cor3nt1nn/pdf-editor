"""PageItem: one QGraphicsItem per PDF page, in page space (points)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from pdfeditor.constants import ZOOM_MAX
from pdfeditor.core.geometry import quantize_scale, zoom_to_scale
from pdfeditor.render.renderer import Priority, RenderKind

if TYPE_CHECKING:
    from pdfeditor.render.service import RenderService

BORDER_COLOR = QColor(160, 160, 160)


class PageItem(QGraphicsItem):
    """Paints the cached rendering of one page into its point-sized bounding rect.

    Child items added in M2+ (FieldHitItem, AnnotHandleItem, ...) use page space too.
    """

    def __init__(self, service: RenderService, page_index: int, size: QSizeF) -> None:
        super().__init__()
        self._service = service
        self.page_index = page_index
        self._size = QSizeF(size)
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self.last_scale: float | None = None

    @property
    def size(self) -> QSizeF:
        return QSizeF(self._size)

    def set_size(self, size: QSizeF) -> None:
        if size != self._size:
            self.prepareGeometryChange()
            self._size = QSizeF(size)
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(0.0, 0.0, self._size.width(), self._size.height())

    def render_scale(self, painter: QPainter) -> float:
        lod = QStyleOptionGraphicsItem.levelOfDetailFromTransform(painter.worldTransform())
        device = painter.device()
        dpr = device.devicePixelRatioF() if device is not None else 1.0
        cap = zoom_to_scale(ZOOM_MAX) * dpr
        return quantize_scale(min(lod * dpr, cap))

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        rect = self.boundingRect()
        scale = self.render_scale(painter)
        self.last_scale = scale
        pixmap = self._service.pixmap(self.page_index, scale)
        if pixmap is not None:
            painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
        else:
            painter.fillRect(rect, Qt.GlobalColor.white)
            placeholder = self._service.best(self.page_index) or self._service.best(
                self.page_index, RenderKind.THUMB
            )
            if placeholder is not None:
                painter.drawPixmap(rect, placeholder, QRectF(placeholder.rect()))
            self._service.request(self.page_index, scale, RenderKind.PAGE, Priority.VISIBLE)
        pen = QPen(BORDER_COLOR)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
