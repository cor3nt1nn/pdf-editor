"""Background page rendering on a dedicated QThread.

The worker only touches PyMuPDF through ``PdfDocument.render`` which takes
``PdfDocument.lock``. Results are delivered via the queued ``rendered`` signal;
the main thread converts them to QPixmap and stores them in the PixmapCache.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
from dataclasses import dataclass, field
from enum import IntEnum

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage

from pdfeditor.core.document import PdfDocument
from pdfeditor.core.geometry import quantize_scale

log = logging.getLogger(__name__)


class RenderKind:
    PAGE = "page"
    THUMB = "thumb"


class Priority(IntEnum):
    VISIBLE = 0
    NEIGHBOR = 1
    THUMB = 2


@dataclass(frozen=True)
class RenderRequest:
    priority: int
    generation: int
    page: int
    scale: float
    kind: str = RenderKind.PAGE
    document: PdfDocument | None = field(default=None, compare=False, repr=False)

    @property
    def key(self) -> tuple[int, float, str]:
        return (self.page, quantize_scale(self.scale), self.kind)


_STOP = object()


class RenderWorker(QThread):
    """Renders pages in priority order; stale generations are dropped."""

    rendered = Signal(object, QImage)  # (RenderRequest, QImage)

    def __init__(self, document: PdfDocument | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._document = document
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._state_lock = threading.Lock()
        self._generations: dict[str, int] = {RenderKind.PAGE: 0, RenderKind.THUMB: 0}
        # key -> (generation, priority) of the live request for that key
        self._pending: dict[tuple[int, float, str], tuple[int, int]] = {}
        self._stopping = False

    # -- main-thread API ----------------------------------------------------
    def generation(self, kind: str = RenderKind.PAGE) -> int:
        return self._generations.get(kind, 0)

    def set_document(self, document: PdfDocument | None) -> None:
        with self._state_lock:
            self._document = document
        self.new_generation()

    def new_generation(self, kind: str | None = None) -> None:
        """Invalidate queued requests of ``kind`` (all kinds if None), e.g. after a zoom
        change (pages only) or a document change (everything)."""
        with self._state_lock:
            kinds = [kind] if kind is not None else list(self._generations)
            for k in kinds:
                self._generations[k] = self._generations.get(k, 0) + 1
            for key in [key for key in self._pending if key[2] in kinds]:
                del self._pending[key]

    def request(self, page: int, scale: float, kind: str = RenderKind.PAGE, priority: int = 0):
        """Queue a render in the current generation. Duplicates are coalesced."""
        with self._state_lock:
            doc = self._document
            if doc is None or self._stopping:
                return None
            gen = self._generations.setdefault(kind, 0)
            req = RenderRequest(int(priority), gen, page, scale, kind, doc)
            live = self._pending.get(req.key)
            if live is not None and live[0] == req.generation and live[1] <= req.priority:
                return None
            self._pending[req.key] = (req.generation, req.priority)
            self._queue.put((req.priority, next(self._seq), req))
        if not self.isRunning():
            self.start()
        return req

    def is_pending(self, page: int, scale: float, kind: str = RenderKind.PAGE) -> bool:
        with self._state_lock:
            return (page, quantize_scale(scale), kind) in self._pending

    def stop(self, timeout_ms: int = 1000) -> bool:
        """Stop the thread and wait for it. Returns True if it finished in time."""
        with self._state_lock:
            self._stopping = True
            self._pending.clear()
        self._queue.put((-1, -1, _STOP))
        if not self.isRunning():
            return True
        return self.wait(timeout_ms)

    # -- worker thread ------------------------------------------------------
    def _take_if_live(self, req: RenderRequest) -> bool:
        with self._state_lock:
            if self._stopping or req.generation != self._generations.get(req.kind):
                return False
            live = self._pending.get(req.key)
            if live != (req.generation, req.priority):
                return False  # superseded by a higher-priority duplicate
            del self._pending[req.key]
            return True

    def run(self) -> None:
        while True:
            _, _, req = self._queue.get()
            if req is _STOP:
                return
            if not self._take_if_live(req):
                continue
            doc = req.document
            if doc is None or not doc.is_open:
                continue
            try:
                image = doc.render(req.page, req.scale)
            except Exception:  # document closed/reopened meanwhile, bad page...
                log.debug("render failed: %r", req, exc_info=True)
                continue
            if req.generation == self._generations.get(req.kind) and not self._stopping:
                self.rendered.emit(req, image)
