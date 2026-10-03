"""The OCR worker process: ``PDFEditor.exe --ocr-worker`` (``python -m pdfeditor
--ocr-worker`` in development), M8.

MuPDF holds the GIL while Tesseract runs (≈0.6 s per page) and while it renders, so text
recognition inside the application would freeze its window (docs/M8_PLAN.md verdict 4).
The application renders each page itself (``PdfDocument.ocr_request``, ≈60 ms) and sends
the samples to this process, which recognises them one by one; no document is opened
here, no Qt is imported (``__main__`` dispatches ``--ocr-worker`` before importing the
application).

Protocol (binary stdin, text stdout):

* request frame = ``MAGIC`` + little-endian ``uint32`` header length + a UTF-8 JSON
  header ``{"page", "width", "height", "page_width", "page_height", "rotation", "dpi",
  "languages"}`` + exactly ``width × height × 3`` bytes of RGB samples;
* one reply line per frame, in order: ``{"page": n, "ok": true, "ocr": PageOcr.to_json()}``
  or ``{"page": n, "ok": false, "reason": "...", "error": "..."}``;
* end of input → exit 0. A malformed frame gets an error reply (``"page": null``) and
  exit 2: the stream cannot be resynchronised. The parent cancels by killing the process.

MuPDF and Tesseract may print to the C-level stdout: the protocol uses a duplicate of the
original stdout descriptor and descriptor 1 is pointed at ``os.devnull``.
"""

from __future__ import annotations

import json
import os
import struct
import sys
from typing import Any, BinaryIO

from pdfeditor.core.ocr import LANGUAGES, OcrError, OcrRequest, recognise

#: Command-line flag of the worker mode.
FLAG = "--ocr-worker"
MAGIC = b"PEOC"
_HEADER = struct.Struct("<4sI")
#: Largest accepted JSON header and image (bytes): a 300 dpi A0 page is ≈ 420 MB.
MAX_HEADER = 64 * 1024
MAX_SAMPLES = 512 * 1024 * 1024
EXIT_OK = 0
EXIT_BAD_FRAME = 2
EXIT_NO_STDIO = 3


class FrameError(ValueError):
    """A request frame is malformed (the stream is out of sync)."""


def encode_frame(request: OcrRequest) -> bytes:
    """The bytes of one request frame."""
    header = json.dumps(
        {
            "page": request.page,
            "width": request.width,
            "height": request.height,
            "page_width": request.page_width,
            "page_height": request.page_height,
            "rotation": request.rotation,
            "dpi": request.dpi,
            "languages": request.languages,
        }
    ).encode("utf-8")
    return _HEADER.pack(MAGIC, len(header)) + header + request.samples


def _read_exactly(stream: BinaryIO, n: int, *, at_start: bool = False) -> bytes | None:
    chunks = []
    remaining = n
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            if at_start and remaining == n:
                return None  # clean end of input
            raise FrameError(f"input ended {remaining} bytes before the end of a frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: BinaryIO) -> OcrRequest | None:
    """The next request of ``stream``, None at the end of input. Raises
    :class:`FrameError`."""
    head = _read_exactly(stream, _HEADER.size, at_start=True)
    if head is None:
        return None
    magic, size = _HEADER.unpack(head)
    if magic != MAGIC:
        raise FrameError(f"bad frame magic {magic!r}")
    if not 0 < size <= MAX_HEADER:
        raise FrameError(f"bad header size {size}")
    try:
        header: dict[str, Any] = json.loads(_read_exactly(stream, size) or b"")
        width, height = int(header["width"]), int(header["height"])
        request = OcrRequest(
            page=int(header.get("page", 0)),
            width=width,
            height=height,
            samples=b"",
            page_width=float(header["page_width"]),
            page_height=float(header["page_height"]),
            rotation=int(header.get("rotation", 0)),
            dpi=int(header.get("dpi", 0)),
            languages=str(header.get("languages", LANGUAGES)),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise FrameError(f"bad frame header: {exc}") from exc
    count = width * height * 3
    if width <= 0 or height <= 0 or count > MAX_SAMPLES:
        raise FrameError(f"bad image size {width}x{height}")
    samples = _read_exactly(stream, count) or b""
    return OcrRequest(
        request.page,
        width,
        height,
        samples,
        request.page_width,
        request.page_height,
        request.rotation,
        request.dpi,
        request.languages,
    )


def _reply(out: BinaryIO, data: dict[str, Any]) -> None:
    out.write(json.dumps(data, ensure_ascii=True).encode("ascii") + b"\n")
    out.flush()


def serve(stdin: BinaryIO, stdout: BinaryIO, *, tessdata: str | None = None) -> int:
    """Answer every frame of ``stdin`` on ``stdout``; returns the exit code."""
    while True:
        try:
            request = read_frame(stdin)
        except FrameError as exc:
            _reply(stdout, {"page": None, "ok": False, "reason": "frame", "error": str(exc)})
            return EXIT_BAD_FRAME
        if request is None:
            return EXIT_OK
        try:
            result = recognise(request, tessdata)
        except OcrError as exc:
            reply = {"page": request.page, "ok": False, "reason": exc.reason, "error": str(exc)}
        except ValueError as exc:
            reply = {"page": request.page, "ok": False, "reason": "input", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - every failure is reported, never fatal
            reply = {"page": request.page, "ok": False, "reason": "failed", "error": repr(exc)}
        else:
            reply = {"page": request.page, "ok": True, "ocr": result.to_json()}
        _reply(stdout, reply)


def main() -> int:
    """Entry point of the worker mode (stdio set up as the module docstring says)."""
    try:
        stdin: BinaryIO = os.fdopen(os.dup(0), "rb")
        stdout: BinaryIO = os.fdopen(os.dup(1), "wb")
    except OSError:
        return EXIT_NO_STDIO
    try:
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, 1)
        os.close(null)
    except OSError:
        pass
    sys.stdout = sys.stderr  # stray Python prints must not reach the protocol
    try:
        import pymupdf

        pymupdf.TOOLS.mupdf_display_errors(False)
        pymupdf.TOOLS.mupdf_display_warnings(False)
    except Exception:  # noqa: BLE001 - cosmetic
        pass
    with stdin, stdout:
        return serve(stdin, stdout)
