"""Snapping of text and stamps to the cells, underlines and checkboxes of flat forms (M3).

:func:`scan_page` (caller holds ``PdfDocument.lock``) extracts the axis-aligned vector
shapes of a page (MuPDF's line-art device, like ``Page.get_cdrawings``, minus the paths
lying inside annotation or widget rects: those are appearances) and the checkbox-like
glyphs of its content text (``☐`` & co., Wingdings/Webdings), in page space (rotation
applied, cropbox-relative, points), clipped to the page. Everything else is pure
geometry on the resulting :class:`PageShapes`: :func:`snap` finds what a click targets,
:func:`text_placement`, :func:`stamp_placement` and :func:`signature_placement` turn a
:class:`Snap` into the page-space rect of a new text box, the centre and size of a new
stamp or the rect of a new signature (M4).

Snap order (:func:`snap`):

1. a checkbox-sized box or glyph box containing the point → ``BOX``;
2. a checkbox-sized box or glyph box within ``tolerance`` → ``BOX`` (nearest);
3. the smaller of the smallest box containing the point and the cell reconstructed by
   casting rays to the nearest horizontal/vertical segments (each of which must run
   along the whole side of the cell; a cell covering ``PAGE_FRAME_RATIO`` of the page
   is a page border and is rejected; a cell bounded only by edges of boxes containing
   the point is the overlap of those boxes and loses to the smallest of them) →
   ``CELL`` (``BOX`` if the result is checkbox-sized);
4. the nearest horizontal segment spanning the point's x with its position within
   ``[y - 4, y + 24]`` → ``UNDERLINE`` (zero-height rect at the segment);
5. ``NONE``.
"""

from __future__ import annotations

import bisect
import logging
import math
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from typing import NamedTuple

import pymupdf
from PySide6.QtCore import QPointF, QRectF, QSizeF

from pdfeditor.core.annotations import BASELINE_RATIO, text_rect

log = logging.getLogger(__name__)

#: (x0, y0, x1, y1) in page space.
Box = tuple[float, float, float, float]

#: A filled rect at most this thick (points) is a rule (segment), not a box.
THIN = 2.5
#: Segments shorter than this (points) are ignored.
MIN_SEGMENT = 6.0
#: Boxes and ray-cast cells covering at least this fraction of the page's width *and*
#: height are page frames or backgrounds: ignored (a frame box's edges included).
PAGE_FRAME_RATIO = 0.75
#: Shapes are clipped to the page grown by this margin (points); shapes entirely outside
#: are dropped (bleed, huge or far-away rules would otherwise flood the spatial indexes).
CLIP_MARGIN = 1.0
#: Paths within an annotation's rect grown by this margin (points) are its appearance.
ANNOT_MARGIN = 1.0
#: Smallest side of a box or reconstructed cell (points).
MIN_BOX_SIDE = 4.0
#: Checkbox size limits (:func:`is_checkbox_size`).
CHECKBOX_MAX_SIDE = 28.0
CHECKBOX_MIN_ASPECT = 0.6
CHECKBOX_MAX_ASPECT = 1.6
#: Visible side of a checkbox glyph = ratio x font size (see :func:`glyph_boxes`).
GLYPH_BOX_RATIO = 0.72
#: Height of a checkbox glyph's centre above the baseline = ratio x font size.
GLYPH_RISE = 0.35
#: Unicode checkbox-like glyphs (☐ ☑ ☒ □ ▢ ◻ ◼ ◯). ■ and ○ are left out: they are far
#: more often bullets than boxes.
CHECKBOX_CHARS = frozenset("☐☑☒□▢◻◼◯")
#: Symbol fonts whose glyphs have no usable Unicode (MuPDF yields "\x00" or PUA codes).
SYMBOL_FONTS = ("wingdings", "webdings")
#: Wingdings box codes (low byte of MuPDF's char, often U+F0xx): o p q r (boxes), x (☒),
#: 0xA8 (◻), 0xFD (☒), 0xFE (☑). A Wingdings char with a known code must be one of these
#: (n = ■ and the round bullets are not boxes); an unknown code ("\x00") is kept.
WINGDINGS_BOX_CODES = frozenset((0x6F, 0x70, 0x71, 0x72, 0x78, 0xA8, 0xFD, 0xFE))
GLYPH_MIN_ASPECT = 0.7
GLYPH_MAX_ASPECT = 1.4
#: Underline search window below/above the click (points).
UNDERLINE_ABOVE = 4.0
UNDERLINE_BELOW = 24.0
#: Slack (points) when testing whether a segment spans a coordinate.
SPAN_SLACK = 1.0
#: Slack (points) when testing that a cell's sides run along the whole cell.
ENCLOSE_SLACK = 2.0
#: Text placement (points / ratios of the font size).
CELL_PAD = 2.0
SHORT_CELL_RATIO = 2.4
#: Helvetica cap height / 2: a free click centres capital letters on the pointer.
CLICK_BASELINE_RATIO = 0.35
MIN_TEXT_WIDTH = 36.0

_EPS = 0.5
_GRID = 32.0


class Segment(NamedTuple):
    """Axis-aligned rule: spans ``start..end`` along its axis at ``position`` across it
    (a horizontal segment spans x = start..end at y = position)."""

    start: float
    end: float
    position: float


class SnapKind(StrEnum):
    NONE = "none"
    BOX = "box"
    CELL = "cell"
    UNDERLINE = "underline"


@dataclass(frozen=True)
class Snap:
    """What a click targets: ``rect`` is in page space (None for ``NONE``; zero height
    for ``UNDERLINE``, at the rule's position)."""

    kind: SnapKind
    rect: QRectF | None = None


@dataclass(frozen=True)
class PageShapes:
    """Snapping targets of one page, in page space."""

    #: Closed axis-aligned rects (stroked rects, 4-line boxes, filled areas).
    boxes: tuple[Box, ...] = ()
    #: Horizontal rules (thin filled rects, lines, edges of stroked rects), sorted.
    h_segments: tuple[Segment, ...] = ()
    #: Vertical rules, sorted.
    v_segments: tuple[Segment, ...] = ()
    #: Visible squares of checkbox glyphs.
    glyph_boxes: tuple[Box, ...] = ()
    #: (width, height) of the page; (0, 0) = unknown (no page-frame test on cells).
    page_size: tuple[float, float] = (0.0, 0.0)

    @cached_property
    def _box_grid(self) -> dict[tuple[int, int], list[Box]]:
        return _grid_boxes(self.boxes + self.glyph_boxes)

    @cached_property
    def _h_columns(self) -> dict[int, tuple[list[float], list[Segment]]]:
        return _columns(self.h_segments)

    @cached_property
    def _v_columns(self) -> dict[int, tuple[list[float], list[Segment]]]:
        return _columns(self.v_segments)


EMPTY_SHAPES = PageShapes()


# -- scanning (pymupdf) ---------------------------------------------------------------
def scan_page(page: pymupdf.Page) -> PageShapes:
    """Shapes of ``page`` in page space. Caller holds the document lock.

    Vector paths come from MuPDF's line-art device run on the page as displayed
    (:func:`_drawings`; the document is not modified); paths inside the rect of an
    annotation or widget are its appearance and skipped (:func:`_annot_rects`). Glyphs
    come from a text page of the page's display list without annotations (already in
    page space). Everything is clipped to the page (``CLIP_MARGIN``).
    """
    page_size = (float(page.rect.width), float(page.rect.height))
    drawings, matrix = _drawings(page)
    annot_rects = _annot_rects(page)
    boxes: set[Box] = set()
    h: set[Segment] = set()
    v: set[Segment] = set()
    for path in drawings:
        if annot_rects and _in_annot(path, matrix, annot_rects):
            continue
        _scan_path(path, matrix, page_size, boxes, h, v)
    glyphs = {g for g in (_clip(b, page_size) for b in _scan_glyphs(page)) if g is not None}
    return PageShapes(
        boxes=tuple(sorted(boxes)),
        h_segments=tuple(sorted(h)),
        v_segments=tuple(sorted(v)),
        glyph_boxes=tuple(sorted(glyphs)),
        page_size=page_size,
    )


def _drawings(page: pymupdf.Page) -> tuple[list[dict], tuple[float, ...] | None]:
    """(paths, matrix mapping them to page space, or None when already in page space).

    ``Page.get_cdrawings()`` temporarily sets /Rotate to 0 on a rotated page, which
    marks the page object as changed (the next save would rewrite it), so PyMuPDF's C++
    helper behind it is called directly on the page as displayed: coordinates are then
    already in page space, but on rotated pages rectangles come out as four ``l`` items
    (see :func:`_loop_rect`). Falls back to ``get_cdrawings()`` if the helper is missing.
    """
    helper = getattr(getattr(pymupdf, "extra", None), "get_cdrawings", None)
    if helper is not None:
        try:
            return list(helper(pymupdf.mupdf.FzPage(page.this), None, None, None)), None
        except Exception:  # pragma: no cover - internal API changed
            log.debug("pymupdf.extra.get_cdrawings failed", exc_info=True)
    rotation = int(page.rotation) % 360  # pragma: no cover - fallback
    return page.get_cdrawings(), tuple(page.rotation_matrix) if rotation else None


def _annot_rects(page: pymupdf.Page) -> list[Box]:
    """Page-space rects (grown by ``ANNOT_MARGIN``) of the page's visible annotations and
    widgets, read from their /Rect (``load_annot`` would be quadratic)."""
    try:
        xrefs = page.annot_xrefs()
    except Exception:
        return []
    if not xrefs:
        return []
    doc = page.parent
    mupdf = pymupdf.mupdf
    mediabox, ctm = mupdf.FzRect(), mupdf.FzMatrix()
    # PDF space -> page space (Page.transformation_matrix ignores the cropbox offset on
    # rotated pages).
    mupdf.pdf_page_transform(pymupdf._as_pdf_page(page.this), mediabox, ctm)
    matrix = pymupdf.Matrix(ctm.a, ctm.b, ctm.c, ctm.d, ctm.e, ctm.f)
    m = ANNOT_MARGIN
    rects: list[Box] = []
    for xref, _subtype, _name in xrefs:
        try:
            flags_kind, flags = doc.xref_get_key(xref, "F")
            if flags_kind == "int" and int(flags) & (2 | 32):  # Hidden, NoView
                continue
            kind, value = doc.xref_get_key(xref, "Rect")
            if kind != "array":
                continue
            nums = [float(n) for n in value.strip("[] ").split()]
            if len(nums) != 4 or not all(math.isfinite(n) for n in nums):
                continue
            r = (pymupdf.Rect(nums) * matrix).normalize()
        except Exception:  # malformed annotation: its appearance counts as content
            continue
        rects.append((r.x0 - m, r.y0 - m, r.x1 + m, r.y1 + m))
    return rects


def _in_annot(path: dict, matrix: tuple[float, ...] | None, annot_rects: list[Box]) -> bool:
    rect = path.get("rect")
    if rect is None:
        return False
    x0, y0, x1, y1 = _map(tuple(rect), matrix)  # type: ignore[arg-type]
    return any(a[0] <= x0 and a[1] <= y0 and x1 <= a[2] and y1 <= a[3] for a in annot_rects)


def _clip(box: Box, page_size: tuple[float, float]) -> Box | None:
    """``box`` clipped to the page grown by ``CLIP_MARGIN``; None if entirely outside."""
    m = CLIP_MARGIN
    x0, y0 = max(box[0], -m), max(box[1], -m)
    x1, y1 = min(box[2], page_size[0] + m), min(box[3], page_size[1] + m)
    if x0 > x1 or y0 > y1:
        return None
    return (x0, y0, x1, y1)


def _loop_rect(items: list) -> Box | None:
    """Bounding rect of a path made of exactly four axis-aligned ``l`` items forming a
    closed loop (how the line-art device reports a rectangle on a rotated page)."""
    if len(items) != 4 or any(item[0] != "l" for item in items):
        return None
    for i, item in enumerate(items):
        (xa, ya), (xb, yb) = item[1], item[2]
        if abs(xa - xb) > _EPS and abs(ya - yb) > _EPS:
            return None  # slanted
        nx, ny = items[(i + 1) % 4][1]
        if abs(xb - nx) > _EPS or abs(yb - ny) > _EPS:
            return None  # not a closed loop
    xs = [item[1][0] for item in items]
    ys = [item[1][1] for item in items]
    return (min(xs), min(ys), max(xs), max(ys))


def _map(rect: Box, matrix: tuple[float, ...] | None) -> Box:
    x0, y0, x1, y1 = rect
    if matrix is None:
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    a, b, c, d, e, f = matrix
    xa, ya = a * x0 + c * y0 + e, b * x0 + d * y0 + f
    xb, yb = a * x1 + c * y1 + e, b * x1 + d * y1 + f
    return (min(xa, xb), min(ya, yb), max(xa, xb), max(ya, yb))


def _round(box: Box) -> Box:
    return tuple(round(v, 2) for v in box)  # type: ignore[return-value]


def _is_white(color: object) -> bool:
    return isinstance(color, tuple | list) and len(color) >= 3 and min(color) >= 0.98


def _scan_path(
    path: dict,
    matrix: tuple[float, ...] | None,
    page_size: tuple[float, float],
    boxes: set[Box],
    h: set[Segment],
    v: set[Segment],
) -> None:
    kind = path.get("type") or ""
    stroked = "s" in kind
    filled = "f" in kind
    if filled and not stroked and _is_white(path.get("fill")):
        return  # invisible (white background or mask)
    items = list(path.get("items", ()))
    loop = _loop_rect(items)
    if loop is not None:
        items = [("re", loop)]
    for item in items:
        op = item[0]
        if op == "re":
            rect: Box | None = tuple(item[1])  # type: ignore[assignment]
        elif op == "qu":
            rect = _axis_rect(item[1])
        elif op == "l":
            if stroked:
                (xa, ya), (xb, yb) = item[1], item[2]
                line = _clip(_map((xa, ya, xb, yb), matrix), page_size)
                if line is not None:
                    _add_rule(line, h, v)
            continue
        else:
            continue
        if rect is None:
            continue
        r = _clip(_map(rect, matrix), page_size)
        if r is None:
            continue
        w, ht = r[2] - r[0], r[3] - r[1]
        if min(w, ht) <= THIN:
            _add_rule(r, h, v)
            continue
        if min(w, ht) < MIN_BOX_SIDE:
            continue
        if w >= PAGE_FRAME_RATIO * page_size[0] and ht >= PAGE_FRAME_RATIO * page_size[1]:
            continue
        boxes.add(r)
        if stroked:
            x0, y0, x1, y1 = r
            if w >= MIN_SEGMENT:
                h.add(Segment(x0, x1, y0))
                h.add(Segment(x0, x1, y1))
            if ht >= MIN_SEGMENT:
                v.add(Segment(y0, y1, x0))
                v.add(Segment(y0, y1, x1))


def _axis_rect(quad: tuple) -> Box | None:
    """Bounding rect of an axis-aligned rectangular quad, else None."""
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    for x, y in zip(xs, ys, strict=True):
        if min(abs(x - x0), abs(x - x1)) > _EPS or min(abs(y - y0), abs(y - y1)) > _EPS:
            return None
    return (x0, y0, x1, y1)


def _add_rule(r: Box, h: set[Segment], v: set[Segment]) -> None:
    """Add the rule covering rect/line ``r`` (normalised) if it is thin and long enough."""
    x0, y0, x1, y1 = r
    w, ht = x1 - x0, y1 - y0
    if ht <= THIN and w >= MIN_SEGMENT:
        h.add(Segment(x0, x1, (y0 + y1) / 2))
    elif w <= THIN and ht >= MIN_SEGMENT:
        v.add(Segment(y0, y1, (x0 + x1) / 2))


def _scan_glyphs(page: pymupdf.Page) -> set[Box]:
    textpage = pymupdf.TextPage(
        page.get_displaylist(annots=False).get_textpage(pymupdf.TEXTFLAGS_RAWDICT)
    )
    return glyph_boxes(textpage.extractRAWDICT())


def glyph_boxes(rawdict: dict) -> set[Box]:
    """Visible squares of the checkbox glyphs of a ``rawdict`` text extraction: a char
    of :data:`CHECKBOX_CHARS`, or any near-square char of a :data:`SYMBOL_FONTS` font.

    Side ``GLYPH_BOX_RATIO`` x font size; centre = middle of the char bbox along the
    writing direction and ``GLYPH_RISE`` x font size above the baseline across it (MuPDF
    reports some fonts' bboxes upright on pages rotated by 180°, so the bbox is not
    trusted across the line).
    """
    glyphs: set[Box] = set()
    for block in rawdict.get("blocks", ()):
        for line in block.get("lines", ()):
            dx, dy = line.get("dir", (1.0, 0.0))
            horizontal = abs(dx) >= abs(dy)
            for span in line.get("spans", ()):
                font = str(span.get("font", "")).lower()
                symbol = any(name in font for name in SYMBOL_FONTS)
                size = float(span.get("size", 0.0))
                if size <= 0:
                    continue
                for char in span.get("chars", ()):
                    x0, y0, x1, y1 = char["bbox"]
                    w, ht = x1 - x0, y1 - y0
                    if char["c"] in CHECKBOX_CHARS:
                        pass
                    elif not (symbol and ht > 0 and GLYPH_MIN_ASPECT <= w / ht <= GLYPH_MAX_ASPECT):
                        continue
                    elif "wingdings" in font and not _wingdings_box(char["c"]):
                        continue
                    half = GLYPH_BOX_RATIO * size / 2
                    ox, oy = char.get("origin", (x0, y1))
                    # "Up" (towards the ascender) is dir rotated by -90° in y-down space.
                    rise = GLYPH_RISE * size
                    if horizontal:
                        cx, cy = (x0 + x1) / 2, oy + math.copysign(rise, -dx)
                    else:
                        cx, cy = ox + math.copysign(rise, dy), (y0 + y1) / 2
                    glyphs.add(_round((cx - half, cy - half, cx + half, cy + half)))
    return glyphs


def _wingdings_box(c: str) -> bool:
    """A Wingdings char may be a box: its code is unknown ("\\x00") or a box code."""
    code = ord(c[0]) if c else 0
    return code == 0 or (code & 0xFF) in WINGDINGS_BOX_CODES


# -- spatial indexes --------------------------------------------------------------------
def _grid_boxes(boxes: tuple[Box, ...]) -> dict[tuple[int, int], list[Box]]:
    grid: dict[tuple[int, int], list[Box]] = {}
    for box in boxes:
        x0, y0, x1, y1 = box
        for gx in range(math.floor(x0 / _GRID), math.floor(x1 / _GRID) + 1):
            for gy in range(math.floor(y0 / _GRID), math.floor(y1 / _GRID) + 1):
                grid.setdefault((gx, gy), []).append(box)
    return grid


def _columns(segments: tuple[Segment, ...]) -> dict[int, tuple[list[float], list[Segment]]]:
    """Segments bucketed by the grid columns they span, each bucket sorted by position."""
    buckets: dict[int, list[Segment]] = {}
    for seg in segments:
        lo = math.floor((seg.start - SPAN_SLACK) / _GRID)
        hi = math.floor((seg.end + SPAN_SLACK) / _GRID)
        for g in range(lo, hi + 1):
            buckets.setdefault(g, []).append(seg)
    out = {}
    for g, segs in buckets.items():
        segs.sort(key=lambda s: s.position)
        out[g] = ([s.position for s in segs], segs)
    return out


def _spanning(
    columns: dict[int, tuple[list[float], list[Segment]]], along: float
) -> tuple[list[float], list[Segment]]:
    return columns.get(math.floor(along / _GRID), ([], []))


def _nearest_before(columns, along: float, across: float) -> Segment | None:
    """The nearest segment spanning ``along`` at or before ``across``."""
    positions, segs = _spanning(columns, along)
    i = bisect.bisect_right(positions, across) - 1
    while i >= 0:
        s = segs[i]
        if s.start - SPAN_SLACK <= along <= s.end + SPAN_SLACK:
            return s
        i -= 1
    return None


def _nearest_after(columns, along: float, across: float) -> Segment | None:
    """The nearest segment spanning ``along`` at or after ``across``."""
    positions, segs = _spanning(columns, along)
    i = bisect.bisect_left(positions, across)
    while i < len(segs):
        s = segs[i]
        if s.start - SPAN_SLACK <= along <= s.end + SPAN_SLACK:
            return s
        i += 1
    return None


# -- pure geometry ---------------------------------------------------------------------
def _checkbox_sized(w: float, h: float) -> bool:
    if w <= 0 or h <= 0:
        return False
    return max(w, h) <= CHECKBOX_MAX_SIDE and CHECKBOX_MIN_ASPECT <= w / h <= CHECKBOX_MAX_ASPECT


def is_checkbox_size(rect: QRectF) -> bool:
    """True for a rect that looks like a checkbox (longest side ≤ 28 pt, aspect 0.6–1.6)."""
    return _checkbox_sized(rect.width(), rect.height())


def _qrect(box: Box) -> QRectF:
    x0, y0, x1, y1 = box
    return QRectF(x0, y0, x1 - x0, y1 - y0)


def _area(box: Box) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def _distance(box: Box, x: float, y: float) -> float:
    dx = max(box[0] - x, 0.0, x - box[2])
    dy = max(box[1] - y, 0.0, y - box[3])
    return math.hypot(dx, dy)


def _nearby_boxes(shapes: PageShapes, x: float, y: float, reach: float) -> set[Box]:
    grid = shapes._box_grid
    found: set[Box] = set()
    for gx in range(math.floor((x - reach) / _GRID), math.floor((x + reach) / _GRID) + 1):
        for gy in range(math.floor((y - reach) / _GRID), math.floor((y + reach) / _GRID) + 1):
            found.update(grid.get((gx, gy), ()))
    return found


def _ray_cell(shapes: PageShapes, x: float, y: float) -> Box | None:
    top = _nearest_before(shapes._h_columns, x, y)
    bottom = _nearest_after(shapes._h_columns, x, y)
    left = _nearest_before(shapes._v_columns, y, x)
    right = _nearest_after(shapes._v_columns, y, x)
    if top is None or bottom is None or left is None or right is None:
        return None
    x0, y0, x1, y1 = left.position, top.position, right.position, bottom.position
    if min(x1 - x0, y1 - y0) < MIN_BOX_SIDE:
        return None
    pw, ph = shapes.page_size
    if pw > 0 and x1 - x0 >= PAGE_FRAME_RATIO * pw and y1 - y0 >= PAGE_FRAME_RATIO * ph:
        return None  # a page border drawn as four rules
    # Each side must run along the whole cell: rays hitting unrelated rules (the edges
    # of two separate boxes, say) do not make a cell.
    for seg, lo, hi in ((top, x0, x1), (bottom, x0, x1), (left, y0, y1), (right, y0, y1)):
        if seg.start > lo + ENCLOSE_SLACK or seg.end < hi - ENCLOSE_SLACK:
            return None
    return (x0, y0, x1, y1)


def _on_edge(box: Box, cell: Box, side: int) -> bool:
    """Side ``side`` (0 left, 1 top, 2 right, 3 bottom) of ``cell`` lies on an edge of
    ``box`` parallel to it."""
    lo, hi = (box[0], box[2]) if side in (0, 2) else (box[1], box[3])
    return abs(cell[side] - lo) <= ENCLOSE_SLACK or abs(cell[side] - hi) <= ENCLOSE_SLACK


def _box_overlap(cell: Box, inside: list[Box]) -> bool:
    """``cell`` is the overlap of drawn boxes containing the point rather than a drawn
    cell: none of them is the cell and every side of it lies on an edge of one of them."""
    if not inside:
        return False
    if any(
        all(abs(a - b) <= ENCLOSE_SLACK for a, b in zip(cell, box, strict=True)) for box in inside
    ):
        return False
    return all(any(_on_edge(box, cell, side) for box in inside) for side in range(4))


def snap(shapes: PageShapes, point: QPointF, *, tolerance: float = 6.0) -> Snap:
    """What a click at ``point`` (page space) targets; see the module docstring."""
    x, y = point.x(), point.y()
    near = _nearby_boxes(shapes, x, y, tolerance)
    inside = [b for b in near if b[0] <= x <= b[2] and b[1] <= y <= b[3]]
    small_inside = [b for b in inside if _checkbox_sized(b[2] - b[0], b[3] - b[1])]
    if small_inside:
        return Snap(SnapKind.BOX, _qrect(min(small_inside, key=_area)))
    close = [
        (_distance(b, x, y), _area(b), b)
        for b in near
        if _checkbox_sized(b[2] - b[0], b[3] - b[1]) and _distance(b, x, y) <= tolerance
    ]
    if close:
        return Snap(SnapKind.BOX, _qrect(min(close)[2]))
    best = min(inside, key=_area) if inside else None
    cell = _ray_cell(shapes, x, y)
    if (
        cell is not None
        and (best is None or _area(cell) < _area(best) - _EPS)
        and not _box_overlap(cell, inside)
    ):
        best = cell
    if best is not None:
        kind = (
            SnapKind.BOX if _checkbox_sized(best[2] - best[0], best[3] - best[1]) else SnapKind.CELL
        )
        return Snap(kind, _qrect(best))
    underline = _underline(shapes, x, y)
    if underline is not None:
        return Snap(
            SnapKind.UNDERLINE,
            QRectF(underline.start, underline.position, underline.end - underline.start, 0.0),
        )
    return Snap(SnapKind.NONE)


def _underline(shapes: PageShapes, x: float, y: float) -> Segment | None:
    positions, segs = _spanning(shapes._h_columns, x)
    lo = bisect.bisect_left(positions, y - UNDERLINE_ABOVE)
    hi = bisect.bisect_right(positions, y + UNDERLINE_BELOW)
    candidates = [s for s in segs[lo:hi] if s.start - SPAN_SLACK <= x <= s.end + SPAN_SLACK]
    if not candidates:
        return None
    return min(candidates, key=lambda s: (abs(s.position - y), s.start))


def text_placement(
    snap_result: Snap,
    click: QPointF,
    font_size: float,
    default_width: float,
    page_width: float,
    page_height: float | None = None,
) -> QRectF:
    """Page-space rect of a new one-line text box (see ``annotations.text_rect``).

    ``BOX``/``CELL``: left = rect.left + 2, width = rect width − 4; the first baseline is
    rect.bottom − 2 for cells shorter than 2.4 × font size, else rect.top + 2 + 0.8 × font
    size. ``UNDERLINE``: left = rect.left + 2, baseline = rule − 2, width = min(default,
    rule end − left). ``NONE``: starts at the click with capitals centred on it, width
    ``default_width`` kept inside the page. Left and right always stay within
    ``[0, page_width]``; given ``page_height``, the box is also shifted vertically to
    stay on the page.
    """
    fs = font_size
    kind, rect = snap_result.kind, snap_result.rect
    if kind in (SnapKind.BOX, SnapKind.CELL) and rect is not None:
        if rect.height() < SHORT_CELL_RATIO * fs:
            baseline = rect.bottom() - CELL_PAD
        else:
            baseline = rect.top() + CELL_PAD + BASELINE_RATIO * fs
        out = _within_width(
            rect.left() + CELL_PAD, rect.right() - CELL_PAD, baseline, fs, page_width
        )
    elif kind is SnapKind.UNDERLINE and rect is not None:
        left = max(rect.left(), 0.0) + CELL_PAD
        right = min(left + default_width, rect.right())
        out = _within_width(left, right, rect.bottom() - CELL_PAD, fs, page_width)
    else:
        left = click.x()
        width = min(default_width, max(page_width - left, min(default_width, MIN_TEXT_WIDTH)))
        if left + width > page_width:
            left = max(0.0, page_width - width)
        out = text_rect(left, click.y() + CLICK_BASELINE_RATIO * fs, width, fs)
    if page_height is not None and out.height() <= page_height:
        out.translate(0.0, max(0.0, -out.top()) - max(0.0, out.bottom() - page_height))
    return out


def _within_width(
    left: float, right: float, baseline: float, fs: float, page_width: float
) -> QRectF:
    left = min(max(left, 0.0), max(page_width - 1.0, 0.0))
    right = min(right, page_width)
    return text_rect(left, baseline, max(right - left, 1.0), fs)


def stamp_placement(
    snap_result: Snap, click: QPointF, default_size: float
) -> tuple[QPointF, float]:
    """(page-space centre, box side) of a new stamp (see ``annotations.stamp_rect``).

    Centred in a ``BOX`` or checkbox-sized ``CELL`` with the box's shorter side;
    otherwise ``default_size`` centred on the click.
    """
    rect = snap_result.rect
    if (
        rect is not None
        and snap_result.kind in (SnapKind.BOX, SnapKind.CELL)
        and is_checkbox_size(rect)
    ):
        return rect.center(), min(rect.width(), rect.height())
    return QPointF(click), default_size


def signature_placement(
    snap_result: Snap,
    click: QPointF,
    default_width: float,
    aspect: float,
    page_size: QSizeF,
) -> QRectF:
    """Page-space rect of a new signature whose width/height ratio is ``aspect``.

    ``CELL``: the largest rect of that aspect fitting the cell minus ``CELL_PAD`` on
    every side, but no wider than ``default_width``, bottom-left aligned in it.
    ``UNDERLINE``: width ``min(default_width, rule length − 2 × CELL_PAD)``, left at the
    rule start + ``CELL_PAD``, bottom at the rule − ``CELL_PAD``. ``BOX`` and ``NONE``:
    ``default_width`` centred on the click. The result is always shrunk to fit the page
    (aspect kept) and shifted onto it.
    """
    aspect = aspect if aspect > 0 and math.isfinite(aspect) else 1.0
    kind, rect = snap_result.kind, snap_result.rect
    width = max(default_width, 1.0)
    out: QRectF | None = None
    if kind is SnapKind.CELL and rect is not None:
        room_w = rect.width() - 2 * CELL_PAD
        room_h = rect.height() - 2 * CELL_PAD
        if room_w > 0 and room_h > 0:
            width = min(room_w, room_h * aspect, width)
            height = width / aspect
            left = rect.left() + CELL_PAD
            bottom = rect.bottom() - CELL_PAD
            out = QRectF(left, bottom - height, width, height)
    elif kind is SnapKind.UNDERLINE and rect is not None:
        room = rect.width() - 2 * CELL_PAD
        if room > 0:
            width = min(width, room)
            height = width / aspect
            left = rect.left() + CELL_PAD
            bottom = rect.bottom() - CELL_PAD
            out = QRectF(left, bottom - height, width, height)
    if out is None:
        height = width / aspect
        out = QRectF(click.x() - width / 2, click.y() - height / 2, width, height)
    return fit_on_page(out, aspect, page_size)


def fit_on_page(rect: QRectF, aspect: float, page_size: QSizeF) -> QRectF:
    """``rect`` shrunk (aspect kept, around its centre) to fit the page, then shifted
    onto it."""
    pw, ph = page_size.width(), page_size.height()
    if pw <= 0 or ph <= 0:
        return rect
    scale = min(1.0, pw / rect.width(), ph / rect.height())
    if scale < 1.0:
        centre = rect.center()
        w = rect.width() * scale
        h = w / aspect
        rect = QRectF(centre.x() - w / 2, centre.y() - h / 2, w, h)
    dx = max(0.0, -rect.left()) - max(0.0, rect.right() - pw)
    dy = max(0.0, -rect.top()) - max(0.0, rect.bottom() - ph)
    return rect.translated(dx, dy)
