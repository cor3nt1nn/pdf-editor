"""FreeText annotations (text boxes and stamps, M3), signatures (M4) and text markups
(Highlight, Underline, StrikeOut, Squiggly: M6b).

Pure functions on ``pymupdf.Document``. Callers hold ``PdfDocument.lock``. Never keep
``pymupdf.Page`` / ``Annot`` objects across calls (every save replaces the document and
full saves renumber xrefs); while using an ``Annot``, keep its ``Page`` referenced
(deleting an annotation loaded from a temporary page crashes MuPDF).

Identity is the annotation's ``/NM`` (a uuid4 written at creation), resolved again with
:func:`resolve_annot`. Reading never modifies the document: a foreign FreeText without
a unique ``/NM`` is listed under a *synthetic* name (:func:`synthetic_name`,
``"#xref:<scope>:<xref>"``) that is only valid for the current load of the document
(``scope``; full saves renumber xrefs), and gets a real uuid4 ``/NM`` from
:func:`claim_name` when it is first changed. Xrefs are never used as lasting identity.

Geometry: callers work in *page space* (rotation applied, cropbox-relative, points).
The unrotated rect stored in the file is ``page_to_unrotated(rect)`` and the text is
drawn with ``/Rotate`` = the page rotation at creation, so it reads upright on screen.

Text markups (:data:`MARKUP_KINDS`) carry their /QuadPoints as page-space
:class:`~pdfeditor.core.pagetext.Quad` objects (``annot.vertices`` turned by the page's
``rotation_matrix``); their rect is the union of the quads (empty without quads: not
editable), their colour is /C (the stroke colour) and their opacity /CA (1.0 when
absent). They are never moved or resized (:attr:`AnnotInfo.movable`): only their colour
and opacity change. They are created with ``add_*_annot(quads)`` (quads mapped by the
page's ``derotation_matrix``, docs/M6_PLAN.md T3), a uuid4 /NM, /C and, below 1.0, /CA.

FreeText annotations and signatures are handled. Text uses Helvetica (``/Helv``), no
border, no fill; stamps are FreeText annotations showing one ZapfDingbats glyph
(:data:`STAMP_GLYPHS`); signatures are Stamp annotations with ``/IT /StampImage`` and an
image appearance (:mod:`pdfeditor.core.signature`; other Stamps are ignored; a
signature only honours its rect). Other subtypes (and widgets) are ignored. Callout
FreeText (/IT /FreeTextCallout), annotations flagged ReadOnly or Locked and signatures
whose image has no valid size are listed but :attr:`AnnotInfo.locked`: never changed or
deleted; LockedContents keeps the text.
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

import pymupdf
from PySide6.QtCore import QPointF, QRectF

from pdfeditor.core import signature
from pdfeditor.core.forms import ANNOT_HIDDEN, ANNOT_NO_VIEW, _page_rect
from pdfeditor.core.geometry import fitz_from_qrect, page_to_unrotated
from pdfeditor.core.pagetext import Quad
from pdfeditor.core.signature import ImageData

log = logging.getLogger(__name__)

Color = tuple[float, float, float]


class AnnotKind(StrEnum):
    TEXT = "text"
    STAMP = "stamp"
    SIGNATURE = "signature"
    HIGHLIGHT = "highlight"
    UNDERLINE = "underline"
    STRIKEOUT = "strikeout"
    SQUIGGLY = "squiggly"


#: Text markup kinds (quads, colour and opacity; never moved or resized).
MARKUP_KINDS = frozenset(
    {AnnotKind.HIGHLIGHT, AnnotKind.UNDERLINE, AnnotKind.STRIKEOUT, AnnotKind.SQUIGGLY}
)
#: PDF annotation subtype of each markup kind (docs/M6_PLAN.md T5).
MARKUP_SUBTYPES = {
    AnnotKind.HIGHLIGHT: pymupdf.PDF_ANNOT_HIGHLIGHT,
    AnnotKind.UNDERLINE: pymupdf.PDF_ANNOT_UNDERLINE,
    AnnotKind.STRIKEOUT: pymupdf.PDF_ANNOT_STRIKE_OUT,
    AnnotKind.SQUIGGLY: pymupdf.PDF_ANNOT_SQUIGGLY,
}
_MARKUP_BY_SUBTYPE = {subtype: kind for kind, subtype in MARKUP_SUBTYPES.items()}
#: Largest coordinate (points) of a usable markup quad (``forms.MAX_COORD`` for widgets).
MAX_QUAD_COORD = 1e6
#: Colour of a markup without /C (what MuPDF writes when creating one, T3).
MARKUP_DEFAULT_COLORS: dict[AnnotKind, tuple[float, float, float]] = {
    AnnotKind.HIGHLIGHT: (1.0, 1.0, 0.0),
    AnnotKind.UNDERLINE: (0.0, 1.0, 0.0),
    AnnotKind.STRIKEOUT: (1.0, 0.0, 0.0),
    AnnotKind.SQUIGGLY: (1.0, 0.0, 1.0),
}


#: Stamp name -> ZapfDingbats character code (✓, ✗, ●).
STAMP_GLYPHS = {"check": "4", "cross": "8", "dot": "l"}
#: Centre of each rendered ZapfDingbats glyph relative to the annotation rect's top-left
#: corner, in units of the font size (measured, docs/M3_PLAN.md A4).
STAMP_CENTRE = {"4": (0.425, 0.45), "8": (0.34, 0.45), "l": (0.40, 0.45)}
#: Stamp font size = ratio x box side.
STAMP_FONT_RATIO = 0.9
#: First baseline = rect top + ratio x font size (MuPDF's FreeText appearance).
BASELINE_RATIO = 0.8
#: Line pitch = ratio x font size.
LINE_HEIGHT_RATIO = 1.2
#: Extra height (points) added below the text lines when a text box hugs its content.
TEXT_PAD = 2.0
DEFAULT_TEXT_WIDTH = 180.0
#: Space (points) kept between an auto-width text box and the page's right edge (along
#: the text direction) when nothing else limits its width (Deviation 164).
TEXT_PAGE_MARGIN = 18.0
#: Extra width (points) added to the measured text of an auto-width box so that no glyph
#: overhang is clipped (MuPDF draws the text from the rect's left edge, unpadded).
TEXT_SIDE_PAD = 2.0
#: Private annotation keys written by this application only (Deviation 164):
#: ``/PDFEditorFixedWidth`` (true: keep the width; false: auto width, the width hugs the
#: text) and ``/PDFEditorMaxWidth`` (an auto box's wrap width, points; absent: up to the
#: page edge). A text box without the flag (another program's, or an older version's)
#: keeps its width.
FIXED_WIDTH_KEY = "PDFEditorFixedWidth"
MAX_WIDTH_KEY = "PDFEditorMaxWidth"
DEFAULT_FONT_SIZE = 11.0
BLACK: Color = (0.0, 0.0, 0.0)
#: Annotation flags (PDF 32000 §12.5.3) making an annotation read-only for us.
ANNOT_READ_ONLY = 64
ANNOT_LOCKED = 128
ANNOT_LOCKED_CONTENTS = 512
#: /IT of a callout FreeText (its /CL line is not maintained here: not editable).
CALLOUT_INTENT = "FreeTextCallout"
#: Prefix of synthetic names (never a real /NM: such a /NM is treated as missing).
SYNTHETIC_PREFIX = "#xref:"

_TEXT_FONT = "helv"
_STAMP_FONT = "zadb"


@dataclass(frozen=True)
class AnnotInfo:
    """Snapshot of one FreeText annotation, signature or text markup (no live pymupdf
    object)."""

    page: int
    #: Xref of the annotation (stale after a full save; identity is ``name``).
    xref: int
    #: The annotation's /NM, or a synthetic name (:func:`is_synthetic`) for a foreign
    #: FreeText without a unique /NM.
    name: str
    kind: AnnotKind
    #: /Contents (line breaks normalised to "\n"; a markup's comment, read-only here).
    text: str
    #: Font size from /DA (markups: ``DEFAULT_FONT_SIZE``, unused).
    font_size: float
    #: Text colour (RGB, 0..1) from /DA; a markup's /C.
    color: Color
    #: The annotation's /Rotate (0 when absent).
    rotate: int
    hidden: bool
    #: Rect in page space (rotation applied); empty for a missing/degenerate /Rect. A
    #: markup's rect is the union of its quads (its /Rect is larger).
    rect: QRectF
    #: Raw /Rect (x0, y0, x1, y1) in unrotated page coordinates (``annot.rect``).
    unrotated_rect: tuple[float, float, float, float]
    #: /F ReadOnly or Locked, or a callout (/IT /FreeTextCallout): never changed here.
    locked: bool = False
    #: /F LockedContents: the text cannot be edited (moving and resizing can).
    locked_contents: bool = False
    #: Signature only: xref of its image (stale after a full save) and its pixel size
    #: as embedded (pre-rotated for the page).
    image_xref: int = 0
    image_size: tuple[int, int] = (0, 0)
    #: Markup only: /QuadPoints in page space, one :class:`Quad` per group of 8 numbers.
    quads: tuple[Quad, ...] = ()
    #: /CA (1.0 when absent).
    opacity: float = 1.0
    #: Text only: the width is kept on text edits (/PDFEditorFixedWidth not false);
    #: False: the width hugs the text (Deviation 164).
    fixed_width: bool = True
    #: Text only, auto width: wrap width (points, /PDFEditorMaxWidth); 0 = page edge.
    max_width: float = 0.0

    @property
    def editable(self) -> bool:
        """The user can select and edit this annotation (move it too if :attr:`movable`)."""
        return not self.hidden and not self.locked and not self.rect.isEmpty()

    @property
    def is_markup(self) -> bool:
        """A text markup (Highlight, Underline, StrikeOut, Squiggly)."""
        return self.kind in MARKUP_KINDS

    @property
    def movable(self) -> bool:
        """The annotation can be moved and resized (not a text markup)."""
        return self.kind not in MARKUP_KINDS

    @property
    def text_editable(self) -> bool:
        """The user can change the text of this (editable) annotation."""
        return (
            self.editable
            and not self.locked_contents
            and self.kind is not AnnotKind.SIGNATURE
            and self.kind not in MARKUP_KINDS
        )


@dataclass(frozen=True)
class AnnotSpec:
    """Everything needed to create an annotation."""

    page: int
    kind: AnnotKind
    #: Text, or the ZapfDingbats code of a stamp (a value of :data:`STAMP_GLYPHS`);
    #: ignored for a signature.
    text: str
    font_size: float
    color: Color
    #: Rect in page space.
    rect: QRectF
    #: /NM to write; "" = generate a uuid4.
    name: str = ""
    #: /Rotate of the text; None = the page rotation at creation (upright on screen).
    rotate: int | None = None
    #: Signature only: the image samples, already turned for the page (required).
    image: ImageData | None = None
    #: Markup only (required): the quads to mark, page space (``PageText.range_quads``).
    quads: tuple[Quad, ...] = ()
    #: Markup only: /CA (written only below 1.0).
    opacity: float = 1.0
    #: Text only: keep the width (True) or hug the text, up to ``max_width`` (0 = the
    #: page edge less ``TEXT_PAGE_MARGIN``) when created with ``fit_height``.
    fixed_width: bool = True
    max_width: float = 0.0


def quads_rect(quads: Iterable[Quad]) -> QRectF:
    """Union of the bounding rects of ``quads`` (page space); empty without quads."""
    out: QRectF | None = None
    for quad in quads:
        box = quad.bounding_rect()
        out = box if out is None else out.united(box)
    return QRectF() if out is None else out


def markup_spec(
    page: int,
    kind: AnnotKind,
    quads: Sequence[Quad],
    color: Color,
    *,
    opacity: float = 1.0,
    name: str = "",
) -> AnnotSpec:
    """The spec of a text markup of ``kind`` covering ``quads`` (page space)."""
    if kind not in MARKUP_KINDS:
        raise ValueError(f"{kind} is not a text markup")
    return AnnotSpec(
        page=page,
        kind=kind,
        text="",
        font_size=DEFAULT_FONT_SIZE,
        color=(float(color[0]), float(color[1]), float(color[2])),
        rect=quads_rect(quads),
        name=name,
        quads=tuple(quads),
        opacity=float(opacity),
    )


# -- /DA -------------------------------------------------------------------
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)"
_DA_FONT = re.compile(rf"/([^\s/\[\]()<>]+)\s+({_NUM})\s+Tf")
_DA_RGB = re.compile(rf"({_NUM})\s+({_NUM})\s+({_NUM})\s+rg\b")
_DA_GRAY = re.compile(rf"({_NUM})\s+g\b")
_DA_CMYK = re.compile(rf"({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+k\b")


def _clamp(v: float) -> float:
    return min(1.0, max(0.0, v))


def parse_da(da: str) -> tuple[str, float, Color]:
    """(font resource name, font size, RGB colour) of a /DA string.

    Missing parts default to ("", 0.0, black). ``"0 0 1 rg /Helv 11 Tf"`` ->
    ``("Helv", 11.0, (0, 0, 1))``.
    """
    font, size = "", 0.0
    m = _DA_FONT.search(da or "")
    if m:
        font, size = m.group(1), abs(float(m.group(2)))
    color = BLACK
    m_rgb, m_gray, m_cmyk = _DA_RGB.search(da or ""), _DA_GRAY.search(da or ""), None
    if m_rgb:
        color = tuple(_clamp(float(v)) for v in m_rgb.groups())  # type: ignore[assignment]
    else:
        m_cmyk = _DA_CMYK.search(da or "")
        if m_cmyk:
            c, mg, y, k = (_clamp(float(v)) for v in m_cmyk.groups())
            color = ((1 - c) * (1 - k), (1 - mg) * (1 - k), (1 - y) * (1 - k))
        elif m_gray:
            g = _clamp(float(m_gray.group(1)))
            color = (g, g, g)
    return font, size, color


# -- low-level helpers -----------------------------------------------------
def _key(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        return doc.xref_get_key(xref, key)
    except Exception:  # malformed object
        return ("null", "null")


def _string_key(doc: pymupdf.Document, xref: int, key: str) -> str:
    kind, value = _key(doc, xref, key)
    return value if kind == "string" else ""


def _float_key(doc: pymupdf.Document, xref: int, key: str) -> float:
    kind, value = _key(doc, xref, key)
    if kind in ("int", "float"):
        try:
            number = float(value)
        except ValueError:
            return 0.0
        return number if math.isfinite(number) and number > 0 else 0.0
    return 0.0


def _int_key(doc: pymupdf.Document, xref: int, key: str) -> int:
    kind, value = _key(doc, xref, key)
    if kind in ("int", "float"):
        try:
            return int(float(value))
        except ValueError:
            return 0
    return 0


def _set_name(doc: pymupdf.Document, xref: int, name: str) -> None:
    # Annot.set_name() writes /Name (the icon), not /NM (A5).
    doc.xref_set_key(xref, "NM", pymupdf.get_pdf_str(name))


def assign_name(fitz_doc: pymupdf.Document, xref: int, name: str) -> None:
    """Write ``name`` as the /NM of annotation ``xref``."""
    _set_name(fitz_doc, xref, name)


def _kind(font: str, text: str) -> AnnotKind:
    if font == "ZaDb" and text in STAMP_CENTRE:
        return AnnotKind.STAMP
    return AnnotKind.TEXT


def _info(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    index: int,
    annot: pymupdf.Annot,
    details: dict[str, str] | None = None,
    *,
    image_xref: int | None = None,
) -> AnnotInfo:
    if annot.type[0] == pymupdf.PDF_ANNOT_STAMP:
        return _signature_info(doc, page, index, annot, details, image_xref)
    if annot.type[0] in _MARKUP_BY_SUBTYPE:
        return _markup_info(doc, page, index, annot, details)
    xref = int(annot.xref)
    font, size, color = parse_da(_string_key(doc, xref, "DA"))
    if details is None:
        details = annot.info
    text = str(details.get("content") or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = pymupdf.Rect(annot.rect)
    rotate = _int_key(doc, xref, "Rotate") % 360 // 90 * 90
    flags = int(annot.flags or 0)
    intent = _key(doc, xref, "IT")
    callout = intent[0] == "name" and intent[1].lstrip("/") == CALLOUT_INTENT
    return AnnotInfo(
        page=index,
        xref=xref,
        name=str(details.get("id") or ""),
        kind=_kind(font, text),
        text=text,
        font_size=size or DEFAULT_FONT_SIZE,
        color=color,
        rotate=rotate,
        hidden=bool(flags & (ANNOT_HIDDEN | ANNOT_NO_VIEW)),
        rect=_page_rect(raw, page),
        unrotated_rect=(raw.x0, raw.y0, raw.x1, raw.y1),
        locked=callout or bool(flags & (ANNOT_READ_ONLY | ANNOT_LOCKED)),
        locked_contents=bool(flags & ANNOT_LOCKED_CONTENTS),
        fixed_width=_key(doc, xref, FIXED_WIDTH_KEY) != ("bool", "false"),
        max_width=_float_key(doc, xref, MAX_WIDTH_KEY),
    )


def _signature_info(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    index: int,
    annot: pymupdf.Annot,
    details: dict[str, str] | None,
    image_xref: int | None,
) -> AnnotInfo:
    """Snapshot of signature ``annot`` (locked when its image has no valid size or its
    appearance is not MuPDF's, see :func:`signature.has_mupdf_appearance`)."""
    xref = int(annot.xref)
    if details is None:
        details = annot.info
    if image_xref is None:
        image_xref = signature.signature_image_xref(doc, page, annot)
    size = signature.image_size(doc, image_xref) if image_xref else (0, 0)
    ok = size[0] > 0 and size[1] > 0 and signature.has_mupdf_appearance(doc, xref)
    raw = pymupdf.Rect(annot.rect)
    flags = int(annot.flags or 0)
    return AnnotInfo(
        page=index,
        xref=xref,
        name=str(details.get("id") or ""),
        kind=AnnotKind.SIGNATURE,
        text="",
        font_size=DEFAULT_FONT_SIZE,
        color=BLACK,
        rotate=0,
        hidden=bool(flags & (ANNOT_HIDDEN | ANNOT_NO_VIEW)),
        rect=_page_rect(raw, page),
        unrotated_rect=(raw.x0, raw.y0, raw.x1, raw.y1),
        locked=not ok or bool(flags & (ANNOT_READ_ONLY | ANNOT_LOCKED)),
        locked_contents=bool(flags & ANNOT_LOCKED_CONTENTS),
        image_xref=image_xref,
        image_size=size,
    )


def _stroke_color(annot: pymupdf.Annot, kind: AnnotKind) -> Color:
    """/C of a markup as RGB (gray and CMYK converted; the kind's default if absent)."""
    values = [_clamp(float(v)) for v in ((annot.colors or {}).get("stroke") or ())]
    if len(values) == 3:
        return (values[0], values[1], values[2])
    if len(values) == 1:
        return (values[0], values[0], values[0])
    if len(values) == 4:
        c, m, y, k = values
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return MARKUP_DEFAULT_COLORS[kind]


def _page_quads(annot: pymupdf.Annot, page: pymupdf.Page) -> tuple[Quad, ...]:
    """/QuadPoints of ``annot`` in page space (``vertices`` are unrotated). A quad with a
    coordinate that is not finite or reaches ``MAX_QUAD_COORD`` is dropped (it would
    cover every page and catch every click)."""
    matrix = page.rotation_matrix
    points = [pymupdf.Point(v) * matrix for v in (annot.vertices or ())]
    out = []
    for i in range(0, len(points) - 3, 4):
        group = points[i : i + 4]
        if not all(math.isfinite(v) and abs(v) < MAX_QUAD_COORD for p in group for v in p):
            continue
        out.append(_ordered_quad([QPointF(p.x, p.y) for p in group]))
    return tuple(out)


def _ordered_quad(points: list[QPointF]) -> Quad:
    """The four /QuadPoints corners of one quad as a :class:`Quad`, whatever order the
    producer wrote them in: PyMuPDF/Acrobat write (ul, ur, ll, lr), the PDF specification
    describes (ll, lr, ur, ul) — read as written, the latter gives a self-intersecting
    outline. The first two points share an edge along the writing direction ``d`` in both
    orders, so each corner is named by its projection on ``d`` and on the normal
    (-d.y, d.x) (upper = the low side, as in :class:`~pdfeditor.core.pagetext.Quad`); the
    points themselves are kept (slanted quads stay as they are). Degenerate input is
    returned as written."""
    p0, p1 = points[0], points[1]
    dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return Quad(*points)
    dx, dy = dx / length, dy / length
    across = sorted(points, key=lambda p: p.y() * dx - p.x() * dy)
    upper = sorted(across[:2], key=lambda p: p.x() * dx + p.y() * dy)
    lower = sorted(across[2:], key=lambda p: p.x() * dx + p.y() * dy)
    return Quad(upper[0], upper[1], lower[0], lower[1])


def _markup_info(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    index: int,
    annot: pymupdf.Annot,
    details: dict[str, str] | None,
) -> AnnotInfo:
    """Snapshot of text markup ``annot`` (rect = union of its quads)."""
    xref = int(annot.xref)
    kind = _MARKUP_BY_SUBTYPE[annot.type[0]]
    if details is None:
        details = annot.info
    text = str(details.get("content") or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = pymupdf.Rect(annot.rect)
    flags = int(annot.flags or 0)
    quads = _page_quads(annot, page)
    opacity = float(annot.opacity)
    return AnnotInfo(
        page=index,
        xref=xref,
        name=str(details.get("id") or ""),
        kind=kind,
        text=text,
        font_size=DEFAULT_FONT_SIZE,
        color=_stroke_color(annot, kind),
        rotate=_int_key(doc, xref, "Rotate") % 360 // 90 * 90,
        hidden=bool(flags & (ANNOT_HIDDEN | ANNOT_NO_VIEW)),
        rect=quads_rect(quads),
        unrotated_rect=(raw.x0, raw.y0, raw.x1, raw.y1),
        locked=bool(flags & (ANNOT_READ_ONLY | ANNOT_LOCKED)),
        quads=quads,
        opacity=round(opacity, 4) if 0.0 <= opacity < 1.0 else 1.0,
    )


def read_one(fitz_doc: pymupdf.Document, page_index: int, xref: int) -> AnnotInfo:
    """Snapshot of annotation ``xref`` of a page (FreeText, signature or markup; real
    /NM)."""
    page = fitz_doc[page_index]
    return _info(fitz_doc, page, page_index, page.load_annot(xref))


def _candidate(fitz_doc: pymupdf.Document, xref: int, subtype: int) -> bool:
    """Annotation ``xref`` of ``subtype`` may be one we list (cheap check)."""
    if subtype == pymupdf.PDF_ANNOT_FREE_TEXT or subtype in _MARKUP_BY_SUBTYPE:
        return True
    return subtype == pymupdf.PDF_ANNOT_STAMP and signature.is_signature_intent(fitz_doc, xref)


def new_name() -> str:
    """A fresh /NM value."""
    return str(uuid.uuid4())


def synthetic_name(xref: int, scope: str = "") -> str:
    """The in-memory name of an annotation without a unique /NM (valid for ``scope``)."""
    return f"{SYNTHETIC_PREFIX}{scope}:{xref}"


def is_synthetic(name: str) -> bool:
    """``name`` is a synthetic name (no /NM written yet)."""
    return name.startswith(SYNTHETIC_PREFIX)


def synthetic_parts(name: str) -> tuple[str, int] | None:
    """(scope, xref) of a synthetic name, else None."""
    if not is_synthetic(name):
        return None
    scope, _, xref = name[len(SYNTHETIC_PREFIX) :].rpartition(":")
    try:
        return scope, int(xref)
    except ValueError:
        return None


# -- reading ---------------------------------------------------------------
def read_annots(
    fitz_doc: pymupdf.Document,
    page_index: int,
    *,
    include_hidden: bool = False,
    scope: str = "",
) -> list[AnnotInfo]:
    """FreeText annotations, signatures and text markups of a page, in /Annots order;
    hidden ones skipped by default.

    Never modifies the document. An annotation without /NM, whose /NM repeats an earlier
    one of the page or looks synthetic, is listed under ``synthetic_name(xref, scope)``.
    """
    page = fitz_doc[page_index]  # keep the Page alive while its annots are used
    out: list[AnnotInfo] = []
    seen: set[str] = set()
    # page.load_annot(xref) scans the page's annotation list from its start (quadratic:
    # about 80 ms for 200 annotations). Do NOT walk it with page.first_annot/Annot.next
    # instead: in PyMuPDF 1.28.2 that makes a FreeText without /AP (MuPDF-synthesised
    # appearance) vanish from renders once another annotation is added to the page.
    for xref, subtype, name in page.annot_xrefs():
        if not _candidate(fitz_doc, xref, subtype):
            continue
        try:
            annot = page.load_annot(xref)
            image = None
            if subtype == pymupdf.PDF_ANNOT_STAMP:
                image = signature.signature_image_xref(fitz_doc, page, annot)
                if not image:
                    continue  # a Stamp that is not a signature
            if not name or name in seen or is_synthetic(name):
                name = synthetic_name(xref, scope)
            else:
                seen.add(name)
            details = {**annot.info, "id": name}
            info = _info(fitz_doc, page, page_index, annot, details, image_xref=image)
        except Exception:  # one malformed annotation must not hide the others
            log.warning("skipping unreadable annotation xref %s", xref, exc_info=True)
            continue
        if include_hidden or not info.hidden:
            out.append(info)
    return out


def resolve_annot(
    fitz_doc: pymupdf.Document, page_index: int, name: str
) -> tuple[pymupdf.Page, pymupdf.Annot] | None:
    """The live FreeText annotation, signature or markup named ``name`` on a page, as
    ``(page, annot)`` — keep the page referenced while using the annot — or None.

    A synthetic name is resolved by its xref (the caller checks its scope).
    """
    if not name:
        return None
    page = fitz_doc[page_index]
    parts = synthetic_parts(name)
    for xref, subtype, nm in page.annot_xrefs():
        if not _candidate(fitz_doc, xref, subtype):
            continue
        if (xref == parts[1]) if parts is not None else (nm == name):
            annot = page.load_annot(xref)
            if subtype == pymupdf.PDF_ANNOT_STAMP and not signature.signature_image_xref(
                fitz_doc, page, annot
            ):
                continue
            return page, annot
    return None


def claim_name(fitz_doc: pymupdf.Document, page_index: int, name: str) -> str:
    """The lasting /NM of annotation ``name``: ``name`` itself, or for a synthetic name a
    fresh uuid4 written to the annotation now. Raises ``LookupError`` if it is gone."""
    if not is_synthetic(name):
        return name
    found = resolve_annot(fitz_doc, page_index, name)
    if found is None:
        raise LookupError(f"annotation {name!r} not found on page {page_index}")
    _page, annot = found
    real = new_name()
    _set_name(fitz_doc, annot.xref, real)
    log.info("assigned /NM %s to annotation xref %s", real, annot.xref)
    return real


# -- writing ---------------------------------------------------------------
def _pdf_size(size: float) -> float | int:
    """Font size as written to /DA ("11", not "11.0", for whole sizes)."""
    return int(size) if float(size).is_integer() else float(size)


def _fontname(kind: AnnotKind) -> str:
    return _STAMP_FONT if kind is AnnotKind.STAMP else _TEXT_FONT


def create_annot(
    fitz_doc: pymupdf.Document, page_index: int, spec: AnnotSpec, *, fit_height: bool = False
) -> AnnotInfo:
    """Create a FreeText annotation, signature or text markup from ``spec`` and return
    its snapshot.

    ``fit_height`` (text only): the height then hugs the wrapped text (see
    :func:`update_annot`); otherwise ``spec.rect`` is used as is. A signature gets an
    image object of its own (``PdfDocument.add_annot`` shares them instead). A markup
    uses ``spec.quads`` (``ValueError`` without), ``color``, ``opacity`` and, when not
    empty, ``text`` as /Contents; ``rect``, ``font_size`` and ``rotate`` are ignored.
    """
    if spec.kind in MARKUP_KINDS:
        return _create_markup(fitz_doc, page_index, spec)
    if spec.kind is AnnotKind.SIGNATURE:
        if spec.image is None:
            raise ValueError("a signature spec needs an image")
        image_xref = signature.add_image_xobject(fitz_doc, spec.image)
        return signature.create_signature_annot(fitz_doc, page_index, spec, image_xref)
    page = fitz_doc[page_index]
    rotate = page.rotation if spec.rotate is None else int(spec.rotate) % 360
    unrotated = page_to_unrotated(fitz_from_qrect(spec.rect), page.derotation_matrix)
    annot = page.add_freetext_annot(
        unrotated,
        spec.text,
        fontsize=_pdf_size(spec.font_size),
        fontname=_fontname(spec.kind),
        text_color=spec.color,
        fill_color=None,
        border_width=0,
        rotate=rotate,
    )
    xref = annot.xref
    _set_name(fitz_doc, xref, spec.name or new_name())
    # add_freetext_annot writes a stray callout line array (A1).
    fitz_doc.xref_set_key(xref, "CL", "null")
    if spec.kind is AnnotKind.TEXT:
        _write_width_mode(fitz_doc, xref, spec.fixed_width, spec.max_width)
    info = _info(fitz_doc, page, page_index, page.load_annot(xref))
    if fit_height and info.kind is AnnotKind.TEXT:
        return update_annot(fitz_doc, page_index, info.name, fit_height=True)
    return info


def _fitz_quad(quad: Quad) -> pymupdf.Quad:
    return pymupdf.Quad(*(pymupdf.Point(p.x(), p.y()) for p in quad))


def _rgb(color: Color) -> tuple[float, float, float]:
    return (_clamp(float(color[0])), _clamp(float(color[1])), _clamp(float(color[2])))


def _create_markup(fitz_doc: pymupdf.Document, page_index: int, spec: AnnotSpec) -> AnnotInfo:
    if not spec.quads:
        raise ValueError("a text markup needs at least one quad")
    page = fitz_doc[page_index]
    unrotated = [_fitz_quad(q) * page.derotation_matrix for q in spec.quads]
    adders = {
        AnnotKind.HIGHLIGHT: page.add_highlight_annot,
        AnnotKind.UNDERLINE: page.add_underline_annot,
        AnnotKind.STRIKEOUT: page.add_strikeout_annot,
        AnnotKind.SQUIGGLY: page.add_squiggly_annot,
    }
    annot = adders[spec.kind](quads=unrotated)
    xref = annot.xref
    _set_name(fitz_doc, xref, spec.name or new_name())
    annot.set_colors(stroke=_rgb(spec.color))
    if spec.opacity < 1.0:
        annot.set_opacity(max(0.0, float(spec.opacity)))
    if spec.text:
        annot.set_info(content=spec.text)
    annot.update()
    return _info(fitz_doc, page, page_index, page.load_annot(xref))


def _update_markup(
    fitz_doc: pymupdf.Document,
    page: pymupdf.Page,
    page_index: int,
    annot: pymupdf.Annot,
    name: str,
    color: Color | None,
    opacity: float | None,
) -> AnnotInfo:
    if color is not None:
        annot.set_colors(stroke=_rgb(color))
    if opacity is not None:
        annot.set_opacity(_clamp(float(opacity)))  # 1.0 removes /CA
    annot.update()  # keeps /QuadPoints and /Rotate (T4); a foreign markup gets an /AP
    xref = annot.xref
    return _info(fitz_doc, page, page_index, page.load_annot(xref), {**annot.info, "id": name})


def _write_width_mode(
    fitz_doc: pymupdf.Document, xref: int, fixed: bool, max_width: float | None = None
) -> None:
    """Write /PDFEditorFixedWidth and, given ``max_width`` (> 0 for an auto box), set or
    remove /PDFEditorMaxWidth (Deviation 164)."""
    fitz_doc.xref_set_key(xref, FIXED_WIDTH_KEY, "false" if not fixed else "true")
    if max_width is None:
        return
    if not fixed and max_width > 0 and math.isfinite(max_width):
        fitz_doc.xref_set_key(xref, MAX_WIDTH_KEY, f"{max_width:g}")
    elif _key(fitz_doc, xref, MAX_WIDTH_KEY)[0] != "null":
        fitz_doc.xref_set_key(xref, MAX_WIDTH_KEY, "null")


def natural_text_width(text: str, font_size: float) -> float:
    """Width (points) of the longest line of ``text`` in Helvetica at ``font_size`` (the
    advance widths MuPDF wraps the FreeText appearance with), plus ``TEXT_SIDE_PAD``."""
    longest = max(
        (
            pymupdf.get_text_length(line, fontname=_TEXT_FONT, fontsize=font_size)
            for line in normalize_text(text).split("\n")
        ),
        default=0.0,
    )
    return longest + TEXT_SIDE_PAD


def min_text_width(font_size: float) -> float:
    """Smallest width (points) of an auto-width text box: one em."""
    return max(float(font_size), 1.0)


def fitted_width(text: str, font_size: float, limit: float) -> float:
    """Width of an auto-width text box showing ``text``: its natural width, at most
    ``limit`` (where longer lines wrap) and at least :func:`min_text_width`."""
    low = min_text_width(font_size)
    return max(low, min(natural_text_width(text, font_size), max(limit, low)))


def width_limit(room: float, max_width: float = 0.0) -> float:
    """Wrap width of an auto-width box with ``room`` points before the page edge (along
    the text direction): ``max_width`` when set (a cell or underline), else ``room`` less
    ``TEXT_PAGE_MARGIN``; never more than ``room`` unless that is less than the margin."""
    limit = max_width if max_width > 0 else room - TEXT_PAGE_MARGIN
    return max(1.0, min(limit, room) if room > 0 else limit)


def normalize_text(text: str) -> str:
    """``text`` with CRLF/CR line breaks turned into LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _frame_room(page: pymupdf.Page, rect: pymupdf.Rect, rotate: int) -> float:
    """Distance (points) from the start of the text in unrotated ``rect`` to the page
    edge, along the text direction."""
    bounds = (page.rect * page.derotation_matrix).normalize()
    if rotate == 90:  # bottom to top
        return rect.y1 - bounds.y0
    if rotate == 180:  # right to left
        return rect.x1 - bounds.x0
    if rotate == 270:  # top to bottom
        return bounds.y1 - rect.y0
    return bounds.x1 - rect.x0


def _with_frame_width(rect: pymupdf.Rect, rotate: int, width: float) -> pymupdf.Rect:
    """``rect`` (unrotated) with its text frame width set, keeping where the text
    starts."""
    r = pymupdf.Rect(rect)
    if rotate == 90:
        r.y0 = r.y1 - width
    elif rotate == 180:
        r.x0 = r.x1 - width
    elif rotate == 270:
        r.y1 = r.y0 + width
    else:
        r.x1 = r.x0 + width
    return r


def _with_frame_height(rect: pymupdf.Rect, rotate: int, height: float) -> pymupdf.Rect:
    """``rect`` (unrotated) with its text frame height set, keeping the text's top edge."""
    r = pymupdf.Rect(rect)
    if rotate == 90:
        r.x1 = r.x0 + height
    elif rotate == 180:
        r.y0 = r.y1 - height
    elif rotate == 270:
        r.x0 = r.x1 - height
    else:
        r.y1 = r.y0 + height
    return r


def _line_count(page: pymupdf.Page, annot: pymupdf.Annot, rotate: int, font_size: float) -> int:
    """Number of text lines of ``annot``'s appearance, up to the last visible one
    (leading empty lines count, trailing ones do not)."""
    rect = pymupdf.Rect(annot.rect)
    last = 0.0
    for block in annot.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                if not span.get("text", "").strip():
                    continue
                # get_text reports page space; measure from the text's top edge.
                o = pymupdf.Point(span["origin"]) * page.derotation_matrix
                offset = {
                    90: o.x - rect.x0,
                    180: rect.y1 - o.y,
                    270: rect.x1 - o.x,
                }.get(rotate, o.y - rect.y0)
                last = max(last, offset)
    if last <= 0:
        return 1
    pitch = LINE_HEIGHT_RATIO * font_size
    return max(1, round((last - BASELINE_RATIO * font_size) / pitch) + 1)


def fitted_height(lines: int, font_size: float) -> float:
    """Height of a text box hugging ``lines`` lines."""
    return max(1, lines) * LINE_HEIGHT_RATIO * font_size + TEXT_PAD


def update_annot(
    fitz_doc: pymupdf.Document,
    page_index: int,
    name: str,
    *,
    text: str | None = None,
    font_size: float | None = None,
    color: Color | None = None,
    rect: QRectF | None = None,
    fit_height: bool = False,
    opacity: float | None = None,
    fixed_width: bool | None = None,
) -> AnnotInfo:
    """Change an annotation and regenerate its appearance; returns the new snapshot.

    ``None`` keeps a property. ``rect`` is in page space. For a stamp resized without an
    explicit ``font_size`` the glyph is scaled to ``STAMP_FONT_RATIO x min(w, h)``.
    ``fit_height`` (text only) then sets the height to hug the wrapped text, keeping the
    top edge and the width, capped at the page edge (longer text is clipped); an
    auto-width text box (:attr:`AnnotInfo.fixed_width` False) first gets the width of its
    text (:func:`fitted_width`, wrapping at its ``max_width`` or near the page edge, the
    start of its text kept). ``fixed_width`` (text only) sets that mode. Foreign
    annotations are normalised to Helvetica (stamps to ZapfDingbats) and lose their rich
    text (/RC, /DS); /CL is always removed. The returned snapshot keeps ``name`` (even
    synthetic). A signature honours ``rect`` only (the rest is ignored; without a rect
    nothing is written). A text markup honours ``color`` and ``opacity`` only
    (``font_size`` is ignored; ``text``, ``rect`` or ``fit_height`` raise ``ValueError``);
    ``opacity`` is ignored for other kinds. Raises ``LookupError`` if the annotation is
    gone, ``PermissionError`` if it is :attr:`AnnotInfo.locked`.
    """
    found = resolve_annot(fitz_doc, page_index, name)
    if found is None:
        raise LookupError(f"annotation {name!r} not found on page {page_index}")
    page, annot = found
    xref = annot.xref
    current = _info(fitz_doc, page, page_index, annot)
    if current.locked:
        raise PermissionError(f"annotation {name!r} is locked")
    if current.kind in MARKUP_KINDS:
        if text is not None or rect is not None or fit_height:
            raise ValueError("a text markup only changes its colour and opacity")
        return _update_markup(fitz_doc, page, page_index, annot, name, color, opacity)
    if current.kind is AnnotKind.SIGNATURE:
        if rect is not None:
            unrotated = page_to_unrotated(fitz_from_qrect(rect), page.derotation_matrix)
            signature.set_signature_rect(annot, unrotated)
            if _key(fitz_doc, xref, "CL")[0] != "null":
                fitz_doc.xref_set_key(xref, "CL", "null")
        details = {**annot.info, "id": name}
        return _info(fitz_doc, page, page_index, page.load_annot(xref), details)
    kind = current.kind
    if text is not None and text not in STAMP_CENTRE:
        kind = AnnotKind.TEXT  # a stamp given ordinary text becomes a text box
    if rect is not None and kind is AnnotKind.STAMP and font_size is None:
        font_size = STAMP_FONT_RATIO * min(rect.width(), rect.height())
    size = current.font_size if font_size is None else float(font_size)
    rgb = current.color if color is None else tuple(float(c) for c in color)
    for key in ("RC", "DS"):  # rich text would override /Contents in other viewers
        if _key(fitz_doc, xref, key)[0] != "null":
            fitz_doc.xref_set_key(xref, key, "null")
    if text is not None:
        annot.set_info(content=text)
    if rect is not None:
        _set_rect(fitz_doc, annot, page_to_unrotated(fitz_from_qrect(rect), page.derotation_matrix))
    fixed = current.fixed_width
    if fixed_width is not None and kind is AnnotKind.TEXT:
        fixed = bool(fixed_width)
        if fixed != current.fixed_width or _key(fitz_doc, xref, FIXED_WIDTH_KEY)[0] == "null":
            _write_width_mode(fitz_doc, xref, fixed)
    kwargs = {"fontsize": _pdf_size(size), "fontname": _fontname(kind), "text_color": rgb}
    if fit_height and kind is AnnotKind.TEXT:
        body = text if text is not None else current.text
        if not fixed:
            frame = pymupdf.Rect(annot.rect)
            limit = width_limit(_frame_room(page, frame, current.rotate), current.max_width)
            width = fitted_width(body, size, limit)
            _set_rect(fitz_doc, annot, _with_frame_width(frame, current.rotate, width))
        # Measure with a frame tall enough for every line, then hug the content.
        tall = fitted_height(len(body) + body.count("\n") + 2, size)
        _set_rect(fitz_doc, annot, _with_frame_height(annot.rect, current.rotate, tall))
        annot.update(**kwargs)
        lines = _line_count(page, annot, current.rotate, size)
        fitted = _with_frame_height(annot.rect, current.rotate, fitted_height(lines, size))
        _set_rect(fitz_doc, annot, _on_page(page, fitted))
    annot.update(**kwargs)
    # Annot.set_rect() writes a callout line /CL (A1): drop it again.
    fitz_doc.xref_set_key(xref, "CL", "null")
    return _info(fitz_doc, page, page_index, page.load_annot(xref), {**annot.info, "id": name})


def _set_rect(fitz_doc: pymupdf.Document, annot: pymupdf.Annot, rect: pymupdf.Rect) -> None:
    """``annot.set_rect(rect)`` without the /CL it writes (A1)."""
    annot.set_rect(rect)
    fitz_doc.xref_set_key(annot.xref, "CL", "null")


def _on_page(page: pymupdf.Page, rect: pymupdf.Rect) -> pymupdf.Rect:
    """Unrotated ``rect`` cut to the page (a fitted box taller than the space left below
    its top edge is capped: the text is then clipped), unless nothing would remain."""
    bounds = page.rect * page.derotation_matrix  # the page in unrotated coordinates
    clipped = pymupdf.Rect(rect) & bounds.normalize()
    if clipped.is_empty or clipped.width < 1 or clipped.height < 1 or clipped == rect:
        return rect
    return clipped


def delete_annot(fitz_doc: pymupdf.Document, page_index: int, name: str) -> bool:
    """Delete the annotation named ``name``; False if it is not there."""
    found = resolve_annot(fitz_doc, page_index, name)
    if found is None:
        return False
    page, annot = found
    if _info(fitz_doc, page, page_index, annot).locked:
        raise PermissionError(f"annotation {name!r} is locked")
    page.delete_annot(annot)
    return True


# -- geometry helpers --------------------------------------------------------
def spec_from(info: AnnotInfo, image: ImageData | None = None) -> AnnotSpec:
    """The spec re-creating ``info`` (same /NM, rect, rotation, style; a markup its
    quads, colour, opacity and /Contents; a text box its width mode; a signature also
    needs its ``image``, e.g. from ``PdfDocument.annot_image``)."""
    return AnnotSpec(
        page=info.page,
        kind=info.kind,
        text=info.text,
        font_size=info.font_size,
        color=info.color,
        rect=QRectF(info.rect),
        name=info.name,
        rotate=info.rotate,
        image=image,
        quads=info.quads,
        opacity=info.opacity,
        fixed_width=info.fixed_width,
        max_width=info.max_width,
    )


def stamp_rect(centre: QPointF, size: float, glyph: str) -> tuple[QRectF, float]:
    """(page-space rect, font size) of a stamp whose glyph is centred on ``centre`` and
    fills a box of side ``size``."""
    fs = STAMP_FONT_RATIO * size
    cx, cy = STAMP_CENTRE[glyph]
    return QRectF(centre.x() - cx * fs, centre.y() - cy * fs, size, size), fs


def text_rect(left: float, baseline: float, width: float, font_size: float) -> QRectF:
    """Page-space rect of a one-line text box whose first baseline is at ``baseline``."""
    return QRectF(left, baseline - BASELINE_RATIO * font_size, width, fitted_height(1, font_size))
