"""FreeText annotations: text boxes and stamps (M3).

Pure functions on ``pymupdf.Document``. Callers hold ``PdfDocument.lock``. Never keep
``pymupdf.Page`` / ``Annot`` objects across calls (every save replaces the document and
full saves renumber xrefs); while using an ``Annot``, keep its ``Page`` referenced
(deleting an annotation loaded from a temporary page crashes MuPDF).

Identity is the annotation's ``/NM`` (a uuid4 written at creation; foreign FreeText
annotations without a unique ``/NM`` get one when read), resolved again with
:func:`resolve_annot`. Xrefs are never used as identity.

Geometry: callers work in *page space* (rotation applied, cropbox-relative, points).
The unrotated rect stored in the file is ``page_to_unrotated(rect)`` and the text is
drawn with ``/Rotate`` = the page rotation at creation, so it reads upright on screen.

Only FreeText annotations are handled. Text uses Helvetica (``/Helv``), no border, no
fill; stamps are FreeText annotations showing one ZapfDingbats glyph
(:data:`STAMP_GLYPHS`). Other subtypes (and widgets) are ignored.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum

import pymupdf
from PySide6.QtCore import QPointF, QRectF

from pdfeditor.core.forms import ANNOT_HIDDEN, ANNOT_NO_VIEW, _page_rect
from pdfeditor.core.geometry import fitz_from_qrect, page_to_unrotated

log = logging.getLogger(__name__)

Color = tuple[float, float, float]


class AnnotKind(StrEnum):
    TEXT = "text"
    STAMP = "stamp"


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
DEFAULT_FONT_SIZE = 11.0
BLACK: Color = (0.0, 0.0, 0.0)

_TEXT_FONT = "helv"
_STAMP_FONT = "zadb"


@dataclass(frozen=True)
class AnnotInfo:
    """Snapshot of one FreeText annotation (no live pymupdf object)."""

    page: int
    #: Xref of the annotation (stale after a full save; identity is ``name``).
    xref: int
    #: The annotation's /NM.
    name: str
    kind: AnnotKind
    #: /Contents (line breaks normalised to "\n").
    text: str
    #: Font size from /DA.
    font_size: float
    #: Text colour (RGB, 0..1) from /DA.
    color: Color
    #: The annotation's /Rotate (0 when absent).
    rotate: int
    hidden: bool
    #: Rect in page space (rotation applied); empty for a missing/degenerate /Rect.
    rect: QRectF
    #: Raw /Rect (x0, y0, x1, y1) in unrotated page coordinates (``annot.rect``).
    unrotated_rect: tuple[float, float, float, float]

    @property
    def editable(self) -> bool:
        """The user can select, move and edit this annotation."""
        return not self.hidden and not self.rect.isEmpty()


@dataclass(frozen=True)
class AnnotSpec:
    """Everything needed to create an annotation."""

    page: int
    kind: AnnotKind
    #: Text, or the ZapfDingbats code of a stamp (a value of :data:`STAMP_GLYPHS`).
    text: str
    font_size: float
    color: Color
    #: Rect in page space.
    rect: QRectF
    #: /NM to write; "" = generate a uuid4.
    name: str = ""
    #: /Rotate of the text; None = the page rotation at creation (upright on screen).
    rotate: int | None = None


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


def _kind(font: str, text: str) -> AnnotKind:
    if font == "ZaDb" and text in STAMP_CENTRE:
        return AnnotKind.STAMP
    return AnnotKind.TEXT


def _info(doc: pymupdf.Document, page: pymupdf.Page, index: int, annot: pymupdf.Annot) -> AnnotInfo:
    xref = int(annot.xref)
    font, size, color = parse_da(_string_key(doc, xref, "DA"))
    text = str(annot.info.get("content") or "")
    raw = pymupdf.Rect(annot.rect)
    rotate = _int_key(doc, xref, "Rotate") % 360 // 90 * 90
    flags = int(annot.flags or 0)
    return AnnotInfo(
        page=index,
        xref=xref,
        name=str(annot.info.get("id") or ""),
        kind=_kind(font, text),
        text=text,
        font_size=size or DEFAULT_FONT_SIZE,
        color=color,
        rotate=rotate,
        hidden=bool(flags & (ANNOT_HIDDEN | ANNOT_NO_VIEW)),
        rect=_page_rect(raw, page),
        unrotated_rect=(raw.x0, raw.y0, raw.x1, raw.y1),
    )


def new_name() -> str:
    """A fresh /NM value."""
    return str(uuid.uuid4())


# -- reading ---------------------------------------------------------------
def read_annots(
    fitz_doc: pymupdf.Document, page_index: int, *, include_hidden: bool = False
) -> list[AnnotInfo]:
    """FreeText annotations of a page, in /Annots order; hidden ones skipped by default.

    A FreeText annotation without /NM, or whose /NM repeats an earlier one of the page,
    is given a fresh uuid4 /NM (this modifies the document).
    """
    page = fitz_doc[page_index]  # keep the Page alive while its annots are used
    out: list[AnnotInfo] = []
    seen: set[str] = set()
    for xref, subtype, name in page.annot_xrefs():
        if subtype != pymupdf.PDF_ANNOT_FREE_TEXT:
            continue
        try:
            if not name or name in seen:
                name = new_name()
                _set_name(fitz_doc, xref, name)
                log.info("assigned /NM %s to FreeText xref %s", name, xref)
            seen.add(name)
            info = _info(fitz_doc, page, page_index, page.load_annot(xref))
        except Exception:  # one malformed annotation must not hide the others
            log.warning("skipping unreadable FreeText xref %s", xref, exc_info=True)
            continue
        if include_hidden or not info.hidden:
            out.append(info)
    return out


def resolve_annot(
    fitz_doc: pymupdf.Document, page_index: int, name: str
) -> tuple[pymupdf.Page, pymupdf.Annot] | None:
    """The live FreeText annotation named ``name`` on a page, as ``(page, annot)`` — keep
    the page referenced while using the annot — or None."""
    if not name:
        return None
    page = fitz_doc[page_index]
    for xref, subtype, nm in page.annot_xrefs():
        if subtype == pymupdf.PDF_ANNOT_FREE_TEXT and nm == name:
            return page, page.load_annot(xref)
    return None


# -- writing ---------------------------------------------------------------
def _pdf_size(size: float) -> float | int:
    """Font size as written to /DA ("11", not "11.0", for whole sizes)."""
    return int(size) if float(size).is_integer() else float(size)


def _fontname(kind: AnnotKind) -> str:
    return _STAMP_FONT if kind is AnnotKind.STAMP else _TEXT_FONT


def create_annot(
    fitz_doc: pymupdf.Document, page_index: int, spec: AnnotSpec, *, fit_height: bool = False
) -> AnnotInfo:
    """Create a FreeText annotation from ``spec`` and return its snapshot.

    ``fit_height`` (text only): the height then hugs the wrapped text (see
    :func:`update_annot`); otherwise ``spec.rect`` is used as is.
    """
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
    info = _info(fitz_doc, page, page_index, page.load_annot(xref))
    if fit_height and info.kind is AnnotKind.TEXT:
        return update_annot(fitz_doc, page_index, info.name, fit_height=True)
    return info


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
) -> AnnotInfo:
    """Change an annotation and regenerate its appearance; returns the new snapshot.

    ``None`` keeps a property. ``rect`` is in page space. For a stamp resized without an
    explicit ``font_size`` the glyph is scaled to ``STAMP_FONT_RATIO x min(w, h)``.
    ``fit_height`` (text only) then sets the height to hug the wrapped text, keeping the
    top edge and the width. Foreign annotations are normalised to Helvetica (stamps to
    ZapfDingbats) and lose their rich text (/RC, /DS). Raises ``LookupError`` if the
    annotation is gone.
    """
    found = resolve_annot(fitz_doc, page_index, name)
    if found is None:
        raise LookupError(f"annotation {name!r} not found on page {page_index}")
    page, annot = found
    xref = annot.xref
    current = _info(fitz_doc, page, page_index, annot)
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
        annot.set_rect(page_to_unrotated(fitz_from_qrect(rect), page.derotation_matrix))
    kwargs = {"fontsize": _pdf_size(size), "fontname": _fontname(kind), "text_color": rgb}
    if fit_height and kind is AnnotKind.TEXT:
        # Measure with a frame tall enough for every line, then hug the content.
        body = text if text is not None else current.text
        tall = fitted_height(len(body) + body.count("\n") + 2, size)
        annot.set_rect(_with_frame_height(annot.rect, current.rotate, tall))
        annot.update(**kwargs)
        lines = _line_count(page, annot, current.rotate, size)
        annot.set_rect(_with_frame_height(annot.rect, current.rotate, fitted_height(lines, size)))
    annot.update(**kwargs)
    return _info(fitz_doc, page, page_index, page.load_annot(xref))


def delete_annot(fitz_doc: pymupdf.Document, page_index: int, name: str) -> bool:
    """Delete the annotation named ``name``; False if it is not there."""
    found = resolve_annot(fitz_doc, page_index, name)
    if found is None:
        return False
    page, annot = found
    page.delete_annot(annot)
    return True


# -- geometry helpers --------------------------------------------------------
def spec_from(info: AnnotInfo) -> AnnotSpec:
    """The spec re-creating ``info`` (same /NM, rect, rotation, style)."""
    return AnnotSpec(
        page=info.page,
        kind=info.kind,
        text=info.text,
        font_size=info.font_size,
        color=info.color,
        rect=QRectF(info.rect),
        name=info.name,
        rotate=info.rotate,
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
