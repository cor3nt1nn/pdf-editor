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
        # (page, kind) -> last pixmap of a page whose content changed but whose size did
        # not: painted as a placeholder until the fresh render arrives (no white flash).
        self._stale: dict[tuple[int, str], QPixmap] = {}

    @property
    def document(self) -> PdfDocument | None:
        return self._document

    def set_document(self, document: PdfDocument | None) -> None:
        self._document = document
        self.cache.clear()
        self._stale.clear()
        self.worker.set_document(document)

    def reset(self) -> None:
        """Drop everything cached and queued (e.g. document reopened after a full save)."""
        self.cache.clear()
        self._stale.clear()
        self.worker.new_generation()

    def remap_pages(self, mapping: list[int | None]) -> None:
        """Pages were added, removed or reordered (``PdfDocument.pages_remapped``): move the
        cached and stale pixmaps of surviving pages to their new index, drop those of
        deleted pages and discard queued renders (they carry the old indexes)."""
        self.cache.remap(mapping)
        stale: dict[tuple[int, str], QPixmap] = {}
        for (page, kind), pixmap in self._stale.items():
            new = mapping[page] if 0 <= page < len(mapping) else None
            if new is not None:
                stale[(new, kind)] = pixmap
        self._stale = stale
        self.worker.new_generation()

    def invalidate_page(self, page: int, keep_stale: bool = False) -> None:
        """Forget ``page``'s pixmaps; in-flight renders (possibly stale) are discarded.

        ``keep_stale``: the page changed content but not size, so its best pixmaps stay
        available through :meth:`best` as placeholders until fresh ones are rendered.
        """
        for kind in (RenderKind.PAGE, RenderKind.THUMB):
            old = self.cache.best(page, kind) if keep_stale else None
            if old is not None:
                self._stale[(page, kind)] = old
            elif not keep_stale:
                self._stale.pop((page, kind), None)
        self.cache.invalidate_page(page)
        self.worker.new_generation()

    def is_stale(self, page: int, kind: str = RenderKind.PAGE) -> bool:
        """True while a stale placeholder of ``page`` waits for its re-render."""
        return (page, kind) in self._stale

    def pixmap(self, page: int, scale: float, kind: str = RenderKind.PAGE) -> QPixmap | None:
        return self.cache.get(page, scale, kind)

    def best(self, page: int, kind: str = RenderKind.PAGE) -> QPixmap | None:
        """Best placeholder for ``page``: any cached scale, else the stale pixmap."""
        return self.cache.best(page, kind) or self._stale.get((page, kind))

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
        self._stale.pop((req.page, req.kind), None)
        self.pixmap_ready.emit(req.page, req.kind)
