"""Page thumbnails: list model fed by the shared RenderService, and the sidebar view."""

from __future__ import annotations

import uuid
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
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QWidget

from pdfeditor.constants import THUMB_WIDTH_PX
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.geometry import quantize_scale
from pdfeditor.render.renderer import Priority, RenderKind
from pdfeditor.render.service import RenderService

ModelIndex = QModelIndex | QPersistentModelIndex

PAGES_MIME = "application/x-pdfeditor-pages"
#: Second format of a page drag: the token of the model it comes from, so that a drop
#: whose ``source()`` is unknown (synthetic events) is accepted only from this sidebar,
#: never from another window or process whose rows mean other pages.
PAGES_SOURCE_MIME = "application/x-pdfeditor-pages-source"
#: Interval (ms) and step (px) of the scrolling while a drag hovers near an edge.
AUTOSCROLL_MS = 40
AUTOSCROLL_STEP = 24


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
        #: Identifies the drags of this model (:data:`PAGES_SOURCE_MIME`).
        self.drag_token = QByteArray(uuid.uuid4().hex.encode("ascii"))
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
        data.setData(PAGES_SOURCE_MIME, self.drag_token)
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
    reported as requests (the model never moves rows itself).

    Drag and drop is handled here, not by ``QAbstractItemView``: in IconMode Qt 6.11
    ignores a drag over an item that is not drop-enabled, so a page could never be dropped
    onto another one, and draws no insertion mark. :meth:`dragMoveEvent` accepts any of
    our page drags, remembers the insertion row (:meth:`drop_row`), paints a line there
    and scrolls near the edges (docs/ARCHITECTURE.md Deviation 99).
    """

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
        self.setDropIndicatorShown(False)  # painted by paintEvent (IconMode draws none)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        # setMovement(Static) made the viewport refuse drops: drag events then never
        # reach the view's handlers (they go to the window).
        self.viewport().setAcceptDrops(True)
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
        # Insertion row of the drag in progress (None: no drag over us).
        self._drop_target: int | None = None
        self._drag_pos = QPoint()
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(AUTOSCROLL_MS)
        self._autoscroll.timeout.connect(self._autoscroll_step)

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
        """Row the dragged pages go before for a drop at viewport position ``pos``: the
        first row whose centre lies below ``pos`` (only y counts, so a drop in a gap or
        beside a thumbnail lands between its neighbours), else the end."""
        model = self.model()
        low, high = 0, model.rowCount()
        while low < high:  # rows are laid out top to bottom
            mid = (low + high) // 2
            if self.visualRect(model.index(mid, 0)).center().y() > pos.y():
                high = mid
            else:
                low = mid + 1
        return low

    @property
    def drop_target(self) -> int | None:
        """Insertion row of the drag hovering over the sidebar (None without one)."""
        return self._drop_target

    def _accepts(self, event) -> bool:
        """``event`` carries pages dragged from this sidebar, and dragging is allowed."""
        mime = event.mimeData()
        if not self.dragEnabled() or mime is None or not mime.hasFormat(PAGES_MIME):
            return False
        if event.source() is self:
            return True
        token = mime.data(PAGES_SOURCE_MIME)
        return event.source() is None and token == self.thumbnail_model.drag_token

    def _set_drop_target(self, row: int | None) -> None:
        if row != self._drop_target:
            self._drop_target = row
            self.viewport().update()

    def _end_drag(self) -> None:
        self._autoscroll.stop()
        self._set_drop_target(None)

    def dragEnterEvent(self, event) -> None:
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event) -> None:
        if not self._accepts(event):
            self._end_drag()
            event.ignore()
            return
        self._drag_pos = event.position().toPoint()
        self._set_drop_target(self.drop_row(self._drag_pos))
        margin = self.autoScrollMargin()
        y, height = self._drag_pos.y(), self.viewport().height()
        if y < margin or y > height - margin:
            if not self._autoscroll.isActive():
                self._autoscroll.start()
        else:
            self._autoscroll.stop()
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def dragLeaveEvent(self, event) -> None:
        self._end_drag()
        event.accept()

    def _autoscroll_step(self) -> None:
        bar = self.verticalScrollBar()
        margin = self.autoScrollMargin()
        y = self._drag_pos.y()
        if y < margin:
            bar.setValue(bar.value() - AUTOSCROLL_STEP)
        elif y > self.viewport().height() - margin:
            bar.setValue(bar.value() + AUTOSCROLL_STEP)
        else:
            self._autoscroll.stop()
            return
        self._set_drop_target(self.drop_row(self._drag_pos))

    def dropEvent(self, event) -> None:
        self._end_drag()
        rows = decode_rows(event.mimeData().data(PAGES_MIME))
        if not rows or not self._accepts(event):
            event.ignore()
            return
        target = self.drop_row(event.position().toPoint())
        # IgnoreAction: QAbstractItemView must never remove the source rows itself.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self.pages_move_requested.emit(rows, target)

    def drop_indicator_y(self, row: int) -> int:
        """Viewport y of the insertion line before ``row`` (``rowCount`` = after the last)."""
        model = self.model()
        count = model.rowCount()
        if count == 0:
            return 0
        half = max(1, self.spacing() // 2)
        if row <= 0:
            return self.visualRect(model.index(0, 0)).top() - half
        if row >= count:
            return self.visualRect(model.index(count - 1, 0)).bottom() + half
        above = self.visualRect(model.index(row - 1, 0)).bottom()
        below = self.visualRect(model.index(row, 0)).top()
        return (above + below) // 2

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drop_target is None:
            return
        y = self.drop_indicator_y(self._drop_target)
        painter = QPainter(self.viewport())
        try:
            painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Highlight), 3))
            painter.drawLine(4, y, self.viewport().width() - 5, y)
        finally:
            painter.end()

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
