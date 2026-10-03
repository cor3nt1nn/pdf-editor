"""Text recognition (OCR) of scanned pages with MuPDF's built-in Tesseract (M8).

No Qt here: the OCR worker process (:mod:`pdfeditor.core.ocr_worker`) imports this module
without starting Qt. Only the Tesseract language files are needed (MuPDF has Tesseract
compiled in): the bundled ``resources/tessdata`` folder (tessdata_fast ``fra`` + ``eng``)
is always passed explicitly; ``TESSDATA_PREFIX`` and ``pymupdf.get_tessdata()`` are never
relied on (docs/M8_PLAN.md O1–O3).

Pipeline: :func:`render_request` (caller holds ``PdfDocument.lock``) renders the page *as
displayed* (rotation applied, annotations left out) to RGB samples at :func:`choose_dpi`
— a grey pixmap yields no words (O4) — then :func:`recognise` (any process, no document)
rebuilds a pixmap from the samples, runs ``Pixmap.pdfocr_tobytes`` and reads the words
back from the one-page PDF it returns. The result is a :class:`PageOcr`: lines of words
with boxes in page space (points, rotation applied, cropbox-relative) for the page's
rotation at recognition time; :meth:`PageOcr.rotated` follows later rotations and
:meth:`PageOcr.rawdict` feeds :meth:`pagetext.PageText.from_rawdict` (in-memory OCR text,
invisible spans).

:func:`is_scanned_page` tells a page that looks like a scan (no text, mostly image, few
vector paths) from a digital one.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

from pdfeditor.core.errors import DocumentError

log = logging.getLogger(__name__)

#: Languages passed to Tesseract (French first: the app's main audience; ``eng`` alone
#: recognises 77 % of the words of a French page, ``fra+eng`` 88 %, docs/M8_PLAN.md O5).
LANGUAGES = "fra+eng"
ENGINE = "tesseract"
#: Recognition resolution limits (dots per inch): scans are read at their native
#: resolution within these bounds (upscaling hurts, O6; above 300 dpi is only slower).
MIN_DPI = 150
MAX_DPI = 300
#: Resolution of pages without an image (vector-outlined text).
DEFAULT_DPI = 300
#: A scanned page: images cover at least this fraction of the page...
SCAN_IMAGE_COVERAGE = 0.5
#: ... and it draws at most this many vector paths (a digital page with outlined text
#: draws hundreds).
MAX_SCAN_DRAWINGS = 20
#: Version of :meth:`PageOcr.to_json`.
JSON_VERSION = 1

#: (x0, y0, x1, y1), page space (points).
Rect = tuple[float, float, float, float]
Point = tuple[float, float]


class OcrError(DocumentError):
    """Text recognition failed or was refused (a :class:`DocumentError`, so the callers
    catching document errors catch it too). ``reason``: ``"tessdata"`` (language data
    missing), ``"permission"`` (the document forbids changing its pages), ``"exists"``
    (the page already has an OCR layer), ``"input"`` (bad samples), ``"failed"``."""

    def __init__(self, message: str, reason: str = "failed") -> None:
        super().__init__(message)
        self.reason = reason


# -- result model -------------------------------------------------------------------------
@dataclass(frozen=True)
class OcrWord:
    """A recognised word and its box (page space)."""

    rect: Rect
    text: str


@dataclass(frozen=True)
class OcrLine:
    """A line of words: its box, the start of its baseline (``origin``) and its unit
    writing direction (``dir``; ``(1, 0)`` = left to right on screen)."""

    rect: Rect
    origin: Point
    words: tuple[OcrWord, ...]
    dir: Point = (1.0, 0.0)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass(frozen=True)
class PageOcr:
    """The words recognised on one page, in page space for the page's ``rotation`` and
    size (``width`` × ``height`` points, as displayed) at recognition time."""

    lines: tuple[OcrLine, ...]
    width: float
    height: float
    rotation: int = 0
    dpi: int = DEFAULT_DPI
    languages: str = LANGUAGES
    engine: str = ENGINE
    #: Seconds spent recognising (diagnostics only; not compared).
    seconds: float = field(default=0.0, compare=False)

    @property
    def words(self) -> tuple[OcrWord, ...]:
        return tuple(w for line in self.lines for w in line.words)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def is_empty(self) -> bool:
        return not any(line.words for line in self.lines)

    # -- serialisation (worker protocol) ------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {
            "version": JSON_VERSION,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "dpi": self.dpi,
            "languages": self.languages,
            "engine": self.engine,
            "seconds": round(self.seconds, 3),
            "lines": [
                {
                    "rect": list(line.rect),
                    "origin": list(line.origin),
                    "dir": list(line.dir),
                    "words": [[*w.rect, w.text] for w in line.words],
                }
                for line in self.lines
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PageOcr:
        """Inverse of :meth:`to_json`; raises ``ValueError`` on a malformed dict."""
        try:
            if int(data.get("version", JSON_VERSION)) != JSON_VERSION:
                raise ValueError(f"unknown OCR result version {data.get('version')}")
            lines = tuple(
                OcrLine(
                    rect=_rect(line["rect"]),
                    origin=_point(line["origin"]),
                    dir=_point(line.get("dir", (1.0, 0.0))),
                    words=tuple(OcrWord(_rect(w[:4]), str(w[4])) for w in line.get("words", ())),
                )
                for line in data.get("lines", ())
            )
            return cls(
                lines=lines,
                width=float(data["width"]),
                height=float(data["height"]),
                rotation=int(data.get("rotation", 0)) % 360,
                dpi=int(data.get("dpi", DEFAULT_DPI)),
                languages=str(data.get("languages", LANGUAGES)),
                engine=str(data.get("engine", ENGINE)),
                seconds=float(data.get("seconds", 0.0)),
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"malformed OCR result: {exc}") from exc

    # -- geometry -------------------------------------------------------------------------
    def rotated(self, rotation: int) -> PageOcr:
        """The same words on the page turned to ``rotation`` (degrees, multiple of 90):
        boxes, origins and directions follow the page (pure geometry)."""
        rotation = int(rotation) % 360
        if rotation % 90:
            raise ValueError(f"rotation must be a multiple of 90: {rotation}")
        quarters = ((rotation - self.rotation) // 90) % 4
        if quarters == 0:
            return self
        w, h = self.width, self.height
        lines = self.lines
        for _ in range(quarters):
            lines = tuple(_turn_line(line, h) for line in lines)
            w, h = h, w
        return PageOcr(lines, w, h, rotation, self.dpi, self.languages, self.engine, self.seconds)

    def rawdict(self) -> dict[str, Any]:
        """An ``extractRAWDICT()``-shaped dict of the words for
        :meth:`pagetext.PageText.from_rawdict`: one block per line, one invisible span
        (``alpha`` 0) per line, a char per letter (the word box split evenly along the
        line) and a space char between words."""
        blocks = []
        for line in self.lines:
            chars = list(_line_chars(line))
            if not chars:
                continue
            size = _line_thickness(line) * 0.85
            span = {
                "font": "OCR",
                "size": size,
                "flags": 0,
                "color": 0,
                "alpha": 0,
                "origin": list(line.origin),
                "ascender": 0.8,
                "descender": -0.2,
                "bbox": list(line.rect),
                "chars": chars,
            }
            blocks.append(
                {
                    "type": 0,
                    "bbox": list(line.rect),
                    "lines": [
                        {
                            "bbox": list(line.rect),
                            "dir": list(line.dir),
                            "wmode": 0,
                            "spans": [span],
                        }
                    ],
                }
            )
        return {"blocks": blocks}


def _rect(values: Iterable[Any]) -> Rect:
    x0, y0, x1, y1 = (float(v) for v in values)
    return (x0, y0, x1, y1)


def _point(values: Iterable[Any]) -> Point:
    x, y = (float(v) for v in values)
    return (x, y)


def _turn_point(p: Point, height: float) -> Point:
    """A point of a page ``height`` points high after a 90° clockwise turn."""
    return (height - p[1], p[0])


def _turn_rect(r: Rect, height: float) -> Rect:
    ax, ay = _turn_point((r[0], r[1]), height)
    bx, by = _turn_point((r[2], r[3]), height)
    return (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))


def _turn_line(line: OcrLine, height: float) -> OcrLine:
    dx, dy = line.dir
    return OcrLine(
        rect=_turn_rect(line.rect, height),
        origin=_turn_point(line.origin, height),
        words=tuple(OcrWord(_turn_rect(w.rect, height), w.text) for w in line.words),
        dir=(-dy + 0.0, dx + 0.0),
    )


def _line_thickness(line: OcrLine) -> float:
    x0, y0, x1, y1 = line.rect
    return (y1 - y0) if abs(line.dir[0]) >= abs(line.dir[1]) else (x1 - x0)


def _line_chars(line: OcrLine) -> Iterable[dict[str, Any]]:
    """rawdict chars of a line: each word's box split evenly into its letters along the
    line direction, a space spanning the gap to the next word."""
    dx, dy = line.dir
    horizontal = abs(dx) >= abs(dy)
    forward = (dx if horizontal else dy) >= 0
    ox, oy = line.origin
    for k, word in enumerate(line.words):
        x0, y0, x1, y1 = word.rect
        n = max(len(word.text), 1)
        for i, c in enumerate(word.text):
            if horizontal:
                step = (x1 - x0) / n
                a = x0 + i * step if forward else x1 - (i + 1) * step
                box = (a, y0, a + step, y1)
                origin = (a if forward else a + step, oy)
            else:
                step = (y1 - y0) / n
                a = y0 + i * step if forward else y1 - (i + 1) * step
                box = (x0, a, x1, a + step)
                origin = (ox, a if forward else a + step)
            yield {"c": c, "bbox": list(box), "origin": list(origin)}
        if k + 1 < len(line.words):
            nx0, ny0, nx1, ny1 = line.words[k + 1].rect
            if horizontal:
                gap = (x1, y0, nx0, y1) if forward else (nx1, y0, x0, y1)
                origin = (gap[0] if forward else gap[2], oy)
            else:
                gap = (x0, y1, x1, ny0) if forward else (x0, ny1, x1, y0)
                origin = (ox, gap[1] if forward else gap[3])
            gx0, gy0, gx1, gy1 = gap
            box = (min(gx0, gx1), min(gy0, gy1), max(gx0, gx1), max(gy0, gy1))
            yield {"c": " ", "bbox": list(box), "origin": list(origin)}


# -- language data ------------------------------------------------------------------------
def tessdata_dir() -> Path:
    """The bundled language data folder (``pdfeditor/resources/tessdata``)."""
    from pdfeditor.resources import tessdata_dir as bundled

    return bundled()


def check_tessdata(tessdata: str | Path, languages: str = LANGUAGES) -> Path:
    """``tessdata`` as a Path after checking that every language file of ``languages``
    is there; raises :class:`OcrError` (``"tessdata"``) otherwise."""
    folder = Path(tessdata)
    missing = [
        lang for lang in languages.split("+") if not (folder / f"{lang}.traineddata").is_file()
    ]
    if missing:
        raise OcrError(
            f"Tesseract language data missing in {folder}: {', '.join(missing)}", "tessdata"
        )
    return folder


# -- rendering (caller holds the document lock) ---------------------------------------------
@dataclass(frozen=True)
class OcrRequest:
    """One page to recognise: RGB samples (``width`` × ``height`` × 3 bytes) of the page
    as displayed at ``dpi``, the page's displayed size in points and rotation."""

    page: int
    width: int
    height: int
    samples: bytes
    page_width: float
    page_height: float
    rotation: int
    dpi: int
    languages: str = LANGUAGES


def choose_dpi(page: pymupdf.Page) -> int:
    """Recognition resolution of ``page``: the native resolution of its largest image,
    within [:data:`MIN_DPI`, :data:`MAX_DPI`]; :data:`DEFAULT_DPI` without an image."""
    best_area = 0.0
    dpi = float(DEFAULT_DPI)
    try:
        infos = image_info(page)
    except Exception:  # MuPDF raises FzError* (not RuntimeError)
        log.warning("could not list the images of page %d", page.number, exc_info=True)
        infos = []
    for info in infos:
        x0, y0, x1, y1 = info.get("bbox", (0, 0, 0, 0))
        area_pt = abs(x1 - x0) * abs(y1 - y0)
        pixels = float(info.get("width", 0)) * float(info.get("height", 0))
        if area_pt <= 1.0 or pixels <= 0 or area_pt <= best_area:
            continue
        best_area = area_pt
        dpi = math.sqrt(pixels / area_pt) * 72.0
    return int(round(min(max(dpi, MIN_DPI), MAX_DPI)))


def render_request(page: pymupdf.Page, dpi: int | None = None) -> OcrRequest:
    """Render ``page`` as displayed (no annotations) to RGB at ``dpi`` (default
    :func:`choose_dpi`). Caller holds the document lock."""
    dpi = int(dpi or choose_dpi(page))
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False, annots=False)
    if pix.n != 3 or pix.stride != pix.width * 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix, 0)
    rect = page.rect
    return OcrRequest(
        page=int(page.number),
        width=int(pix.width),
        height=int(pix.height),
        samples=bytes(pix.samples),
        page_width=float(rect.width),
        page_height=float(rect.height),
        rotation=int(page.rotation) % 360,
        dpi=dpi,
    )


# -- recognition (no document, any process) -------------------------------------------------
def recognise(request: OcrRequest, tessdata: str | Path | None = None) -> PageOcr:
    """Recognise the words of ``request`` (see :func:`ocr_samples`)."""
    return ocr_samples(
        request.samples,
        request.width,
        request.height,
        page_size=(request.page_width, request.page_height),
        rotation=request.rotation,
        dpi=request.dpi,
        languages=request.languages,
        tessdata=tessdata,
    )


def ocr_samples(
    samples: bytes,
    width: int,
    height: int,
    *,
    page_size: tuple[float, float],
    rotation: int = 0,
    dpi: int = DEFAULT_DPI,
    languages: str = LANGUAGES,
    tessdata: str | Path | None = None,
) -> PageOcr:
    """Words of the RGB image ``samples`` (``width`` × ``height``, 3 bytes per pixel, no
    padding) of a page of ``page_size`` points, boxes scaled to points.

    Raises ``ValueError`` for samples that are not RGB of that size (grey images yield no
    words with Tesseract through MuPDF, O4) and :class:`OcrError` when the language data
    is missing or MuPDF fails.
    """
    import time

    if width <= 0 or height <= 0 or len(samples) != width * height * 3:
        raise ValueError(f"OCR needs RGB samples: {len(samples)} bytes for {width}x{height} pixels")
    pw, ph = float(page_size[0]), float(page_size[1])
    if pw <= 0 or ph <= 0:
        raise ValueError(f"bad page size {page_size}")
    folder = check_tessdata(tessdata if tessdata is not None else tessdata_dir(), languages)
    start = time.perf_counter()
    try:
        pix = pymupdf.Pixmap(pymupdf.csRGB, width, height, samples, False)
        pix.set_dpi(72, 72)  # one point per pixel in the OCR document
        data = pix.pdfocr_tobytes(compress=False, language=languages, tessdata=str(folder))
        del pix
        result = pymupdf.open("pdf", data)
    except Exception as exc:  # MuPDF raises FzError* (not RuntimeError)
        raise OcrError(f"text recognition failed: {exc}") from exc
    try:
        flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
        raw = result[0].get_text("rawdict", flags=flags)
    except Exception as exc:
        raise OcrError(f"the recognised text could not be read: {exc}") from exc
    finally:
        result.close()
    lines = tuple(_read_lines(raw, width / pw, height / ph, pw, ph))
    seconds = time.perf_counter() - start
    return PageOcr(lines, pw, ph, int(rotation) % 360, int(dpi), languages, ENGINE, seconds)


def _union(rects: Iterable[Rect]) -> Rect:
    rects = list(rects)
    return (
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    )


def _line_words(ln: dict[str, Any], box: Callable[[Iterable[float]], Rect]) -> list[OcrWord]:
    """Words of a rawdict line: runs of non-space chars (a span boundary ends a word)."""
    words: list[OcrWord] = []
    for span in ln.get("spans", ()):
        run: list[tuple[str, Rect]] = []
        for ch in [*span.get("chars", ()), {"c": " "}]:
            c = str(ch.get("c", ""))
            if c and not c.isspace():
                run.append((c, box(ch["bbox"])))
                continue
            if run:
                r = _union(rect for _, rect in run)
                if r[2] > r[0] and r[3] > r[1]:
                    words.append(OcrWord(r, "".join(c for c, _ in run)))
                run = []
    return words


def _read_lines(
    raw: dict[str, Any], zx: float, zy: float, pw: float, ph: float
) -> Iterable[OcrLine]:
    """OcrLines from the rawdict of the OCR document (pixels → points, clipped to the
    page; words = runs of non-space chars; lines without words dropped)."""

    def box(b: Iterable[float]) -> Rect:
        x0, y0, x1, y1 = b
        return (
            min(max(x0 / zx, 0.0), pw),
            min(max(y0 / zy, 0.0), ph),
            min(max(x1 / zx, 0.0), pw),
            min(max(y1 / zy, 0.0), ph),
        )

    for block in raw.get("blocks", ()):
        if block.get("type", 0) != 0:
            continue
        for ln in block.get("lines", ()):
            words = _line_words(ln, box)
            if not words:
                continue
            rect = _union(w.rect for w in words)
            spans = ln.get("spans", ())
            o = spans[0].get("origin") if spans else None
            origin = (o[0] / zx, o[1] / zy) if o is not None else (rect[0], rect[3])
            d = ln.get("dir", (1.0, 0.0))
            yield OcrLine(rect, origin, tuple(words), (float(d[0]), float(d[1])))


# -- scanned page detection (caller holds the document lock) --------------------------------
def image_info(page: pymupdf.Page) -> list[dict[str, Any]]:
    """``Page.get_image_info()`` of the page's content only. PyMuPDF's own runs the
    annotations too, which makes MuPDF create missing appearance streams: the document
    would then have changes to save after a mere look."""
    textpage = pymupdf.TextPage(
        page.get_displaylist(annots=False).get_textpage(pymupdf.TEXT_PRESERVE_IMAGES)
    )
    return textpage.extractIMGINFO()


def page_has_text(page: pymupdf.Page) -> bool:
    """The page's content (annotations left out) shows at least one non-space char."""
    textpage = pymupdf.TextPage(page.get_displaylist(annots=False).get_textpage(0))
    return bool(textpage.extractText().strip())


def image_coverage(page: pymupdf.Page) -> float:
    """Fraction of the page covered by its images (0..1; overlaps counted once per
    image, so it may overestimate stacked images)."""
    box = page.cropbox
    frame = pymupdf.Rect(0, 0, box.width, box.height)
    area = frame.width * frame.height
    if area <= 0:
        return 0.0
    covered = 0.0
    for info in image_info(page):
        r = pymupdf.Rect(info.get("bbox", (0, 0, 0, 0))).normalize() & frame
        if not r.is_empty:
            covered += r.width * r.height
    return min(covered / area, 1.0)


def is_scanned_page(page: pymupdf.Page) -> bool:
    """``page`` looks like a scan without text: no text, images cover at least
    :data:`SCAN_IMAGE_COVERAGE` of it, at most :data:`MAX_SCAN_DRAWINGS` vector paths.
    Caller holds the document lock."""
    from pdfeditor.core.snapping import vector_paths

    if page_has_text(page):
        return False
    if image_coverage(page) < SCAN_IMAGE_COVERAGE:
        return False
    return len(vector_paths(page)) <= MAX_SCAN_DRAWINGS
