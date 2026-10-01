"""FreeText annotations (text boxes and stamps, M3) and signatures (M4).

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
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum

import pymupdf
from PySide6.QtCore import QPointF, QRectF

from pdfeditor.core import signature
from pdfeditor.core.forms import ANNOT_HIDDEN, ANNOT_NO_VIEW, _page_rect
from pdfeditor.core.geometry import fitz_from_qrect, page_to_unrotated
from pdfeditor.core.signature import ImageData

log = logging.getLogger(__name__)

Color = tuple[float, float, float]


class AnnotKind(StrEnum):
    TEXT = "text"
    STAMP = "stamp"
    SIGNATURE = "signature"


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
    """Snapshot of one FreeText annotation or signature (no live pymupdf object)."""

    page: int
    #: Xref of the annotation (stale after a full save; identity is ``name``).
    xref: int
    #: The annotation's /NM, or a synthetic name (:func:`is_synthetic`) for a foreign
    #: FreeText without a unique /NM.
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
    #: /F ReadOnly or Locked, or a callout (/IT /FreeTextCallout): never changed here.
    locked: bool = False
    #: /F LockedContents: the text cannot be edited (moving and resizing can).
    locked_contents: bool = False
    #: Signature only: xref of its image (stale after a full save) and its pixel size
    #: as embedded (pre-rotated for the page).
    image_xref: int = 0
    image_size: tuple[int, int] = (0, 0)

    @property
    def editable(self) -> bool:
        """The user can select, move and edit this annotation."""
        return not self.hidden and not self.locked and not self.rect.isEmpty()

    @property
    def text_editable(self) -> bool:
        """The user can change the text of this (editable) annotation."""
        return self.editable and not self.locked_contents and self.kind is not AnnotKind.SIGNATURE


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
    )


def _signature_info(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    index: int,
    annot: pymupdf.Annot,
    details: dict[str, str] | None,
    image_xref: int | None,
) -> AnnotInfo:
    """Snapshot of signature ``annot`` (locked when its image has no valid size)."""
    xref = int(annot.xref)
    if details is None:
        details = annot.info
    if image_xref is None:
        image_xref = signature.signature_image_xref(doc, page, annot)
    size = signature.image_size(doc, image_xref) if image_xref else (0, 0)
    ok = size[0] > 0 and size[1] > 0
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


def read_one(fitz_doc: pymupdf.Document, page_index: int, xref: int) -> AnnotInfo:
    """Snapshot of annotation ``xref`` of a page (FreeText or signature; real /NM)."""
    page = fitz_doc[page_index]
    return _info(fitz_doc, page, page_index, page.load_annot(xref))


def _candidate(fitz_doc: pymupdf.Document, xref: int, subtype: int) -> bool:
    """Annotation ``xref`` of ``subtype`` may be one we list (cheap check)."""
    if subtype == pymupdf.PDF_ANNOT_FREE_TEXT:
        return True
    return subtype == pymupdf.PDF_ANNOT_STAMP and signature.is_signature_intent(fitz_doc, xref)


def new_name() -> str:
    """A fresh /NM value."""
    return str(uuid.uuid4())


def synthetic_name(xref: int, scope: str = "") -> str:
    """The in-memory name of a FreeText without a unique /NM (valid for ``scope``)."""
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
    """FreeText annotations and signatures of a page, in /Annots order; hidden ones
    skipped by default.

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
    """The live FreeText annotation or signature named ``name`` on a page, as
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
    """Create a FreeText annotation or signature from ``spec`` and return its snapshot.

    ``fit_height`` (text only): the height then hugs the wrapped text (see
    :func:`update_annot`); otherwise ``spec.rect`` is used as is. A signature gets an
    image object of its own (``PdfDocument.add_annot`` shares them instead).
    """
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
    top edge and the width, capped at the page edge (longer text is clipped). Foreign
    annotations are normalised to Helvetica (stamps to ZapfDingbats) and lose their rich
    text (/RC, /DS); /CL is always removed. The returned snapshot keeps ``name`` (even
    synthetic). A signature honours ``rect`` only (the rest is ignored; without a rect
    nothing is written). Raises ``LookupError`` if the annotation is gone,
    ``PermissionError`` if it is :attr:`AnnotInfo.locked`.
    """
    found = resolve_annot(fitz_doc, page_index, name)
    if found is None:
        raise LookupError(f"annotation {name!r} not found on page {page_index}")
    page, annot = found
    xref = annot.xref
    current = _info(fitz_doc, page, page_index, annot)
    if current.locked:
        raise PermissionError(f"annotation {name!r} is locked")
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
    kwargs = {"fontsize": _pdf_size(size), "fontname": _fontname(kind), "text_color": rgb}
    if fit_height and kind is AnnotKind.TEXT:
        # Measure with a frame tall enough for every line, then hug the content.
        body = text if text is not None else current.text
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
    """The spec re-creating ``info`` (same /NM, rect, rotation, style; a signature also
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
