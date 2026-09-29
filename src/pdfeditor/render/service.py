"""RenderService: one PixmapCache + one RenderWorker shared by the page view and thumbnails."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QPixmap

from pdfeditor.core.document import PdfDocument
from pdfeditor.render.cache import PixmapCache
from pdfeditor.render.renderer import RenderKind, RenderRequest, RenderWorker


class RenderService(QObject):
    """Main-thread facade: queues renders, converts results to QPixmap and caches them."""

    #: (page index, kind) — a new pixmap is available in the cache
    pixmap_ready = Signal(int, str)

    def __init__(self, parent: QObject | None = None, budget_bytes: int | None = None) -> None:
        super().__init__(parent)
        self.cache = PixmapCache() if budget_bytes is None else PixmapCache(budget_bytes)
        self.worker = RenderWorker()
        self.worker.rendered.connect(self._on_rendered, Qt.ConnectionType.QueuedConnection)
        self._document: PdfDocument | None = None

    @property
    def document(self) -> PdfDocument | None:
        return self._document

    def set_document(self, document: PdfDocument | None) -> None:
        self._document = document
        self.cache.clear()
        self.worker.set_document(document)

    def reset(self) -> None:
        """Drop everything cached and queued (e.g. document reopened after a full save)."""
        self.cache.clear()
        self.worker.new_generation()

    def invalidate_page(self, page: int) -> None:
        """Forget ``page``'s pixmaps; in-flight renders (possibly stale) are discarded."""
        self.cache.invalidate_page(page)
        self.worker.new_generation()

    def pixmap(self, page: int, scale: float, kind: str = RenderKind.PAGE) -> QPixmap | None:
        return self.cache.get(page, scale, kind)

    def best(self, page: int, kind: str = RenderKind.PAGE) -> QPixmap | None:
        return self.cache.best(page, kind)

    def request(
        self, page: int, scale: float, kind: str = RenderKind.PAGE, priority: int = 0
    ) -> None:
        if self._document is None:
            return
        if self.cache.get(page, scale, kind) is not None:
            return
        self.worker.request(page, scale, kind, priority)

    def new_generation(self, kind: str | None = None) -> None:
        self.worker.new_generation(kind)

    def stop(self) -> None:
        self.worker.stop()

    def _on_rendered(self, req: RenderRequest, image: QImage) -> None:
        if req.document is not self._document or self._document is None or not req.document.is_open:
            return
        if req.generation != self.worker.generation(req.kind):
            return  # superseded while the result was in the event queue
        if not 0 <= req.page < self._document.page_count:
            return
        self.cache.put(req.page, req.scale, req.kind, QPixmap.fromImage(image))
        self.pixmap_ready.emit(req.page, req.kind)
