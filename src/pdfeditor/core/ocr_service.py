"""Background text recognition of a document's pages through the OCR worker process (M8).

:class:`OcrService` (``QtCore`` only) drives one ``QProcess`` running
``PDFEditor.exe --ocr-worker`` (:func:`worker_command`; see ``core/ocr_worker.py`` for the
protocol). The GUI thread renders each page (``PdfDocument.ocr_request``, ≈60 ms at
300 dpi, under the document lock) and writes it to the worker; while the worker
recognises page *n* (≈0.6 s), page *n + 1* is rendered, so the window stays responsive
(MuPDF holds the GIL while recognising: a thread would freeze it). Pages are kept by
``PageId``: a page deleted meanwhile is skipped, a moved one is still found.

Each result is stored as it arrives (``PdfDocument.set_page_ocr``; a cancelled or failed
run keeps the pages done) and, with ``make_searchable``, written into the page as an
invisible text layer when the document allows it (``can_modify``) and the page has no
content text yet — through one batch :class:`~pdfeditor.core.commands.AddOcrLayerCommand`
("Recognise text") that the caller pushes once the run is over (:meth:`take_command`).

Signals: ``page_done(index, PageOcr)``, ``page_failed(index, message)`` (that page only),
``progress(done, total)``, ``finished(cancelled)`` (normal end or :meth:`cancel`) and
``failed(message)`` (the worker could not start, died or timed out; the run is over).
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from pdfeditor.core.ocr import OcrError, OcrRequest, PageOcr
from pdfeditor.core.ocr_worker import FLAG, encode_frame

if TYPE_CHECKING:
    from pdfeditor.core.commands import AddOcrLayerCommand
    from pdfeditor.core.document import PageId, PdfDocument

log = logging.getLogger(__name__)

#: The worker process must start within this time...
START_TIMEOUT_MS = 15_000
#: ... and answer each page within this one (its own start-up included for the first).
PAGE_TIMEOUT_MS = 60_000
#: How long :meth:`OcrService.cancel` waits for the killed worker.
KILL_WAIT_MS = 1_000


def worker_command() -> list[str]:
    """The worker's command line: ``PDFEditor.exe --ocr-worker`` when frozen, else this
    Python running ``-m pdfeditor --ocr-worker``."""
    if getattr(sys, "frozen", False):
        return [sys.executable, FLAG]
    return [sys.executable, "-m", "pdfeditor", FLAG]


class OcrService(QObject):
    """Recognises pages of one document at a time in the worker process."""

    page_done = Signal(int, object)
    page_failed = Signal(int, str)
    progress = Signal(int, int)
    finished = Signal(bool)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None, *, command: list[str] | None = None):
        super().__init__(parent)
        self._command = list(command) if command else None
        self._process: QProcess | None = None
        self._doc: PdfDocument | None = None
        self._queue: list[PageId] = []
        self._prepared: tuple[PageId, OcrRequest] | None = None
        self._in_flight: PageId | None = None
        self._buffer = b""
        self._total = 0
        self._done = 0
        self._running = False
        self._make_searchable = False
        self._batch: AddOcrLayerCommand | None = None
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_timeout)

    # -- state -----------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def document(self) -> PdfDocument | None:
        return self._doc if self._running else None

    @property
    def total(self) -> int:
        return self._total

    @property
    def done(self) -> int:
        return self._done

    def take_command(self) -> AddOcrLayerCommand | None:
        """The batch command of the last run's layers (None when no layer was written),
        once: the caller pushes it (it is applied already)."""
        batch, self._batch = self._batch, None
        return batch if batch is not None and batch.pages else None

    # -- control ---------------------------------------------------------------------
    def start(
        self, doc: PdfDocument, pages: Sequence[int], *, make_searchable: bool = False
    ) -> None:
        """Recognise ``pages`` (indexes, in that order) of ``doc``. ``make_searchable``
        also writes the text into pages without text when ``doc.can_modify``. Raises
        ``RuntimeError`` while a run is in progress and ``ValueError`` without pages."""
        if self._running:
            raise RuntimeError("a text recognition is already running")
        ids = [doc.page_id(i) for i in dict.fromkeys(int(p) for p in pages)]
        if not ids:
            raise ValueError("no pages to recognise")
        self._reap()
        self._doc = doc
        self._queue = ids
        self._prepared = None
        self._in_flight = None
        self._buffer = b""
        self._total = len(ids)
        self._done = 0
        self._make_searchable = bool(make_searchable)
        self._batch = None
        self._running = True
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(self._on_output)
        process.readyReadStandardError.connect(self._on_stderr)
        process.errorOccurred.connect(self._on_process_error)
        process.finished.connect(self._on_process_finished)
        process.started.connect(self._send_next)
        self._process = process
        command = self._command or worker_command()
        log.info("text recognition of %d pages: %s", len(ids), command)
        self._watchdog.start(START_TIMEOUT_MS)
        process.start(command[0], command[1:])

    def cancel(self) -> None:
        """Stop the run now (the worker is killed); pages done stay recognised. Emits
        ``finished(True)``. Nothing happens when no run is in progress."""
        if not self._running:
            return
        log.info("text recognition cancelled after %d of %d pages", self._done, self._total)
        self._stop()
        self.finished.emit(True)

    def shutdown(self) -> None:
        """Kill the worker without signals (application exit)."""
        self._stop()

    # -- internals -------------------------------------------------------------------
    def _stop(self) -> None:
        self._running = False
        self._watchdog.stop()
        self._queue = []
        self._prepared = None
        self._in_flight = None
        self._reap()

    def _reap(self) -> None:
        """Kill and forget the previous process (its late signals are ignored)."""
        process, self._process = self._process, None
        if process is None:
            return
        for signal in (
            process.readyReadStandardOutput,
            process.readyReadStandardError,
            process.errorOccurred,
            process.finished,
            process.started,
        ):
            signal.disconnect()
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(KILL_WAIT_MS)
        process.deleteLater()

    def _fail(self, message: str) -> None:
        log.warning("text recognition failed: %s", message)
        self._stop()
        self.failed.emit(message)

    def _index(self, pid: PageId) -> int | None:
        doc = self._doc
        if doc is None or not doc.is_open:
            return None
        return doc.page_index(pid)

    def _render(self, pid: PageId) -> OcrRequest | None:
        """Page ``pid`` rendered for the worker; None (reported) when it is gone or
        cannot be rendered."""
        index = self._index(pid)
        if index is None:
            self._count_page()
            return None
        assert self._doc is not None
        try:
            return self._doc.ocr_request(index)
        except OcrError as exc:
            self.page_failed.emit(index, str(exc))
            self._count_page()
            return None

    def _count_page(self) -> None:
        self._done += 1
        self.progress.emit(self._done, self._total)

    def _send_next(self) -> None:
        """Write the next page to the worker (or end the run when none is left)."""
        if not self._running or self._in_flight is not None or self._process is None:
            return
        doc = self._doc
        if doc is None or not doc.is_open:
            self._fail("the document was closed")
            return
        while True:
            if self._prepared is not None:
                pid, request = self._prepared
                self._prepared = None
                break
            if not self._queue:
                self._process.closeWriteChannel()
                self._running = False
                self._watchdog.stop()
                log.info("text recognition done: %d pages", self._total)
                self.finished.emit(False)
                return
            pid = self._queue.pop(0)
            prepared = self._render(pid)
            if prepared is not None:
                request = prepared
                break
            if not self._running:
                return
        self._in_flight = pid
        self._process.write(encode_frame(request))
        self._watchdog.start(PAGE_TIMEOUT_MS)
        QTimer.singleShot(0, self._prepare_next)

    def _prepare_next(self) -> None:
        """Render the page after the one the worker is reading (overlaps the two)."""
        while self._running and self._prepared is None and self._queue:
            pid = self._queue.pop(0)
            request = self._render(pid)
            if request is not None:
                self._prepared = (pid, request)

    def _on_output(self) -> None:
        if self._process is None:
            return
        self._buffer += bytes(self._process.readAllStandardOutput().data())
        while b"\n" in self._buffer and self._running:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if line.strip():
                self._on_reply(line)

    def _on_reply(self, line: bytes) -> None:
        pid, self._in_flight = self._in_flight, None
        try:
            reply = json.loads(line)
        except ValueError:
            self._fail(f"unreadable reply from the text recognition process: {line[:80]!r}")
            return
        if pid is None:
            self._fail(f"unexpected reply: {reply.get('error') or reply}")
            return
        index = self._index(pid)
        if index is not None:
            if reply.get("ok"):
                try:
                    result = PageOcr.from_json(reply["ocr"])
                except (KeyError, ValueError) as exc:
                    self.page_failed.emit(index, f"malformed result: {exc}")
                else:
                    self._store(index, result)
            else:
                self.page_failed.emit(index, str(reply.get("error", "unknown error")))
        self._count_page()
        self._send_next()

    def _store(self, index: int, result: PageOcr) -> None:
        doc = self._doc
        assert doc is not None
        doc.set_page_ocr(index, result)
        if self._make_searchable and doc.can_modify and not result.is_empty:
            try:
                if not doc.has_content_text(index) and not doc.has_ocr_layer(index):
                    self._layer(index, result)
            except OcrError as exc:
                self.page_failed.emit(index, str(exc))
        self.page_done.emit(index, result)

    def _layer(self, index: int, result: PageOcr) -> None:
        from pdfeditor.core.commands import AddOcrLayerCommand

        doc = self._doc
        assert doc is not None
        if self._batch is None:
            self._batch = AddOcrLayerCommand(doc)
        self._batch.add_page(index, result)

    def _on_stderr(self) -> None:
        if self._process is not None:
            text = bytes(self._process.readAllStandardError().data()).decode("utf-8", "replace")
            for line in text.splitlines():
                if line.strip():
                    log.debug("ocr worker: %s", line)

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if not self._running:
            return
        if error is QProcess.ProcessError.FailedToStart:
            self._fail("the text recognition process could not be started")
        elif error is QProcess.ProcessError.Crashed:
            self._fail("the text recognition process crashed")
        elif error is QProcess.ProcessError.WriteError:
            self._fail("the text recognition process stopped reading")

    def _on_process_finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        if self._running:
            self._fail(f"the text recognition process exited (code {code})")

    def _on_timeout(self) -> None:
        if self._running:
            self._fail("the text recognition process did not answer in time")
