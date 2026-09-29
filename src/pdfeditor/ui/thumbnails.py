"""Page thumbnails: list model fed by the shared RenderService, and the sidebar view."""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QWidget

from pdfeditor.constants import THUMB_WIDTH_PX
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.geometry import quantize_scale
from pdfeditor.render.renderer import Priority, RenderKind
from pdfeditor.render.service import RenderService

ModelIndex = QModelIndex | QPersistentModelIndex


class ThumbnailModel(QAbstractListModel):
    """One row per page. DecorationRole is the cached thumbnail or a blank placeholder;
    a cache miss queues a low-priority "thumb" render."""

    def __init__(self, service: RenderService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._document: PdfDocument | None = None
        self.device_pixel_ratio = 1.0
        service.pixmap_ready.connect(self._on_pixmap_ready)

    def set_document(self, document: PdfDocument | None) -> None:
        self.beginResetModel()
        if self._document is not None:
            try:
                self._document.page_changed.disconnect(self._on_page_changed)
                self._document.structure_changed.disconnect(self._on_structure_changed)
            except (RuntimeError, TypeError):
                pass
        self._document = document
        if document is not None:
            document.page_changed.connect(self._on_page_changed)
            document.structure_changed.connect(self._on_structure_changed)
        self.endResetModel()

    def rowCount(self, parent: ModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid() or self._document is None or not self._document.is_open:
            return 0
        return self._document.page_count

    def thumb_scale(self, page: int) -> float:
        assert self._document is not None
        width = max(1.0, self._document.page_size(page).width())
        return quantize_scale(THUMB_WIDTH_PX * self.device_pixel_ratio / width)

    def thumb_size(self, page: int) -> QSize:
        """Logical (device-independent) size of the thumbnail of ``page``."""
        assert self._document is not None
        size = self._document.page_size(page)
        return QSize(THUMB_WIDTH_PX, max(1, round(THUMB_WIDTH_PX * size.height() / size.width())))

    def data(self, index: ModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self._document is None:
            return None
        page = index.row()
        if role == Qt.ItemDataRole.DisplayRole:
            return str(page + 1)
        if role == Qt.ItemDataRole.DecorationRole:
            scale = self.thumb_scale(page)
            pixmap = self._service.pixmap(page, scale, RenderKind.THUMB)
            if pixmap is None:
                self._service.request(page, scale, RenderKind.THUMB, Priority.THUMB)
                # Same-size content change: keep showing the previous thumbnail meanwhile.
                pixmap = self._service.best(page, RenderKind.THUMB)
                if pixmap is None:
                    return self._placeholder(page)
                pixmap = pixmap.scaled(
                    self.thumb_size(page) * self.device_pixel_ratio,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                pixmap.setDevicePixelRatio(self.device_pixel_ratio)
                return pixmap
            pixmap = QPixmap(pixmap)
            pixmap.setDevicePixelRatio(self.device_pixel_ratio)
            return pixmap
        if role == Qt.ItemDataRole.SizeHintRole:
            return self.thumb_size(page) + QSize(16, 28)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignHCenter
        return None

    def is_rendered(self, page: int) -> bool:
        return self._service.pixmap(page, self.thumb_scale(page), RenderKind.THUMB) is not None

    def _placeholder(self, page: int) -> QPixmap:
        size = self.thumb_size(page)
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.white)
        painter = QPainter(pixmap)
        painter.setPen(QColor(200, 200, 200))
        painter.drawRect(0, 0, size.width() - 1, size.height() - 1)
        painter.end()
        return pixmap

    def _row_changed(self, page: int) -> None:
        if 0 <= page < self.rowCount():
            idx = self.index(page)
            self.dataChanged.emit(idx, idx)

    def _on_pixmap_ready(self, page: int, kind: str) -> None:
        if kind == RenderKind.THUMB:
            self._row_changed(page)

    def _on_page_changed(self, page: int) -> None:
        self._row_changed(page)

    def _on_structure_changed(self, *_args) -> None:
        self.beginResetModel()
        self.endResetModel()


class ThumbnailSidebar(QListView):
    """Single-column icon list; selection follows the current page, clicks navigate."""

    page_requested = Signal(int)

    def __init__(self, model: ThumbnailModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("thumbnails")
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.TopToBottom)
        self.setWrapping(False)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(False)
        self.setSpacing(6)
        self.setIconSize(QSize(THUMB_WIDTH_PX, THUMB_WIDTH_PX * 2))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(THUMB_WIDTH_PX + 40)
        self._syncing = False
        model.device_pixel_ratio = self.devicePixelRatioF()
        self.setModel(model)
        self.selectionModel().currentRowChanged.connect(self._on_current_row_changed)

    @property
    def thumbnail_model(self) -> ThumbnailModel:
        return self.model()  # type: ignore[return-value]

    def set_current_page(self, page: int) -> None:
        """Select ``page`` without emitting page_requested."""
        if not 0 <= page < self.model().rowCount():
            return
        self._syncing = True
        try:
            idx = self.model().index(page, 0)
            self.setCurrentIndex(idx)
            self.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)
        finally:
            self._syncing = False

    def _on_current_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not self._syncing and current.isValid():
            self.page_requested.emit(current.row())

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        idx = self.indexAt(event.position().toPoint())
        if idx.isValid() and event.button() == Qt.MouseButton.LeftButton:
            self.page_requested.emit(idx.row())
