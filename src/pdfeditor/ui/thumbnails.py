"""Page thumbnails: list model fed by the shared RenderService, and the sidebar view."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
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

PAGES_MIME = "application/x-pdfeditor-pages"


def encode_rows(rows: Iterable[int]) -> QByteArray:
    return QByteArray(",".join(str(r) for r in sorted(set(rows))).encode("ascii"))


def decode_rows(data: QByteArray | bytes) -> list[int]:
    """Rows from a PAGES_MIME payload; [] when malformed."""
    try:
        text = bytes(data).decode("ascii")
        return sorted({int(x) for x in text.split(",") if x.strip()})
    except (ValueError, UnicodeDecodeError):
        return []


class ThumbnailModel(QAbstractListModel):
    """One row per page. DecorationRole is the cached thumbnail or a blank placeholder;
    a cache miss queues a low-priority "thumb" render. Rows are draggable (PAGES_MIME)
    but the model never moves them: the sidebar turns drops into requests."""

    # Re-emitted pages_remapped: the rows are about to change (structure_changed follows).
    remap_pending = Signal(object)

    def __init__(self, service: RenderService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._document: PdfDocument | None = None
        self.device_pixel_ratio = 1.0
        # old_to_new row mapping of the pending structural change (pages_remapped arrives
        # right before structure_changed); the sidebar uses it to keep its selection.
        self.pending_mapping: list[int | None] | None = None
        service.pixmap_ready.connect(self._on_pixmap_ready)

    def set_document(self, document: PdfDocument | None) -> None:
        self.beginResetModel()
        if self._document is not None:
            try:
                self._document.page_changed.disconnect(self._on_page_changed)
                self._document.pages_remapped.disconnect(self._on_pages_remapped)
                self._document.structure_changed.disconnect(self._on_structure_changed)
            except (RuntimeError, TypeError):
                pass
        self._document = document
        self.pending_mapping = None
        if document is not None:
            document.page_changed.connect(self._on_page_changed)
            document.pages_remapped.connect(self._on_pages_remapped)
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

    def flags(self, index: ModelIndex) -> Qt.ItemFlag:
        if index.isValid():
            return (
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDragEnabled
            )
        return Qt.ItemFlag.ItemIsDropEnabled

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        return [PAGES_MIME]

    def mimeData(self, indexes: Sequence[ModelIndex]) -> QMimeData:
        data = QMimeData()
        data.setData(PAGES_MIME, encode_rows(i.row() for i in indexes if i.isValid()))
        return data

    def dropMimeData(self, *_args) -> bool:
        # The sidebar turns drops into pages_move_requested; rows never move here.
        return False

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

    def _on_pages_remapped(self, mapping: object) -> None:
        self.pending_mapping = list(mapping)  # type: ignore[call-overload]
        self.remap_pending.emit(self.pending_mapping)

    def _on_structure_changed(self, *_args) -> None:
        self.beginResetModel()
        self.endResetModel()
        self.pending_mapping = None


class ThumbnailSidebar(QListView):
    """Single-column icon list; the current item follows the current page and clicks
    navigate. Ctrl/Shift+click build a multi-selection that survives navigation and
    structural changes; dragging thumbnails, the Delete key and the context menu are
    reported as requests (the model never moves rows itself)."""

    page_requested = Signal(int)
    pages_move_requested = Signal(list, int)  # sorted rows, row they go before
    pages_delete_requested = Signal(list)  # sorted rows
    context_menu_requested = Signal(list, QPoint)  # sorted rows, global position

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
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(THUMB_WIDTH_PX + 40)
        self._syncing = False
        self._in_mouse_press = False
        self._saved_rows: list[int] = []
        self._saved_current = -1
        model.device_pixel_ratio = self.devicePixelRatioF()
        self.setModel(model)
        self.selectionModel().currentRowChanged.connect(self._on_current_row_changed)
        self._deferred_current: int | None = None
        model.remap_pending.connect(self._remember_selection)
        model.modelReset.connect(self._restore_selection)

    @property
    def thumbnail_model(self) -> ThumbnailModel:
        return self.model()  # type: ignore[return-value]

    def set_current_page(self, page: int) -> None:
        """Make ``page`` current without emitting page_requested. Inside a
        multi-selection the selection is kept; otherwise ``page`` becomes the
        selection."""
        if self.thumbnail_model.pending_mapping is not None:
            # Between pages_remapped and the model reset rows are stale: apply later.
            self._deferred_current = page
            return
        if not 0 <= page < self.model().rowCount():
            return
        self._syncing = True
        try:
            idx = self.model().index(page, 0)
            sel = self.selectionModel()
            selected = sel.selectedRows()
            if self._in_mouse_press or (idx == self.currentIndex() and selected):
                pass  # the click itself (Ctrl/Shift aware) decides the selection
            elif sel.isSelected(idx) and len(selected) > 1:
                sel.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
            else:
                sel.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            self.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)
        finally:
            self._syncing = False

    def _on_current_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not self._syncing and current.isValid():
            self.page_requested.emit(current.row())

    def selected_pages(self) -> list[int]:
        """Selected rows, sorted."""
        return sorted(i.row() for i in self.selectionModel().selectedRows())

    def select_pages(self, rows: Iterable[int], current: int | None = None) -> None:
        """Replace the selection by ``rows`` (out-of-range rows ignored). ``current``
        (default: the first row) becomes the current item; page_requested is not
        emitted."""
        model = self.model()
        count = model.rowCount()
        valid = sorted({r for r in rows if 0 <= r < count})
        selection = QItemSelection()
        for row in valid:
            idx = model.index(row, 0)
            selection.select(idx, idx)
        if current is None and valid:
            current = valid[0]
        sel = self.selectionModel()
        self._syncing = True
        try:
            if current is not None and 0 <= current < count:
                sel.setCurrentIndex(
                    model.index(current, 0), QItemSelectionModel.SelectionFlag.NoUpdate
                )
            sel.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        finally:
            self._syncing = False

    def drop_row(self, pos: QPoint) -> int:
        """Row the dragged pages go before for a drop at viewport position ``pos``."""
        model = self.model()
        idx = self.indexAt(pos)
        if not idx.isValid():
            count = model.rowCount()
            if count and pos.y() < self.visualRect(model.index(0, 0)).top():
                return 0
            return count
        rect = self.visualRect(idx)
        return idx.row() + (1 if pos.y() > rect.center().y() else 0)

    def dropEvent(self, event) -> None:
        rows = decode_rows(event.mimeData().data(PAGES_MIME))
        if not rows or event.source() not in (self, None):
            event.ignore()
            return
        target = self.drop_row(event.position().toPoint())
        # IgnoreAction: QAbstractItemView must never remove the source rows itself.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self.pages_move_requested.emit(rows, target)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete and event.modifiers() in (
            Qt.KeyboardModifier.NoModifier,
            Qt.KeyboardModifier.KeypadModifier,
        ):
            rows = self.selected_pages()
            if not rows and self.currentIndex().isValid():
                rows = [self.currentIndex().row()]
            if rows:
                self.pages_delete_requested.emit(rows)
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
        idx = self.indexAt(event.pos())
        if not idx.isValid():
            event.ignore()
            return
        rows = self.selected_pages()
        if idx.row() not in rows:
            rows = [idx.row()]
        event.accept()
        self.context_menu_requested.emit(rows, event.globalPos())

    def _remember_selection(self, _mapping: object = None) -> None:
        self._saved_rows = self.selected_pages()
        self._saved_current = self.currentIndex().row()
        self._deferred_current = None

    def _restore_selection(self) -> None:
        """After a structural change keep a multi-selection on the same pages and
        apply the current page the page view reported meanwhile."""
        rows, current = self._saved_rows, self._saved_current
        deferred = self._deferred_current
        self._saved_rows, self._saved_current, self._deferred_current = [], -1, None
        model = self.thumbnail_model
        mapping = model.pending_mapping
        if mapping is None:  # plain reset (set_document)
            return
        new_rows = [m for r in rows if 0 <= r < len(mapping) and (m := mapping[r]) is not None]
        if len(rows) >= 2 and len(new_rows) >= 2:
            if deferred is None and 0 <= current < len(mapping):
                deferred = mapping[current]
            self.select_pages(new_rows, deferred)
            if deferred is not None:
                self.scrollTo(model.index(deferred, 0))
            return
        if deferred is not None:
            model.pending_mapping = None  # still set while modelReset is delivered
            try:
                self.set_current_page(deferred)
            finally:
                model.pending_mapping = mapping

    def mousePressEvent(self, event) -> None:
        # Navigation triggered from inside the press (currentRowChanged -> page view ->
        # set_current_page) must not override the Ctrl/Shift selection being built.
        self._in_mouse_press = True
        try:
            super().mousePressEvent(event)
        finally:
            self._in_mouse_press = False
        idx = self.indexAt(event.position().toPoint())
        if (
            idx.isValid()
            and event.button() == Qt.MouseButton.LeftButton
            and not event.modifiers()
            & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        ):
            self.page_requested.emit(idx.row())
