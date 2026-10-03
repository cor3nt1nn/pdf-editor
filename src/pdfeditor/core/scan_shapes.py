"""Snapping targets of scanned pages: the rules of a scan found in its pixels (M8).

A scan has no vector paths, so :mod:`pdfeditor.core.snapping` finds nothing to snap to.
This module finds the long thin dark runs of a grey render of the page (100 dpi, see
``PdfDocument.page_shapes``) with ``QtGui`` and ``re`` only (no numpy; docs/M8_PLAN.md
O16–O17):

1. :func:`estimate_skew` — the page's skew (−3°..3°): the angle whose rotation makes the
   per-row counts of dark pixels most uneven (text lines and rules then fall on few
   rows), searched by 0.5° then 0.1° on a copy at most 600 pixels wide.
2. The image is turned back by that angle on a white canvas (``QImage.transformed`` would
   leave black corners) and :func:`detect_rules` collects, per row, the runs of at least
   ``min_len`` dark pixels (one regular expression over the row's bytes), merges runs of
   neighbouring rows into segments and keeps those at most ``max_thick`` thick; vertical
   rules are found the same way on the image turned by 90°.
3. :func:`scan_rules` maps each rule's centre back through the deskew rotation into page
   space and keeps it as an axis-aligned :class:`~pdfeditor.core.snapping.Segment` there
   (a rule of a skewed scan stays within ``length × sin(skew) / 2`` of it).

:func:`scan_shapes` turns the rules (plus the dotted leaders of an OCR result, which have
no drawn rule: :func:`leader_segments`) into :class:`~pdfeditor.core.snapping.PageShapes`:
a checkbox square becomes four short rules and the existing ray-cast cell of
:func:`snapping.snap` finds it as a ``BOX``, table cells as ``CELL``, underlines and
leaders as ``UNDERLINE``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QTransform

from pdfeditor.core.ocr import PageOcr
from pdfeditor.core.snapping import MIN_SEGMENT, PageShapes, Segment

#: Resolution of the grey render the rules are found in.
RENDER_DPI = 100
#: Grey levels at or below this are ink.
DARK = 0xA0
#: Shortest rule (points): checkbox sides (≈ 10 pt) are rules too.
MIN_RULE_PT = 8.0
#: Thickest rule (points).
MAX_THICK_PT = 4.0
#: Skew search: coarse range and step, then fine step around the best coarse angle
#: (degrees), on a copy at most this many pixels wide.
MAX_SKEW = 3.0
COARSE_STEP = 0.5
FINE_STEP = 0.1
SKEW_WIDTH = 600
#: Below this skew (degrees) the image is not turned.
MIN_DESKEW = 0.05
#: Rows of runs this close (pixels) merge into one segment; their ends may differ by this.
MERGE_GAP = 2
MERGE_TOLERANCE = 3
#: Dotted leaders in the pixels: at least ``MIN_DOTS`` dots of at most ``DOT_PT`` points
#: separated by gaps of at most ``DOT_GAP_PT`` (grey ≤ ``DOT_DARK``: small dots are
#: lighter), with no ink ``DOT_CLEAR_PX`` rows above and below (text strokes are taller).
MIN_DOTS = 10
DOT_PT = 2.2
DOT_GAP_PT = 3.6
DOT_DARK = 0xC0
DOT_CLEAR_PX = 3
DOT_CLEAR_INK = 0.05
#: A word of at least this many leader characters is a dotted leader (an UNDERLINE).
MIN_LEADER = 4
LEADER_RE = re.compile(rf"^[._…·\-]{{{MIN_LEADER},}}$")

_INK_TABLE = bytes(1 if v <= DARK else 0 for v in range(256))
_SMOOTH = Qt.TransformationMode.SmoothTransformation


@dataclass(frozen=True)
class ScanRules:
    """Rules found on a scan, page space (points): ``skew`` in degrees (the scan is
    turned clockwise by it), horizontal and vertical segments."""

    skew: float
    h: tuple[Segment, ...]
    v: tuple[Segment, ...]
    #: Dotted leaders ("Nom ..........") as horizontal segments through their dots.
    dotted: tuple[Segment, ...] = ()


def _grey(image: QImage) -> QImage:
    if image.format() != QImage.Format.Format_Grayscale8:
        image = image.convertToFormat(QImage.Format.Format_Grayscale8)
    return image


def rows(image: QImage) -> list[bytes]:
    """The pixel rows of a ``Grayscale8`` image (padding dropped)."""
    image = _grey(image)
    w, h, bpl = image.width(), image.height(), image.bytesPerLine()
    data = bytes(image.constBits())
    return [data[y * bpl : y * bpl + w] for y in range(h)]


def _profile_score(image: QImage) -> float:
    """Variance of the per-row ink counts."""
    counts = [r.translate(_INK_TABLE).count(1) for r in rows(image)]
    n = len(counts) or 1
    mean = sum(counts) / n
    return sum((c - mean) ** 2 for c in counts) / n


def turned(image: QImage, angle: float) -> QImage:
    """``image`` (grey) turned by ``angle`` degrees clockwise about its centre on a white
    canvas of the same size."""
    image = _grey(image)
    w, h = image.width(), image.height()
    out = QImage(w, h, QImage.Format.Format_Grayscale8)
    out.fill(QColor(255, 255, 255))
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setTransform(
        QTransform().translate(w / 2, h / 2).rotate(angle).translate(-w / 2, -h / 2)
    )
    painter.drawImage(0, 0, image)
    painter.end()
    return out


def estimate_skew(image: QImage) -> float:
    """The scan's skew in degrees (the content is turned clockwise by it), within
    ±:data:`MAX_SKEW`, to :data:`FINE_STEP`."""
    image = _grey(image)
    small = image.scaledToWidth(min(image.width(), SKEW_WIDTH), _SMOOTH) if image.width() else image
    if small.width() == 0 or small.height() == 0:
        return 0.0

    def score(angle: float) -> float:
        return _profile_score(turned(small, angle) if angle else small)

    steps = int(round(MAX_SKEW / COARSE_STEP))
    coarse = [k * COARSE_STEP for k in range(-steps, steps + 1)]
    best = max(coarse, key=lambda a: (score(a), -abs(a)))
    fine = [best + k * FINE_STEP for k in range(-4, 5)]
    correction = max(fine, key=lambda a: (score(a), -abs(a)))
    return round(-correction, 2)


def _runs(lines: list[bytes], min_len: int) -> list[tuple[int, int, int]]:
    pattern = re.compile(rb"[\x00-%s]{%d,}" % (bytes([DARK]), max(min_len, 1)))
    out = []
    for y, row in enumerate(lines):
        for m in pattern.finditer(row):
            out.append((y, m.start(), m.end()))
    return out


def _merge(runs: list[tuple[int, int, int]]) -> list[list[int]]:
    """Runs of neighbouring rows overlapping along the row → [y0, y1, x0, x1]."""
    segments: list[list[int]] = []
    open_segments: list[list[int]] = []
    for y, x0, x1 in runs:
        open_segments = [s for s in open_segments if y - s[1] <= MERGE_GAP]
        for s in open_segments:
            if x0 < s[3] + MERGE_TOLERANCE and x1 > s[2] - MERGE_TOLERANCE:
                s[1] = y
                s[2] = min(s[2], x0)
                s[3] = max(s[3], x1)
                break
        else:
            s = [y, y, x0, x1]
            segments.append(s)
            open_segments.append(s)
    return segments


def detect_rules(
    image: QImage,
    scale: float,
    *,
    min_len_pt: float = MIN_RULE_PT,
    max_thick_pt: float = MAX_THICK_PT,
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """Horizontal and vertical rules of a grey ``image`` drawn at ``scale`` pixels per
    point, as ``(start, end, position)`` in the image's points (no deskew)."""
    image = _grey(image)
    min_px = int(math.ceil(min_len_pt * scale))
    thick = max_thick_pt * scale
    h_out = [
        (s[2] / scale, s[3] / scale, (s[0] + s[1] + 1) / 2 / scale)
        for s in _merge(_runs(rows(image), min_px))
        if s[1] - s[0] + 1 <= thick
    ]
    # Turned 90° clockwise: row y of the turned image is column y of the original, and
    # column x of the turned image is row (height − 1 − x) of the original.
    height = image.height()
    side = image.transformed(QTransform().rotate(90))
    v_out = [
        ((height - s[3]) / scale, (height - s[2]) / scale, (s[0] + s[1] + 1) / 2 / scale)
        for s in _merge(_runs(rows(side), min_px))
        if s[1] - s[0] + 1 <= thick
    ]
    return h_out, v_out


def _unturn(x: float, y: float, angle: float, w: float, h: float) -> tuple[float, float]:
    """A point of the image turned by ``angle`` about its centre, back in the original."""
    a = math.radians(-angle)
    dx, dy = x - w / 2, y - h / 2
    return w / 2 + dx * math.cos(a) - dy * math.sin(a), h / 2 + dx * math.sin(a) + dy * math.cos(a)


def scan_rules(image: QImage, scale: float, *, skew: float | None = None) -> ScanRules:
    """Rules of the grey render ``image`` of a page (``scale`` pixels per point) in page
    space, after deskewing it (``skew`` default :func:`estimate_skew`)."""
    image = _grey(image)
    if skew is None:
        skew = estimate_skew(image)
    correction = -skew
    work = turned(image, correction) if abs(skew) >= MIN_DESKEW else image
    h_rules, v_rules = detect_rules(work, scale)
    dotted = detect_dotted(work, scale)
    w, ht = image.width() / scale, image.height() / scale
    if work is image:
        return ScanRules(
            0.0 if abs(skew) < MIN_DESKEW else skew,
            tuple(Segment(a, b, p) for a, b, p in h_rules),
            tuple(Segment(a, b, p) for a, b, p in v_rules),
            tuple(Segment(a, b, p) for a, b, p in dotted),
        )

    def horizontal(rules: list[tuple[float, float, float]]) -> tuple[Segment, ...]:
        out = []
        for a, b, p in rules:
            cx, cy = _unturn((a + b) / 2, p, correction, w, ht)
            out.append(Segment(cx - (b - a) / 2, cx + (b - a) / 2, cy))
        return tuple(out)

    v_out = []
    for a, b, p in v_rules:
        cx, cy = _unturn(p, (a + b) / 2, correction, w, ht)
        v_out.append(Segment(cy - (b - a) / 2, cy + (b - a) / 2, cx))
    return ScanRules(skew, horizontal(h_rules), tuple(v_out), horizontal(dotted))


def detect_dotted(image: QImage, scale: float) -> list[tuple[float, float, float]]:
    """Dotted leaders of a grey, deskewed ``image`` (``scale`` pixels per point) as
    ``(start, end, position)`` in the image's points."""
    lines = rows(image)
    dot = max(1, round(DOT_PT * scale))
    gap = max(1, round(DOT_GAP_PT * scale))
    dark, light = bytes([DOT_DARK]), bytes([DOT_DARK + 1])
    pattern = re.compile(
        rb"(?:[\x00-%s]{1,%d}[%s-\xff]{1,%d}){%d,}[\x00-%s]{1,%d}"
        % (dark, dot, light, gap, MIN_DOTS - 1, dark, dot)
    )
    ink = bytes(1 if v <= DOT_DARK else 0 for v in range(256))
    found = []
    for y, row in enumerate(lines):
        for m in pattern.finditer(row):
            x0, x1 = m.span()
            clear = True
            for yy in (y - DOT_CLEAR_PX, y + DOT_CLEAR_PX):
                if 0 <= yy < len(lines):
                    if lines[yy][x0:x1].translate(ink).count(1) > (x1 - x0) * DOT_CLEAR_INK:
                        clear = False
                        break
            if clear:
                found.append((y, x0, x1))
    return [
        (s[2] / scale, s[3] / scale, (s[0] + s[1] + 1) / 2 / scale)
        for s in _merge(found)
        if s[1] - s[0] + 1 <= MAX_THICK_PT * scale
    ]


def leader_segments(result: PageOcr | None) -> list[Segment]:
    """Dotted leaders ("........") of a left-to-right OCR result as horizontal segments
    along the bottom of their word box."""
    if result is None:
        return []
    out = []
    for line in result.lines:
        if line.dir[0] < 0.99:
            continue
        for word in line.words:
            x0, _y0, x1, y1 = word.rect
            if LEADER_RE.match(word.text) and x1 - x0 >= MIN_SEGMENT:
                out.append(Segment(x0, x1, y1))
    return out


def scan_shapes(
    rules: ScanRules, page_size: tuple[float, float], leaders: list[Segment] = ()
) -> PageShapes:
    """Snapping shapes of a scan: its rules and dotted leaders (clipped to the page) and
    the ``leaders`` of its OCR words."""
    pw, ph = page_size

    def clip(s: Segment, length: float, across: float) -> Segment | None:
        start, end = max(s.start, 0.0), min(s.end, length)
        if end - start < MIN_SEGMENT or not 0.0 <= s.position <= across:
            return None
        return Segment(start, end, s.position)

    h = {c for c in (clip(s, pw, ph) for s in [*rules.h, *rules.dotted, *leaders]) if c}
    v = {c for c in (clip(s, ph, pw) for s in rules.v) if c is not None}
    return PageShapes(h_segments=tuple(sorted(h)), v_segments=tuple(sorted(v)), page_size=(pw, ph))
