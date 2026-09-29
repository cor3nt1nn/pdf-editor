from __future__ import annotations

import time

import pymupdf
import pytest

from pdfeditor.core.document import PdfDocument
from pdfeditor.render.renderer import RenderKind, RenderRequest, RenderWorker


@pytest.fixture
def doc(simple_pdf):
    d = PdfDocument.open(simple_pdf)
    yield d
    d.close()


@pytest.fixture
def worker(doc):
    w = RenderWorker(doc)
    yield w
    w.stop()


def test_rendered_signal(qtbot, worker: RenderWorker) -> None:
    with qtbot.waitSignal(worker.rendered, timeout=5000) as blocker:
        worker.request(1, 1.0, RenderKind.PAGE, priority=0)
    req, image = blocker.args
    assert isinstance(req, RenderRequest)
    assert req.page == 1
    assert (image.width(), image.height()) == (792, 612)


def test_stale_generation_dropped(qtbot, doc, worker: RenderWorker) -> None:
    results: list[RenderRequest] = []
    worker.rendered.connect(lambda req, img: results.append(req))
    with doc.lock:  # block the worker while we queue and then invalidate
        worker.request(0, 1.0)
        worker.request(1, 1.0)
        worker.request(2, 0.2, RenderKind.THUMB, priority=2)
        worker.new_generation(RenderKind.PAGE)
        worker.request(2, 1.0)
    qtbot.waitUntil(lambda: len(results) >= 2, timeout=5000)
    qtbot.wait(100)
    kinds_pages = sorted((r.kind, r.page) for r in results)
    assert kinds_pages == [("page", 2), ("thumb", 2)]
    assert all(r.generation == worker.generation(r.kind) for r in results)


def test_duplicates_coalesced(qtbot, doc, worker: RenderWorker) -> None:
    results: list[RenderRequest] = []
    worker.rendered.connect(lambda req, img: results.append(req))
    with doc.lock:
        assert worker.request(0, 1.0, priority=1) is not None
        assert worker.request(0, 1.0, priority=1) is None
        assert worker.request(0, 1.001, priority=2) is None
        assert worker.request(0, 1.0, priority=0) is not None  # upgrade priority
        assert worker.is_pending(0, 1.0)
    qtbot.waitUntil(lambda: len(results) >= 1, timeout=5000)
    qtbot.wait(100)
    assert len(results) == 1
    assert results[0].priority == 0


def test_priority_order(qtbot, doc, worker: RenderWorker) -> None:
    results: list[int] = []
    worker.rendered.connect(lambda req, img: results.append(req.priority))
    with doc.lock:
        worker.request(0, 0.3, RenderKind.THUMB, priority=2)
        worker.request(1, 0.5, priority=1)
        worker.request(2, 0.5, priority=0)
        qtbot.wait(50)  # worker has taken the first item and is blocked on the lock
    qtbot.waitUntil(lambda: len(results) == 3, timeout=5000)
    assert results[1:] == sorted(results[1:])


def test_stop_is_fast(qtbot, doc) -> None:
    w = RenderWorker(doc)
    for i in range(3):
        w.request(i, 2.0)
    start = time.perf_counter()
    assert w.stop()
    assert time.perf_counter() - start < 1.0
    assert not w.isRunning()
    assert w.request(0, 1.0) is None


def test_stop_never_started(doc) -> None:
    w = RenderWorker(doc)
    assert w.stop()


def test_pymupdf_only_under_lock(qtbot, doc, worker: RenderWorker, monkeypatch) -> None:
    original = pymupdf.Page.get_pixmap
    violations: list[str] = []

    def checked(self, *args, **kwargs):
        if not doc.lock._is_owned():
            violations.append("get_pixmap outside lock")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", checked)
    results = []
    worker.rendered.connect(lambda req, img: results.append(req))
    for i in range(3):
        worker.request(i, 0.5)
    qtbot.waitUntil(lambda: len(results) == 3, timeout=5000)
    assert violations == []


def test_closed_document_is_harmless(qtbot, simple_pdf) -> None:
    d = PdfDocument.open(simple_pdf)
    w = RenderWorker(d)
    with d.lock:
        w.request(0, 1.0)
        d.close()
    qtbot.wait(100)
    assert w.stop()
