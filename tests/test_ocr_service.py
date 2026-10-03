"""M8-T3: OcrService drives the real worker process (python -m pdfeditor --ocr-worker)."""

from __future__ import annotations

import shutil
import sys
import time

import pymupdf
import pytest
from PySide6.QtCore import QElapsedTimer, QTimer
from PySide6.QtGui import QUndoStack

from pdfeditor.core import ocr_service
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.ocr_service import OcrService

TIMEOUT_MS = 30_000


@pytest.fixture
def mixed_doc(mixed_scan, tmp_path):
    path = tmp_path / "mixed.pdf"
    shutil.copyfile(mixed_scan, path)
    doc = PdfDocument.open(path)
    yield doc
    doc.close()


@pytest.fixture
def three_scans(scan_clean, tmp_path):
    """Three copies of the clean scan."""
    path = tmp_path / "three.pdf"
    with pymupdf.open(str(scan_clean.path)) as src:
        out = pymupdf.open()
        for _ in range(3):
            out.insert_pdf(src)
        out.save(str(path))
        out.close()
    doc = PdfDocument.open(path)
    yield doc
    doc.close()


class _Recorder:
    def __init__(self, service: OcrService) -> None:
        self.done: list[int] = []
        self.failed_pages: list[tuple[int, str]] = []
        self.progress: list[tuple[int, int]] = []
        self.finished: list[bool] = []
        self.failed: list[str] = []
        service.page_done.connect(lambda i, _r: self.done.append(i))
        service.page_failed.connect(lambda i, m: self.failed_pages.append((i, m)))
        service.progress.connect(lambda a, b: self.progress.append((a, b)))
        service.finished.connect(self.finished.append)
        service.failed.connect(self.failed.append)


def test_worker_command() -> None:
    assert ocr_service.worker_command() == [sys.executable, "-m", "pdfeditor", "--ocr-worker"]


def test_pages_without_text_made_searchable(qtbot, mixed_doc) -> None:
    service = OcrService()
    seen = _Recorder(service)
    with qtbot.waitSignal(service.finished, timeout=TIMEOUT_MS):
        service.start(mixed_doc, [1], make_searchable=True)
    assert not service.is_running
    assert seen.done == [1] and seen.finished == [False] and seen.failed == []
    assert seen.progress == [(1, 1)]
    assert mixed_doc.has_ocr_layer(1) and not mixed_doc.has_ocr_layer(0)
    assert mixed_doc.page_ocr(1) is not None
    assert "Formulaire" in mixed_doc.page_text(1).text
    command = service.take_command()
    assert command is not None and len(command.pages) == 1
    assert service.take_command() is None  # once
    stack = QUndoStack()
    stack.push(command)
    assert command.error is None and mixed_doc.has_ocr_layer(1)
    stack.undo()
    assert command.error is None and not mixed_doc.has_ocr_layer(1)
    # The recognised words stay in memory.
    assert mixed_doc.page_text(1).source == "ocr"


def test_text_pages_and_memory_only(qtbot, mixed_doc) -> None:
    """A page with content text is recognised but never gets a layer; without
    make_searchable nothing is written."""
    service = OcrService()
    seen = _Recorder(service)
    with qtbot.waitSignal(service.finished, timeout=TIMEOUT_MS):
        service.start(mixed_doc, [0, 1])
    assert seen.done == [0, 1]
    assert mixed_doc.page_ocr(0) is not None and mixed_doc.page_ocr(1) is not None
    assert not mixed_doc.has_ocr_layer(0) and not mixed_doc.has_ocr_layer(1)
    assert service.take_command() is None
    assert mixed_doc.page_text(0).source == "content"
    assert mixed_doc.page_text(1).source == "ocr"
    with qtbot.waitSignal(service.finished, timeout=TIMEOUT_MS):
        service.start(mixed_doc, [0], make_searchable=True)
    assert not mixed_doc.has_ocr_layer(0)
    assert service.take_command() is None


def test_cancel_keeps_pages_done(qtbot, three_scans) -> None:
    service = OcrService()
    seen = _Recorder(service)
    service.start(three_scans, [0, 1, 2], make_searchable=True)
    qtbot.waitUntil(lambda: seen.done == [0], timeout=TIMEOUT_MS)
    timer = QElapsedTimer()
    timer.start()
    service.cancel()
    assert timer.elapsed() <= 1000
    assert seen.finished == [True]
    assert not service.is_running
    assert three_scans.has_ocr_layer(0)
    assert three_scans.page_ocr(2) is None
    command = service.take_command()
    assert command is not None and len(command.pages) == 1
    service.cancel()  # not running: nothing
    assert seen.finished == [True]
    # A new run works after a cancel.
    with qtbot.waitSignal(service.finished, timeout=TIMEOUT_MS):
        service.start(three_scans, [2])
    assert three_scans.page_ocr(2) is not None


def test_deleted_page_is_skipped(qtbot, three_scans) -> None:
    service = OcrService()
    seen = _Recorder(service)
    service.start(three_scans, [0, 1, 2])
    three_scans.delete_pages([2])  # before the worker has started
    with qtbot.waitSignal(service.finished, timeout=TIMEOUT_MS):
        pass
    assert seen.done == [0, 1]
    assert seen.progress[-1] == (3, 3)


def test_moved_page_is_followed(qtbot, three_scans) -> None:
    service = OcrService()
    seen = _Recorder(service)
    ids = three_scans.page_ids()
    service.start(three_scans, [2])
    three_scans.reorder_pages([ids[2], ids[0], ids[1]])
    with qtbot.waitSignal(service.finished, timeout=TIMEOUT_MS):
        pass
    assert seen.done == [0]
    assert three_scans.page_ocr(0) is not None and three_scans.page_ocr(2) is None


def test_gui_thread_stays_responsive(qtbot, three_scans) -> None:
    """The GUI thread only renders (≈60 ms a page at 300 dpi): no event-loop stall
    near the recognition time (≈0.6 s a page)."""
    service = OcrService()
    gaps: list[float] = []
    last = [time.perf_counter()]

    def tick() -> None:
        now = time.perf_counter()
        gaps.append(now - last[0])
        last[0] = now

    ticker = QTimer()
    ticker.setInterval(5)
    ticker.timeout.connect(tick)
    ticker.start()
    with qtbot.waitSignal(service.finished, timeout=TIMEOUT_MS):
        service.start(three_scans, [0, 1, 2], make_searchable=True)
    ticker.stop()
    assert len(gaps) > 20
    assert max(gaps) <= 0.35, f"longest GUI-thread stall {max(gaps):.3f} s"


def test_process_cannot_start(qtbot, mixed_doc, tmp_path) -> None:
    service = OcrService(command=[str(tmp_path / "missing.exe")])
    seen = _Recorder(service)
    with qtbot.waitSignal(service.failed, timeout=TIMEOUT_MS):
        service.start(mixed_doc, [1])
    assert "could not be started" in seen.failed[0]
    assert not service.is_running and seen.finished == []


def test_process_exits_early(qtbot, mixed_doc) -> None:
    service = OcrService(command=[sys.executable, "-c", "pass"])
    seen = _Recorder(service)
    with qtbot.waitSignal(service.failed, timeout=TIMEOUT_MS):
        service.start(mixed_doc, [1])
    assert seen.done == [] and not service.is_running


def test_process_timeout(qtbot, mixed_doc, monkeypatch) -> None:
    monkeypatch.setattr(ocr_service, "PAGE_TIMEOUT_MS", 300)
    service = OcrService(command=[sys.executable, "-c", "import time; time.sleep(30)"])
    seen = _Recorder(service)
    with qtbot.waitSignal(service.failed, timeout=TIMEOUT_MS):
        service.start(mixed_doc, [1])
    assert "did not answer" in seen.failed[0]


def test_start_errors(qtbot, mixed_doc) -> None:
    service = OcrService()
    with pytest.raises(ValueError):
        service.start(mixed_doc, [])
    service.start(mixed_doc, [1])
    with pytest.raises(RuntimeError):
        service.start(mixed_doc, [1])
    service.shutdown()
    assert not service.is_running


def test_closed_document_fails_the_run(qtbot, mixed_doc) -> None:
    service = OcrService()
    seen = _Recorder(service)
    service.start(mixed_doc, [1])
    mixed_doc.close()
    with qtbot.waitSignal(service.failed, timeout=TIMEOUT_MS):
        pass
    assert seen.failed == ["the document was closed"]
