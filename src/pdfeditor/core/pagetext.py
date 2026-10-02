"""Selectable text of a page: characters, spans, lines and blocks in page space (M6b).

Shared by text selection and markup (M6b), editing existing text (M7) and OCR (M8).

:func:`extract_page_text` (caller holds ``PdfDocument.lock``) reads MuPDF's raw text
dictionary of the page's display list **without annotations** (FreeText annotations and
form-field values are not page text) in content order, plus ``get_fonts(full=True)`` to
join every span to its font object and ``get_bboxlog()`` for the areas of invisible
(render mode 3, e.g. OCR) text. Everything is turned into an immutable
:class:`PageText` by :meth:`PageText.from_rawdict`, which also accepts a hand-made
rawdict-shaped dict (OCR results). Geometry is page space (rotation applied,
cropbox-relative, points) as ``QRectF``/``QPointF``; queries are pure geometry and never
touch MuPDF, so ``PdfDocument.page_text(i)`` can be used without the lock once cached.

Characters are addressed by :class:`CharRef` (flat index in content order + flat line
index). Selection ranges are inclusive and order-agnostic: ``range_quads(a, b)`` and
``chars_between(a, b)`` accept ``a`` after ``b``. Invisible text is selectable like any
other; :meth:`PageText.is_invisible` tells it apart (M7 refuses editing it).
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any, NamedTuple

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPolygonF

log = logging.getLogger(__name__)

#: Default reach of :meth:`PageText.hit` (points): a point this far from a line (above,
#: below or past its ends) still hits it.
HIT_TOLERANCE = 6.0

#: rawdict block type of text blocks (1 = image).
_TEXT_BLOCK = 0
_IMAGE_BLOCK = 1
#: Subset prefix of embedded font names ("ABCDEF+Name").
_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")
#: Characters dropped by the normalised font-name comparison.
_NAME_NOISE_RE = re.compile(r"[\s,\-_]+")
_FILL_RULE = Qt.FillRule.OddEvenFill
#: Suffixes stripped by the normalised font-name comparison (after lower-casing).
_NAME_SUFFIXES = ("regular", "mt", "ps")


# -- model -------------------------------------------------------------------------------
@dataclass(frozen=True, eq=False)
class Char:
    """One character: its text (``c``), baseline origin and bounding box."""

    c: str
    origin: QPointF
    bbox: QRectF


@dataclass(frozen=True, eq=False)
class Span:
    """A run of characters sharing font, size, flags and colour.

    ``font_xref``/``resource_name`` identify the font object and its name in the page's
    resources (``0``/``""`` when it could not be matched, see :func:`match_font`).
    ``invisible`` is true for text that is not painted (render mode 3, alpha 0).
    """

    font: str
    size: float
    flags: int
    color: int
    origin: QPointF
    ascender: float
    descender: float
    bbox: QRectF
    chars: tuple[Char, ...]
    font_xref: int = 0
    resource_name: str = ""
    invisible: bool = False

    @property
    def text(self) -> str:
        return "".join(ch.c for ch in self.chars)


@dataclass(frozen=True, eq=False)
class Line:
    """A line of spans; ``dir`` is the unit writing direction in page space."""

    bbox: QRectF
    dir: tuple[float, float]
    spans: tuple[Span, ...]
    wmode: int = 0

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass(frozen=True, eq=False)
class Block:
    """A text block (rawdict type 0)."""

    bbox: QRectF
    lines: tuple[Line, ...]


@dataclass(frozen=True, order=True)
class CharRef:
    """A character of a :class:`PageText`: flat ``index`` in content order and the flat
    index of its ``line`` (in :attr:`PageText.lines`). Ordered by ``index``."""

    index: int
    line: int


class Quad(NamedTuple):
    """A quadrilateral in page space (PyMuPDF's corner naming: upper-left, upper-right,
    lower-left, lower-right relative to the writing direction)."""

    ul: QPointF
    ur: QPointF
    ll: QPointF
    lr: QPointF

    def polygon(self) -> QPolygonF:
        """Closed outline ul → ur → lr → ll."""
        return QPolygonF([self.ul, self.ur, self.lr, self.ll])

    def bounding_rect(self) -> QRectF:
        xs = (self.ul.x(), self.ur.x(), self.ll.x(), self.lr.x())
        ys = (self.ul.y(), self.ur.y(), self.ll.y(), self.lr.y())
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    @staticmethod
    def from_rect(rect: QRectF) -> Quad:
        return Quad(rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight())


class _LineGeom(NamedTuple):
    """Line-local frame of a line: unit direction ``d`` and normal ``n`` (page space),
    the line quad's extent along ``d`` and ``n``, and every char's extent along ``d``."""

    d: tuple[float, float]
    n: tuple[float, float]
    along: tuple[float, float]
    across: tuple[float, float]
    chars: tuple[tuple[float, float], ...]


@dataclass(frozen=True, eq=False)
class PageText:
    """Text of one page (immutable; derived indexes are computed on first use)."""

    blocks: tuple[Block, ...] = ()
    #: Areas of invisible text (bboxlog "ignore-text"), page space.
    invisible_rects: tuple[QRectF, ...] = ()
    #: Areas of images (rawdict image blocks), page space.
    image_rects: tuple[QRectF, ...] = ()

    # -- construction --------------------------------------------------------------------
    @classmethod
    def from_rawdict(
        cls,
        rawdict: dict[str, Any],
        *,
        fonts: Sequence[Sequence[Any]] = (),
        bboxlog: Iterable[tuple[str, Any]] = (),
        rotation_matrix: Any = None,
    ) -> PageText:
        """Build from a ``extractRAWDICT()``-shaped dict (page space).

        ``fonts`` are ``page.get_fonts(full=True)`` entries for the font join;
        ``bboxlog`` are ``page.get_bboxlog()`` entries (unrotated coordinates, mapped to
        page space with ``rotation_matrix`` = ``page.rotation_matrix`` when given). Missing
        keys get neutral defaults, so a hand-made dict with only ``blocks → lines → spans
        → chars (c, bbox)`` works.
        """
        font_memo: dict[str, tuple[int, str]] = {}
        blocks: list[Block] = []
        images: list[QRectF] = []
        for b in rawdict.get("blocks", ()):
            btype = b.get("type", _TEXT_BLOCK)
            if btype == _IMAGE_BLOCK:
                images.append(_qrect(b["bbox"]))
                continue
            if btype != _TEXT_BLOCK:
                continue
            lines: list[Line] = []
            for ln in b.get("lines", ()):
                spans = tuple(_span(s, fonts, font_memo) for s in ln.get("spans", ()))
                spans = tuple(s for s in spans if s.chars)
                if not spans:
                    continue
                d = ln.get("dir", (1.0, 0.0))
                bbox = _qrect(ln["bbox"]) if "bbox" in ln else _union(s.bbox for s in spans)
                lines.append(Line(bbox, (float(d[0]), float(d[1])), spans, int(ln.get("wmode", 0))))
            if lines:
                bbox = _qrect(b["bbox"]) if "bbox" in b else _union(ln.bbox for ln in lines)
                blocks.append(Block(bbox, tuple(lines)))
        invisible: list[QRectF] = []
        for entry in bboxlog:
            kind, rect = entry[0], entry[1]
            if kind != "ignore-text":
                continue
            r = pymupdf.Rect(rect)
            if rotation_matrix is not None:
                r = (r * rotation_matrix).normalize()
            invisible.append(QRectF(r.x0, r.y0, r.width, r.height))
        return cls(tuple(blocks), tuple(invisible), tuple(images))

    # -- flat indexes --------------------------------------------------------------------
    @cached_property
    def lines(self) -> tuple[Line, ...]:
        """Every line, in content order."""
        return tuple(ln for b in self.blocks for ln in b.lines)

    @cached_property
    def _flat(self) -> tuple[tuple[Char, ...], tuple[Span, ...], tuple[int, ...], tuple[int, ...]]:
        chars: list[Char] = []
        spans: list[Span] = []
        char_line: list[int] = []
        starts: list[int] = []
        for k, ln in enumerate(self.lines):
            starts.append(len(chars))
            for s in ln.spans:
                for ch in s.chars:
                    chars.append(ch)
                    spans.append(s)
                    char_line.append(k)
        starts.append(len(chars))
        return tuple(chars), tuple(spans), tuple(char_line), tuple(starts)

    @property
    def chars(self) -> tuple[Char, ...]:
        """Every character, in content order (index = ``CharRef.index``)."""
        return self._flat[0]

    @property
    def is_empty(self) -> bool:
        """No character at all (scanned page, blank page)."""
        return not self.chars

    @property
    def text(self) -> str:
        """All text, lines separated by ``\\n``."""
        return "\n".join(ln.text for ln in self.lines)

    def ref(self, index: int) -> CharRef:
        """The :class:`CharRef` of flat char ``index`` (negative counts from the end)."""
        chars, _, char_line, _ = self._flat
        if index < 0:
            index += len(chars)
        if not 0 <= index < len(chars):
            raise IndexError(index)
        return CharRef(index, char_line[index])

    def char(self, ref: CharRef | int) -> Char:
        return self.chars[_index(ref)]

    def span_of(self, ref: CharRef | int) -> Span:
        return self._flat[1][_index(ref)]

    def line_of(self, ref: CharRef | int) -> Line:
        return self.lines[self._flat[2][_index(ref)]]

    def line_range(self, ref: CharRef | int) -> tuple[CharRef, CharRef]:
        """First and last chars of the line holding ``ref``."""
        _, _, char_line, starts = self._flat
        k = char_line[_index(ref)]
        return CharRef(starts[k], k), CharRef(starts[k + 1] - 1, k)

    def word_range(self, ref: CharRef | int) -> tuple[CharRef, CharRef]:
        """First and last chars of the word holding ``ref`` (a run of non-whitespace chars
        of one line); a whitespace char is its own range."""
        chars, _, char_line, starts = self._flat
        i = _index(ref)
        k = char_line[i]
        if chars[i].c.isspace():
            return CharRef(i, k), CharRef(i, k)
        a = i
        while a > starts[k] and not chars[a - 1].c.isspace():
            a -= 1
        b = i
        while b < starts[k + 1] - 1 and not chars[b + 1].c.isspace():
            b += 1
        return CharRef(a, k), CharRef(b, k)

    def chars_between(self, a: CharRef | int, b: CharRef | int) -> list[CharRef]:
        """Chars from ``a`` to ``b`` inclusive, in content order (either order)."""
        i, j = sorted((_index(a), _index(b)))
        char_line = self._flat[2]
        return [CharRef(k, char_line[k]) for k in range(i, j + 1)]

    # -- geometry ------------------------------------------------------------------------
    @cached_property
    def _geoms(self) -> tuple[_LineGeom, ...]:
        return tuple(_line_geom(ln) for ln in self.lines)

    def _char_quad(self, i: int) -> Quad:
        chars, spans, char_line, _ = self._flat
        return _char_quad(self.lines[char_line[i]].dir, spans[i], chars[i].bbox)

    def _nearest_line(self, point: QPointF, tolerance: float) -> int | None:
        px, py = point.x(), point.y()
        best: tuple[float, float] | None = None
        best_k: int | None = None
        for k, g in enumerate(self._geoms):
            if not g.chars:
                continue
            a = px * g.d[0] + py * g.d[1]
            c = px * g.n[0] + py * g.n[1]
            da = _outside(a, g.along)
            dc = _outside(c, g.across)
            key = (math.hypot(da, dc), abs(c - (g.across[0] + g.across[1]) / 2.0))
            if best is None or key < best:
                best, best_k = key, k
        if best is None or best[0] > tolerance:
            return None
        return best_k

    def hit(self, point: QPointF, tolerance: float = HIT_TOLERANCE) -> CharRef | None:
        """The char at ``point``: on the nearest line within ``tolerance`` points (a point
        inside several lines' areas takes the line whose middle is closest), the char whose
        extent along the line contains the point, else the nearest one. ``None`` when no
        line is that close (use ``math.inf`` to always get the nearest char, e.g. while
        dragging)."""
        k = self._nearest_line(point, tolerance)
        if k is None:
            return None
        g = self._geoms[k]
        a = point.x() * g.d[0] + point.y() * g.d[1]
        start = self._flat[3][k]
        best_i, best_d = 0, math.inf
        for i, extent in enumerate(g.chars):
            dist = _outside(a, extent)
            if dist == 0.0:
                best_i = i
                break
            if dist < best_d:
                best_i, best_d = i, dist
        return CharRef(start + best_i, k)

    def line_at(self, point: QPointF, tolerance: float = 0.0) -> Line | None:
        """The line whose area (within ``tolerance``) holds ``point``."""
        k = self._nearest_line(point, tolerance)
        return None if k is None else self.lines[k]

    def span_at(self, point: QPointF) -> Span | None:
        """The span of the char whose box holds ``point``."""
        ref = self.hit(point, 0.0)
        if ref is None:
            return None
        if not self._char_quad(ref.index).polygon().containsPoint(point, _FILL_RULE):
            return None
        return self.span_of(ref)

    def word_at(self, point: QPointF) -> tuple[CharRef, CharRef] | None:
        """First and last chars of the word at ``point`` (within ``HIT_TOLERANCE``)."""
        ref = self.hit(point)
        return None if ref is None else self.word_range(ref)

    def chars_in_rect(self, area: QRectF | Quad) -> list[CharRef]:
        """Chars whose box centre lies in ``area`` (a rect or a quad), content order."""
        chars, _, char_line, _ = self._flat
        if isinstance(area, Quad):
            poly = area.polygon()
            return [
                CharRef(i, char_line[i])
                for i, ch in enumerate(chars)
                if poly.containsPoint(ch.bbox.center(), _FILL_RULE)
            ]
        r = area.normalized()
        out = []
        for i, ch in enumerate(chars):
            c = ch.bbox.center()
            if r.left() <= c.x() <= r.right() and r.top() <= c.y() <= r.bottom():
                out.append(CharRef(i, char_line[i]))
        return out

    def range_quads(self, a: CharRef | int, b: CharRef | int) -> list[Quad]:
        """One quad per line covering chars ``a``..``b`` inclusive (either order): from
        the first char's upper/lower-left corner to the last char's upper/lower-right
        corner (non-rectangular for slanted text)."""
        i, j = sorted((_index(a), _index(b)))
        if not self.chars:
            return []
        _, _, char_line, starts = self._flat
        out: list[Quad] = []
        for k in range(char_line[i], char_line[j] + 1):
            first = max(i, starts[k])
            last = min(j, starts[k + 1] - 1)
            if first > last:
                continue
            q0 = self._char_quad(first)
            q1 = q0 if last == first else self._char_quad(last)
            out.append(Quad(q0.ul, q1.ur, q0.ll, q1.lr))
        return out

    def text_of(self, chars: Iterable[CharRef | int]) -> str:
        """Text of ``chars`` in content order (duplicates once), ``\\n`` between lines."""
        all_chars, _, char_line, _ = self._flat
        parts: list[str] = []
        prev_line: int | None = None
        for i in sorted({_index(c) for c in chars}):
            if prev_line is not None and char_line[i] != prev_line:
                parts.append("\n")
            parts.append(all_chars[i].c)
            prev_line = char_line[i]
        return "".join(parts)

    def is_invisible(self, ref: CharRef | int) -> bool:
        """The char is not painted (render mode 3 / OCR layer): its span says so, or its
        box centre lies in an ``invisible_rects`` area."""
        i = _index(ref)
        if self._flat[1][i].invisible:
            return True
        c = self.chars[i].bbox.center()
        return any(r.contains(c) for r in self.invisible_rects)


EMPTY_PAGE_TEXT = PageText()


# -- extraction (pymupdf) -----------------------------------------------------------------
def extract_page_text(page: pymupdf.Page) -> PageText:
    """Text of ``page`` (caller holds the document lock). Annotations and widgets are
    excluded; content order is kept (no sorting)."""
    textpage = pymupdf.TextPage(
        page.get_displaylist(annots=False).get_textpage(pymupdf.TEXTFLAGS_RAWDICT)
    )
    rawdict = textpage.extractRAWDICT(sort=False)
    has_text = any(b.get("type", 0) == _TEXT_BLOCK for b in rawdict.get("blocks", ()))
    fonts: list = []
    bboxlog: list = []
    if has_text:
        try:
            fonts = page.get_fonts(full=True)
        except Exception:  # MuPDF raises FzError* (not RuntimeError)
            log.warning("could not list the fonts of page %d", page.number, exc_info=True)
        try:
            bboxlog = page.get_bboxlog()
        except Exception:
            log.warning("could not read the bbox log of page %d", page.number, exc_info=True)
    return PageText.from_rawdict(
        rawdict, fonts=fonts, bboxlog=bboxlog, rotation_matrix=page.rotation_matrix
    )


def match_font(name: str, fonts: Sequence[Sequence[Any]]) -> tuple[int, str]:
    """``(xref, resource_name)`` of the ``get_fonts(full=True)`` entry used by a span
    whose font is ``name``, ``(0, "")`` when unknown or ambiguous.

    The span name is the base font without its "ABCDEF+" subset prefix, ``#xx`` decoded.
    Exact match first; else a normalised comparison (case-insensitive; spaces, ``-``,
    ``,`` and ``_`` dropped; "Regular"/"MT"/"PS" suffixes stripped), because embedded
    TrueType fonts report their PostScript name ("ArialMT") while ``/BaseFont`` may say
    "Arial Regular"; it must single out one font object.
    """
    if not name:
        return 0, ""
    exact: list[tuple[int, str]] = []
    loose: list[tuple[int, str]] = []
    key = _normalise(name)
    for f in fonts:
        if len(f) < 5:
            continue
        xref, basefont, resource = int(f[0]), str(f[3]), str(f[4])
        base = _decode_name(_SUBSET_RE.sub("", basefont))
        if base == name:
            exact.append((xref, resource))
        elif _normalise(base) == key:
            loose.append((xref, resource))
    if exact:
        return exact[0]
    if len({x for x, _ in loose}) == 1:
        return loose[0]
    return 0, ""


# -- helpers ------------------------------------------------------------------------------
def _index(ref: CharRef | int) -> int:
    return ref.index if isinstance(ref, CharRef) else int(ref)


def _qrect(r: Sequence[float]) -> QRectF:
    x0, y0, x1, y1 = r[0], r[1], r[2], r[3]
    return QRectF(x0, y0, x1 - x0, y1 - y0)


def _qpoint(p: Sequence[float] | None, fallback: QRectF) -> QPointF:
    if p is None:
        return fallback.bottomLeft()
    return QPointF(float(p[0]), float(p[1]))


def _union(rects: Iterable[QRectF]) -> QRectF:
    out = QRectF()
    for r in rects:
        out = r if out.isNull() else out.united(r)
    return out


def _decode_name(name: str) -> str:
    return re.sub(r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), name)


def _normalise(name: str) -> str:
    s = _NAME_NOISE_RE.sub("", _decode_name(name)).lower()
    changed = True
    while changed:
        changed = False
        for suffix in _NAME_SUFFIXES:
            if len(s) > len(suffix) and s.endswith(suffix):
                s = s[: -len(suffix)]
                changed = True
    return s


def _span(
    s: dict[str, Any], fonts: Sequence[Sequence[Any]], memo: dict[str, tuple[int, str]]
) -> Span:
    chars_list: list[Char] = []
    for c in s.get("chars", ()):
        x0, y0, x1, y1 = c["bbox"]
        bbox = QRectF(x0, y0, x1 - x0, y1 - y0)
        o = c.get("origin")
        origin = QPointF(o[0], o[1]) if o is not None else QPointF(x0, y1)
        chars_list.append(Char(c.get("c", ""), origin, bbox))
    chars = tuple(chars_list)
    bbox = _qrect(s["bbox"]) if "bbox" in s else _union(ch.bbox for ch in chars)
    font = str(s.get("font", ""))
    if font not in memo:
        memo[font] = match_font(font, fonts)
    xref, resource = memo[font]
    return Span(
        font=font,
        size=float(s.get("size", bbox.height())),
        flags=int(s.get("flags", 0)),
        color=int(s.get("color", 0)),
        origin=_qpoint(s.get("origin"), bbox),
        ascender=float(s.get("ascender", 1.0)),
        descender=float(s.get("descender", 0.0)),
        bbox=bbox,
        chars=chars,
        font_xref=xref,
        resource_name=resource,
        invisible=int(s.get("alpha", 255)) == 0,
    )


def _axis_aligned(d: tuple[float, float]) -> bool:
    return abs(d[0]) < 1e-3 or abs(d[1]) < 1e-3


def _char_quad(d: tuple[float, float], span: Span, bbox: QRectF) -> Quad:
    """Quad of a char box (``pymupdf.utils.recover_bbox_quad`` in Qt types); an
    axis-aligned line's char quad is its box."""
    if _axis_aligned(d):
        q = Quad.from_rect(bbox)
        # Orient the corners along the writing direction.
        cos, sin = d
        if abs(cos) >= abs(sin):
            return q if cos > 0 else Quad(q.lr, q.ll, q.ur, q.ul)
        # Vertical: dir (0, 1) runs down the page (top of glyphs to the right).
        if sin > 0:
            return Quad(q.ur, q.lr, q.ul, q.ll)
        return Quad(q.ll, q.ul, q.lr, q.ur)
    cos, sin = d
    height = (span.ascender - span.descender) * span.size
    hs, hc = height * sin, height * cos
    x0, y0, x1, y1 = bbox.left(), bbox.top(), bbox.right(), bbox.bottom()
    if hc >= 0 and hs <= 0:
        ul, ur, ll, lr = (x0, y1 - hc), (x1 + hs, y0), (x0 - hs, y1), (x1, y0 + hc)
    elif hc <= 0 and hs <= 0:
        ul, ur, ll, lr = (x1 + hs, y1), (x0, y0 - hc), (x1, y1 + hc), (x0 - hs, y0)
    elif hc <= 0 and hs >= 0:
        ul, ur, ll, lr = (x1, y0 - hc), (x0 + hs, y1), (x1 - hs, y0), (x0, y1 + hc)
    else:
        ul, ur, ll, lr = (x0 + hs, y0), (x1, y1 - hc), (x0, y0 + hc), (x1 - hs, y1)
    return Quad(QPointF(*ul), QPointF(*ur), QPointF(*ll), QPointF(*lr))


def _line_geom(line: Line) -> _LineGeom:
    cos, sin = line.dir
    norm = math.hypot(cos, sin) or 1.0
    d = (cos / norm, sin / norm)
    n = (-d[1], d[0])
    extents: list[tuple[float, float]] = []
    pts: list[QPointF] = []
    if _axis_aligned(line.dir):
        # Fast path: char quads are their boxes.
        horizontal = abs(d[0]) >= abs(d[1])
        s = 1.0 if (d[0] if horizontal else d[1]) > 0 else -1.0
        x0 = y0 = math.inf
        x1 = y1 = -math.inf
        for span in line.spans:
            for ch in span.chars:
                b = ch.bbox
                bx0, by0, bx1, by1 = b.left(), b.top(), b.right(), b.bottom()
                lo, hi = (bx0, bx1) if horizontal else (by0, by1)
                extents.append((lo * s, hi * s) if s > 0 else (hi * s, lo * s))
                x0, y0, x1, y1 = min(x0, bx0), min(y0, by0), max(x1, bx1), max(y1, by1)
        if extents:
            pts = [QPointF(x0, y0), QPointF(x1, y1), QPointF(x0, y1), QPointF(x1, y0)]
    else:
        for span in line.spans:
            for ch in span.chars:
                q = _char_quad(line.dir, span, ch.bbox)
                along = [p.x() * d[0] + p.y() * d[1] for p in q]
                extents.append((min(along), max(along)))
                pts.extend(q)
    if not pts:
        r = line.bbox
        pts = [r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()]
    along = [p.x() * d[0] + p.y() * d[1] for p in pts]
    across = [p.x() * n[0] + p.y() * n[1] for p in pts]
    return _LineGeom(d, n, (min(along), max(along)), (min(across), max(across)), tuple(extents))


def _outside(v: float, extent: tuple[float, float]) -> float:
    lo, hi = extent
    if v < lo:
        return lo - v
    if v > hi:
        return v - hi
    return 0.0
