"""M8-T3: the OCR worker process (core/ocr_worker.py)."""

from __future__ import annotations

import io
import json
import struct
import subprocess
import sys

import pymupdf
import pytest

from pdfeditor.core import ocr, ocr_worker
from pdfeditor.core.ocr import OcrRequest, PageOcr

WORKER = [sys.executable, "-m", "pdfeditor", "--ocr-worker"]


@pytest.fixture(scope="module")
def request_150(scan_clean) -> OcrRequest:
    with pymupdf.open(str(scan_clean.path)) as doc:
        return ocr.render_request(doc[0], 150)


def _blank(page: int = 7) -> OcrRequest:
    return OcrRequest(page, 20, 30, b"\xff" * (20 * 30 * 3), 20.0, 30.0, 0, 72)


def test_frame_round_trip(request_150) -> None:
    data = ocr_worker.encode_frame(request_150) + ocr_worker.encode_frame(_blank())
    stream = io.BytesIO(data)
    assert ocr_worker.read_frame(stream) == request_150
    assert ocr_worker.read_frame(stream) == _blank()
    assert ocr_worker.read_frame(stream) is None


@pytest.mark.parametrize(
    "data",
    [
        b"XXXX\x05\x00\x00\x00hello",  # bad magic
        ocr_worker.MAGIC + struct.pack("<I", 0),  # empty header
        ocr_worker.MAGIC + struct.pack("<I", 4) + b"nope",  # not JSON
        ocr_worker.MAGIC + struct.pack("<I", 2) + b"{}",  # missing keys
        ocr_worker.encode_frame(_blank())[:-10],  # truncated samples
        ocr_worker.encode_frame(_blank())[:5],  # truncated prefix
    ],
)
def test_bad_frames(data) -> None:
    with pytest.raises(ocr_worker.FrameError):
        ocr_worker.read_frame(io.BytesIO(data))


def test_serve_in_process(request_150) -> None:
    bad_size = OcrRequest(3, 20, 30, b"\xff" * 1800, 0.0, 30.0, 0, 72)  # bad page size
    stdin = io.BytesIO(
        ocr_worker.encode_frame(request_150)
        + ocr_worker.encode_frame(bad_size)
        + ocr_worker.encode_frame(_blank())
    )
    stdout = io.BytesIO()
    assert ocr_worker.serve(stdin, stdout) == ocr_worker.EXIT_OK
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r["page"] for r in replies] == [0, 3, 7]
    assert [r["ok"] for r in replies] == [True, False, True]
    assert replies[1]["reason"] == "input"
    first = PageOcr.from_json(replies[0]["ocr"])
    assert first.dpi == 150 and len(first.words) > 100
    assert PageOcr.from_json(replies[2]["ocr"]).is_empty


def test_serve_stops_on_a_bad_frame(request_150) -> None:
    stdin = io.BytesIO(ocr_worker.encode_frame(_blank()) + b"garbage!" * 3)
    stdout = io.BytesIO()
    assert ocr_worker.serve(stdin, stdout) == ocr_worker.EXIT_BAD_FRAME
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[0]["ok"] is True
    assert replies[1] == {
        "page": None,
        "ok": False,
        "reason": "frame",
        "error": replies[1]["error"],
    }


def test_missing_language_data_is_reported(tmp_path) -> None:
    stdin = io.BytesIO(ocr_worker.encode_frame(_blank()))
    stdout = io.BytesIO()
    assert ocr_worker.serve(stdin, stdout, tessdata=str(tmp_path)) == ocr_worker.EXIT_OK
    reply = json.loads(stdout.getvalue())
    assert reply["ok"] is False and reply["reason"] == "tessdata"


def test_worker_process(request_150) -> None:
    """``python -m pdfeditor --ocr-worker``: two frames → two lines, exit 0 at EOF."""
    data = ocr_worker.encode_frame(request_150) + ocr_worker.encode_frame(_blank())
    done = subprocess.run(WORKER, input=data, capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
    lines = done.stdout.decode("ascii").splitlines()
    assert len(lines) == 2
    replies = [json.loads(line) for line in lines]
    assert replies[0]["ok"] and replies[1]["ok"]
    words = [w.text for w in PageOcr.from_json(replies[0]["ocr"]).words]
    assert "Formulaire" in words and "l'école" in words


def test_worker_process_eof_and_bad_frame() -> None:
    done = subprocess.run(WORKER, input=b"", capture_output=True, timeout=60)
    assert done.returncode == 0 and done.stdout == b""
    done = subprocess.run(WORKER, input=b"not a frame", capture_output=True, timeout=60)
    assert done.returncode == ocr_worker.EXIT_BAD_FRAME
    assert json.loads(done.stdout)["reason"] == "frame"


def test_worker_imports_no_qt() -> None:
    code = (
        "import sys; import pdfeditor.core.ocr_worker, pdfeditor.core.ocr; "
        "ocr = pdfeditor.core.ocr; ocr.check_tessdata(ocr.tessdata_dir()); "
        "print(sorted(m for m in sys.modules if m.startswith('PySide6')))"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == b"[]"


def test_app_main_runs_the_worker(monkeypatch) -> None:
    """The installed ``pdfeditor`` scripts call app.main: it dispatches the flag too."""
    from pdfeditor import app

    called = []
    monkeypatch.setattr(ocr_worker, "main", lambda: called.append(1) or 0)
    assert app.main(["pdfeditor", "--ocr-worker"]) == 0
    assert called == [1]
