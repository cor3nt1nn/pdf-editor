"""Replace a run of existing page text in place (M7, docs/M7_PLAN.md §1.2).

The unit of an edit is a :class:`Run`: consecutive characters of one line inside one span
of :class:`~pdfeditor.core.pagetext.PageText` (flat char indexes, inclusive). The caller
holds ``PdfDocument.lock``; :meth:`PdfDocument.replace_text_run` is the entry point.

:func:`replace_run` works in four moves, every one of them verified by the planner's
experiments (plan facts R1–R8, U1–U3, F1–F10):

1. **Snapshot** ``before = page.read_contents()``, the page's ``/Resources`` value (own
   or inherited) and its ``/Annots`` value. A page whose content exceeds
   :data:`MAX_CONTENT_BYTES` is refused (two copies travel with the undo command).
2. **Remove** the run's glyphs with a redaction (``fill=False``, images and line art kept,
   ``PDF_REDACT_TEXT_REMOVE``): only the glyphs whose ink box meets the rect go, the
   others keep their exact origins (R1/R2). The redaction rect is the union of the chars'
   boxes mapped with ``page.derotation_matrix`` (R4). The page's ``/Annots`` is swapped
   for a fresh ``[]`` holding only the redaction annotation while it is applied, so that
   no other annotation is deleted (R6) and an indirect ``/Annots`` array is never touched;
   afterwards the original value is put back (or the key deleted when there was none —
   ``/Annots null`` fails pypdf's strict reader). MuPDF rewrites ``/Resources`` as a
   pruned direct dictionary with renamed XObjects (R5): the original value is restored and
   every name MuPDF introduced is added to it (U2), so both the old and the new content
   bytes are valid against the page's resources.
3. **Collateral loop**: the page text is read again and compared with the old one; a
   char that vanished outside the run (a neighbour whose box overlaps, e.g. tight kerning)
   makes the run grow to include it, the content is restored and step 2 runs again (at
   most :data:`MAX_COLLATERAL_ROUNDS` times). The grown run is reported
   (:attr:`TextEditResult.extended_run`) and its outer characters are written back
   unchanged around the new text.
4. **Write** the new text: font from :func:`pdfeditor.core.fontmatch.match` (the span's
   own embedded font when it has every glyph, else an installed face embedded by
   :mod:`pdfeditor.core.fontembed`), ``q BT /F size Tf r g b rg [Tz] Tm <hex> Tj ET Q``
   appended to the single consolidated stream (the existing content is wrapped in
   ``q``/``Q`` first when its graphics state is unbalanced). The text matrix is the span's
   direction and first char origin taken from page space to content space through the
   inverse of ``pdf_page_transform`` (F4). A wider text is narrowed with ``Tz`` down to
   :data:`MAX_TZ_SQUEEZE` %, beyond which it overflows (reported).

Coincident duplicate text (fake bold: the same chars drawn twice at the same origin) is
edited in every copy when the copies match char for char — the run's ops are written once
per copy — and refused (``EditReason.DUPLICATE``) when only part of the run is doubled.
Right-to-left and vertical runs are refused (``EditReason.DIRECTION``).

:func:`set_page_content` restores or reapplies a content snapshot (undo/redo): the
single stream is rewritten (``update_stream(compress=True)``), or a new stream replaces a
``/Contents`` array, which survives ``garbage=3`` renumbering (U1). With ``expect`` the
current bytes must match, else ``EditReason.STALE``.

No user-visible string lives here: :class:`TextEditError` carries an :class:`EditReason`
and :class:`TextEditResult` the facts (font plan, extension, narrowing) that the UI turns
into translated notices.
"""

from __future__ import annotations

import enum
import logging
import math
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import NamedTuple

import pymupdf
from PySide6.QtCore import QPointF, QRectF

from pdfeditor.core import fontembed, fontmatch, fontread, pagetext
from pdfeditor.core.document import DocumentError
from pdfeditor.core.fontembed import FontEmbedError, M7Font
from pdfeditor.core.fontinfo import EmbeddedFont, FontKind
from pdfeditor.core.fontmatch import FontPlan, PlanKind, SystemFonts
from pdfeditor.core.pagetext import Char, CharRef, Line, PageText, Span
from pdfeditor.core.pdfdict import inherited

log = logging.getLogger(__name__)

#: Narrowest horizontal scaling (``Tz`` percent) applied to a text wider than the original.
MAX_TZ_SQUEEZE = 85.0
#: A new text up to this much wider (points) than the original is not narrowed.
WIDTH_SLACK_PT = 1.0
#: Largest page content (bytes) that may be edited: the undo command keeps two copies.
MAX_CONTENT_MB = 20
MAX_CONTENT_BYTES = MAX_CONTENT_MB * 1024 * 1024
#: Rounds of the collateral loop (each one grows the run by the chars it caught).
MAX_COLLATERAL_ROUNDS = 3
#: Two chars this close (points) with the same text are coincident duplicates.
TWIN_TOLERANCE_PT = pagetext.TWIN_TOLERANCE_PT

_REDACT_KW = {
    "images": pymupdf.PDF_REDACT_IMAGE_NONE,
    "graphics": pymupdf.PDF_REDACT_LINE_ART_NONE,
    "text": pymupdf.PDF_REDACT_TEXT_REMOVE,
}
#: pagetext span flags (PyMuPDF TEXT_FONT_*).
_FLAG_ITALIC, _FLAG_SERIF, _FLAG_MONO, _FLAG_BOLD = 2, 4, 8, 16
_RTL_CLASSES = ("R", "AL")


class EditReason(enum.StrEnum):
    """Why a text edit was refused or failed (the UI picks the translated notice)."""

    #: The run names no character (scanned page, outlined text, empty run).
    NO_TEXT = "no_text"
    #: Invisible text (render mode 3, e.g. an OCR layer over an image).
    INVISIBLE = "invisible"
    #: Text drawn by a Form XObject (its font is not a page resource).
    XOBJECT = "xobject"
    #: The run spans several spans/lines (the UI clamps to one span).
    MULTI_SPAN = "multi_span"
    #: Right-to-left or vertical text.
    DIRECTION = "direction"
    #: Coincident duplicate text (fake bold) that does not double the whole run.
    DUPLICATE = "duplicate"
    #: The document's permissions forbid modifying it.
    PERMISSION = "permission"
    #: The page content is larger than :data:`MAX_CONTENT_BYTES`.
    TOO_COMPLEX = "too_complex"
    #: No font can show the new text (not even a generic installed one).
    NO_FONT = "no_font"
    #: The page content changed since the snapshot (undo guard), or the run does not
    #: match the page's text any more.
    STALE = "stale"
    #: Anything else (MuPDF error, glyphs that could not be removed...).
    FAILED = "failed"


class TextEditError(DocumentError):
    """A page-text edit was refused or failed; ``reason`` says why."""

    def __init__(self, message: str, reason: EditReason = EditReason.FAILED) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, order=True)
class Run:
    """Consecutive chars ``first``..``last`` (inclusive flat indexes of a
    :class:`PageText`, either order) of one line inside one span."""

    first: int
    last: int

    def __post_init__(self) -> None:
        a, b = int(self.first), int(self.last)
        if a < 0 or b < 0:
            raise ValueError(f"negative char index: {a}, {b}")
        object.__setattr__(self, "first", min(a, b))
        object.__setattr__(self, "last", max(a, b))

    @classmethod
    def from_refs(cls, a: CharRef | int, b: CharRef | int) -> Run:
        return cls(_index(a), _index(b))

    @property
    def length(self) -> int:
        return self.last - self.first + 1

    @property
    def indexes(self) -> range:
        return range(self.first, self.last + 1)

    def contains(self, index: int) -> bool:
        return self.first <= index <= self.last

    def text(self, page_text: PageText) -> str:
        """The run's text in ``page_text`` (``IndexError`` when out of range)."""
        chars = page_text.chars
        if self.last >= len(chars):
            raise IndexError(self.last)
        return "".join(chars[i].c for i in self.indexes)


@dataclass(frozen=True)
class TextEditResult:
    """What :func:`replace_run` did (the undo command keeps ``before``/``after``).

    ``run`` is the run asked for, ``extended_run`` the run actually rewritten when
    neighbouring chars had to be included (else ``None``); ``old_text`` is the text of the
    rewritten run and ``new_text`` what was written in its place (``requested_text``
    surrounded by the extension's outer chars). ``plan`` is the font used (``None`` for a
    pure removal); ``scaling`` the ``Tz`` percent (100 = natural width) and ``overflow``
    whether the text is still wider than the original at :data:`MAX_TZ_SQUEEZE`;
    ``copies`` the number of coincident copies rewritten (fake bold).
    """

    page: int
    before: bytes
    after: bytes
    run: Run
    old_text: str
    new_text: str
    requested_text: str
    extended_run: Run | None = None
    plan: FontPlan | None = None
    scaling: float = 100.0
    overflow: bool = False
    natural_width: float = 0.0
    target_width: float = 0.0
    copies: int = 1

    @property
    def extended(self) -> bool:
        return self.extended_run is not None

    @property
    def narrowed(self) -> bool:
        return self.scaling < 100.0

    @property
    def substituted(self) -> bool:
        """Another font than the document's wrote the text (the UI warns)."""
        return self.plan is not None and self.plan.warning is not None

    @property
    def font_family(self) -> str:
        return self.plan.family if self.plan is not None else ""

    @property
    def font_kind(self) -> PlanKind | None:
        return self.plan.kind if self.plan is not None else None


class _Target(NamedTuple):
    """A run resolved against a :class:`PageText`."""

    indexes: tuple[int, ...]
    line_index: int
    line: Line
    span: Span
    chars: tuple[Char, ...]


def _index(ref: CharRef | int) -> int:
    return ref.index if isinstance(ref, CharRef) else int(ref)


# -- geometry -----------------------------------------------------------------------------
def page_to_pdf_matrix(page: pymupdf.Page) -> pymupdf.Matrix:
    """Page space (rotated, cropbox-relative) → content space: the inverse of the page's
    ``pdf_page_transform`` matrix (exact for 0/90/180/270 with or without a cropbox,
    plan F4)."""
    mupdf = pymupdf.mupdf
    ppage = mupdf.pdf_page_from_fz_page(page.this)
    mediabox = mupdf.FzRect()
    ctm = mupdf.FzMatrix()
    mupdf.pdf_page_transform(ppage, mediabox, ctm)
    return ~pymupdf.Matrix(ctm.a, ctm.b, ctm.c, ctm.d, ctm.e, ctm.f)


def _unit(d: tuple[float, float]) -> tuple[float, float]:
    n = math.hypot(d[0], d[1]) or 1.0
    return d[0] / n, d[1] / n


def _project(p: QPointF, d: tuple[float, float]) -> float:
    return p.x() * d[0] + p.y() * d[1]


def _corners(r: QRectF) -> tuple[QPointF, QPointF, QPointF, QPointF]:
    return r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()


def _union(rects: Iterable[QRectF]) -> QRectF:
    out = QRectF()
    for r in rects:
        out = QRectF(r) if out.isNull() else out.united(r)
    return out


def fit_scaling(natural: float, target: float) -> tuple[float, bool]:
    """``(Tz percent, overflow)`` to fit a text of ``natural`` width into ``target``
    points: nothing up to :data:`WIDTH_SLACK_PT` wider, else narrowed down to
    :data:`MAX_TZ_SQUEEZE` %, beyond which it stays at that scaling and overflows."""
    if natural <= 0 or natural <= target + WIDTH_SLACK_PT:
        return 100.0, False
    tz = 100.0 * target / natural
    if tz >= MAX_TZ_SQUEEZE:
        return tz, False
    return MAX_TZ_SQUEEZE, True


# -- resolving the run --------------------------------------------------------------------
def _resolve(text: PageText, run: Run) -> _Target:
    chars = text.chars
    if not chars:
        raise TextEditError("the page has no text", EditReason.NO_TEXT)
    if run.last >= len(chars):
        raise TextEditError("the run is outside the page's text", EditReason.STALE)
    indexes = tuple(run.indexes)
    span = text.span_of(indexes[0])
    line_index = text.ref(indexes[0]).line
    if any(text.span_of(i) is not span for i in indexes):
        raise TextEditError("the run covers several spans", EditReason.MULTI_SPAN)
    line = text.lines[line_index]
    return _Target(indexes, line_index, line, span, tuple(chars[i] for i in indexes))


def _check_editable(text: PageText, target: _Target, new_text: str) -> None:
    span = target.span
    if span.in_xobject:
        raise TextEditError("the text is drawn by a Form XObject", EditReason.XOBJECT)
    if span.invisible or any(text.is_invisible(i) for i in target.indexes):
        raise TextEditError("the text is invisible (OCR layer)", EditReason.INVISIBLE)
    if target.line.wmode != 0:
        raise TextEditError("vertical text", EditReason.DIRECTION)
    old = "".join(ch.c for ch in target.chars)
    if _has_rtl(old) or _has_rtl(new_text) or _runs_backwards(target):
        raise TextEditError("right-to-left text", EditReason.DIRECTION)


def _has_rtl(s: str) -> bool:
    return any(unicodedata.bidirectional(ch) in _RTL_CLASSES for ch in s)


def _runs_backwards(target: _Target) -> bool:
    """The chars' origins decrease along the line direction (RTL run in logical order)."""
    if len(target.chars) < 2:
        return False
    d = _unit(target.line.dir)
    pos = [_project(ch.origin, d) for ch in target.chars]
    return all(b < a - 1e-3 for a, b in zip(pos, pos[1:], strict=False))


def _char_key(ch: Char) -> tuple[str, float, float]:
    return ch.c, round(ch.origin.x(), 1), round(ch.origin.y(), 1)


def _twins(text: PageText, indexes: Sequence[int]) -> tuple[int, list[int]]:
    """``(copies, twin indexes)``: coincident duplicates of the run's chars (same text,
    origin within :data:`TWIN_TOLERANCE_PT`, same font and size) in other spans, found
    by :meth:`PageText.twins_of` (also behind the selection's :attr:`PageText.duplicates`).
    Every duplicating span must double the whole run, else ``EditReason.DUPLICATE``."""
    run_set = set(indexes)
    span = text.span_of(indexes[0])
    groups: dict[int, set[int]] = {}  # id(twin span) -> run indexes it doubles
    twins: list[int] = []
    for i in indexes:
        for j in text.twins_of(i):
            if j in run_set:
                continue
            twin_span = text.span_of(j)
            if twin_span.font != span.font or abs(twin_span.size - span.size) > 0.01:
                raise TextEditError("coincident text in another font", EditReason.DUPLICATE)
            groups.setdefault(id(twin_span), set()).add(i)
            twins.append(j)
    for covered in groups.values():
        if covered != run_set:
            raise TextEditError("part of the run is drawn twice", EditReason.DUPLICATE)
    return 1 + len(groups), twins


# -- the document side --------------------------------------------------------------------
def _key(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        return doc.xref_get_key(xref, key)
    except Exception:  # noqa: BLE001 - a broken object reads as absent
        return "null", "null"


def _resources_value(doc: pymupdf.Document, page_xref: int) -> str:
    """The page's ``/Resources`` source to restore after a redaction: its own value, else
    the inherited one (the page then gets its own entry), else an empty dictionary."""
    kind, value = inherited(doc, page_xref, "Resources")
    return value if kind != "null" else "<<>>"


def _remove(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    rect: QRectF,
    resources: str,
    annots: tuple[str, str],
) -> pymupdf.Page:
    """Remove the glyphs meeting ``rect`` (page space); keep annotations and resources
    (module docstring, step 2). Returns the reloaded page."""
    page_xref = page.xref
    r = pymupdf.Rect(rect.left(), rect.top(), rect.right(), rect.bottom())
    r = (r * page.derotation_matrix).normalize()
    doc.xref_set_key(page_xref, "Annots", "[]")
    try:
        page = _reload(doc, page)
        page.add_redact_annot(r, fill=False)
        page.apply_redactions(**_REDACT_KW)
        _merge_resources(doc, page_xref, resources)
    finally:
        if annots[0] == "null":
            mupdf = pymupdf.mupdf
            pdf = pymupdf._as_pdf_document(doc)
            mupdf.pdf_dict_dels(mupdf.pdf_new_indirect(pdf, page_xref, 0), "Annots")
        else:
            doc.xref_set_key(page_xref, "Annots", annots[1])
    return _reload(doc, page)


def _merge_resources(doc: pymupdf.Document, page_xref: int, original: str) -> None:
    """Put the ``original`` ``/Resources`` value back and add every entry of the
    dictionary MuPDF wrote (renamed XObjects, copied forms) that it lacks (U2). Works on
    the MuPDF objects so direct and indirect sub-dictionaries and values are treated
    alike; a shared indirect dictionary becomes a superset, which is harmless."""
    mupdf = pymupdf.mupdf
    pdf = pymupdf._as_pdf_document(doc)
    page_ref = mupdf.pdf_new_indirect(pdf, page_xref, 0)
    rewritten = mupdf.pdf_dict_get(page_ref, mupdf.PDF_ENUM_NAME_Resources)
    rewritten = mupdf.pdf_deep_copy_obj(mupdf.pdf_resolve_indirect(rewritten))
    doc.xref_set_key(page_xref, "Resources", original)
    res = mupdf.pdf_resolve_indirect(mupdf.pdf_dict_get(page_ref, mupdf.PDF_ENUM_NAME_Resources))
    if not mupdf.pdf_is_dict(res):
        raise TextEditError("the page's /Resources is not a dictionary")
    for i in range(mupdf.pdf_dict_len(rewritten)):
        key = mupdf.pdf_dict_get_key(rewritten, i)
        value = mupdf.pdf_dict_get_val(rewritten, i)
        current = mupdf.pdf_dict_get(res, key)
        if not current.m_internal or mupdf.pdf_is_null(current):
            mupdf.pdf_dict_put(res, key, value)
            continue
        new_sub = mupdf.pdf_resolve_indirect(value)
        cur_sub = mupdf.pdf_resolve_indirect(current)
        if not (mupdf.pdf_is_dict(new_sub) and mupdf.pdf_is_dict(cur_sub)):
            continue
        for j in range(mupdf.pdf_dict_len(new_sub)):
            name = mupdf.pdf_dict_get_key(new_sub, j)
            entry = mupdf.pdf_dict_get_val(new_sub, j)
            existing = mupdf.pdf_dict_get(cur_sub, name)
            if not existing.m_internal or mupdf.pdf_is_null(existing):
                mupdf.pdf_dict_put(cur_sub, name, entry)
            elif mupdf.pdf_objcmp(existing, entry):
                log.info(
                    "page %d: resource /%s/%s differs after the redaction; keeping the original",
                    page_xref,
                    mupdf.pdf_to_name(key),
                    mupdf.pdf_to_name(name),
                )


def _content_shared(doc: pymupdf.Document, page_xref: int, stream_xref: int) -> bool:
    """Another page's ``/Contents`` names ``stream_xref`` too."""
    needle = f"{stream_xref} 0 R"
    for pno in range(doc.page_count):
        try:
            other = doc.page_xref(pno)
        except Exception:  # noqa: BLE001
            continue
        if other == page_xref:
            continue
        kind, value = _key(doc, other, "Contents")
        if kind in ("xref", "array") and needle in value:
            return True
    return False


def set_page_content(
    doc: pymupdf.Document, page: pymupdf.Page, data: bytes, expect: bytes | None = None
) -> pymupdf.Page:
    """Make ``data`` the page's whole content (one compressed stream) and return the
    reloaded page. With ``expect``, the current content must equal it
    (``EditReason.STALE``). ``EditReason.TOO_COMPLEX`` above :data:`MAX_CONTENT_BYTES`."""
    if len(data) > MAX_CONTENT_BYTES:
        raise TextEditError("the page content is too large", EditReason.TOO_COMPLEX)
    if expect is not None and page.read_contents() != expect:
        raise TextEditError("the page content changed since this edit", EditReason.STALE)
    streams = page.get_contents()
    if len(streams) == 1 and not _content_shared(doc, page.xref, streams[0]):
        doc.update_stream(streams[0], data, compress=True)
    else:
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        doc.update_stream(xref, data, compress=True)
        doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")
    return _reload(doc, page)


def _reload(doc: pymupdf.Document, page: pymupdf.Page) -> pymupdf.Page:
    """``doc.reload_page(page)``; when another ``Page`` object still holds the same MuPDF
    page (a caller's, or the render thread's — PyMuPDF then asserts), the page is fetched
    again and its annotation and link lists are re-read from the page dictionary
    (``pdf_sync_page``): the content is read from the xref on every run, but the
    ``/Annots`` swap of :func:`_remove` must reach the in-memory annotation list."""
    pno = page.number
    try:
        return doc.reload_page(page)
    except AssertionError:
        log.info("page %d is held elsewhere; syncing it instead of reloading", pno)
        page = doc[pno]
        mupdf = pymupdf.mupdf
        mupdf.pdf_sync_page(mupdf.pdf_page_from_fz_page(page.this))
        return page


# -- fonts and widths ---------------------------------------------------------------------
def _span_font_stub(span: Span) -> EmbeddedFont:
    """An :class:`EmbeddedFont` describing a span whose font object is unknown (so that
    :func:`fontmatch.match` can look for an installed face by name and style)."""
    family, style = fontread.split_base_font(span.font)
    lowered = style.lower()
    return EmbeddedFont(
        xref=0,
        resource_name="",
        kind=FontKind.OTHER,
        base_font=span.font,
        family=family,
        style=style,
        bold=bool(span.flags & _FLAG_BOLD) or "bold" in lowered,
        italic=bool(span.flags & _FLAG_ITALIC) or "italic" in lowered or "oblique" in lowered,
        serif=bool(span.flags & _FLAG_SERIF),
        mono=bool(span.flags & _FLAG_MONO),
    )


def _choose_font(
    doc: pymupdf.Document,
    page_xref: int,
    span: Span,
    text: str,
    fonts: SystemFonts | None,
) -> tuple[FontPlan, M7Font | EmbeddedFont, str, EmbeddedFont]:
    """``(plan, font, resource name, document font)`` to write ``text`` in place of
    ``span``'s text; the document font is the span's own (a stub when unknown)."""
    embedded: EmbeddedFont | None = None
    if span.font_xref and span.resource_name:
        embedded = fontread.read_embedded_font(doc, span.font_xref, span.resource_name)
        if not embedded.family:
            embedded = _span_font_stub(span)
    if embedded is None:
        embedded = _span_font_stub(span)
    plan = fontmatch.match(embedded, text, fonts=fonts)
    log.debug("font plan for %r: %s", text, plan)
    if plan.kind is PlanKind.REUSE:
        return plan, embedded, span.resource_name, embedded
    if plan.path is None:
        raise TextEditError(f"no installed font can show {text!r}", EditReason.NO_FONT)
    try:
        font = fontembed.ensure_font(doc, page_xref, plan.path, plan.index, text)
    except FontEmbedError as exc:
        raise TextEditError(str(exc), EditReason.NO_FONT) from exc
    return plan, font, font.resource_name, embedded


def _advance(font: M7Font | EmbeddedFont, ch: str, code: int) -> float:
    """Advance of ``code`` in 1/1000 em."""
    if isinstance(font, M7Font):
        return float(font.widths.get(code, 1000.0))
    width = font.widths.get(code)
    if width is None:
        width = font.default_width if font.kind is FontKind.TYPE0 else 0.0
    return float(width)


def _codes(font: M7Font | EmbeddedFont, text: str, plan: FontPlan) -> list[int]:
    if isinstance(font, M7Font):
        return [font.gid_for(ch) or 0 for ch in text]
    return list(plan.codes) if plan.codes else list(font.codes(text))


def _natural_width(
    font: M7Font | EmbeddedFont, text: str, codes: Sequence[int], size: float
) -> float:
    total = sum(_advance(font, ch, code) for ch, code in zip(text, codes, strict=False))
    return total * size / 1000.0


def _run_width(target: _Target, embedded: EmbeddedFont | None) -> float:
    """Extent (points, along the line) of the run: from the first char's origin to the end
    of the last char's advance (its width in the document's font when known, else the far
    edge of its box)."""
    d = _unit(target.line.dir)
    chars = target.chars
    start = _project(chars[0].origin, d)
    last = chars[-1]
    end = max(_project(p, d) for p in _corners(last.bbox))
    if embedded is not None:
        code = embedded.code_for(last.c)
        if code is not None:
            width = embedded.widths.get(code)
            if width is None and embedded.kind is FontKind.TYPE0:
                width = embedded.default_width
            if width:
                end = max(end, _project(last.origin, d) + width * target.span.size / 1000.0)
    return max(0.0, end - start)


def _num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _ops(
    page: pymupdf.Page,
    target: _Target,
    resource: str,
    hexstr: bytes,
    scaling: float,
) -> bytes:
    """``q BT … Tj ET Q`` writing ``hexstr`` with the span's size, colour, direction and
    first char origin (content space)."""
    span = target.span
    to_pdf = page_to_pdf_matrix(page)
    o = pymupdf.Point(target.chars[0].origin.x(), target.chars[0].origin.y()) * to_pdf
    dx, dy = _unit(target.line.dir)
    ux, uy = _unit((dx * to_pdf.a + dy * to_pdf.c, dx * to_pdf.b + dy * to_pdf.d))
    r = ((span.color >> 16) & 0xFF) / 255.0
    g = ((span.color >> 8) & 0xFF) / 255.0
    b = (span.color & 0xFF) / 255.0
    parts = [
        "q BT",
        f"/{resource} {_num(span.size)} Tf",
        f"{_num(r)} {_num(g)} {_num(b)} rg",
    ]
    if scaling < 100.0:
        parts.append(f"{_num(scaling)} Tz")
    parts.append(f"{_num(ux)} {_num(uy)} {_num(-uy)} {_num(ux)} {_num(o.x)} {_num(o.y)} Tm")
    return " ".join(parts).encode("ascii") + b" " + hexstr + b" Tj ET Q"


def _balanced(page: pymupdf.Page, content: bytes) -> bytes:
    """``content`` wrapped in the ``q``/``Q`` it lacks (so the appended ops start from a
    clean graphics state)."""
    try:
        push, pop = page._count_q_balance()  # what Page.wrap_contents uses; no public form
    except Exception:  # noqa: BLE001
        push = pop = 0
    return b"q\n" * max(0, int(push)) + content + b"\nQ" * max(0, int(pop))


# -- the edit -----------------------------------------------------------------------------
def _diff(
    before: PageText, after: PageText, expected_gone: set[int]
) -> tuple[list[int], list[int]]:
    """``(vanished, survived)``: indexes of ``before`` chars missing from ``after`` though
    not expected to go, and of chars expected to go that are still there."""
    remaining = Counter(_char_key(ch) for ch in after.chars)
    vanished: list[int] = []
    survived: list[int] = []
    for i, ch in enumerate(before.chars):
        key = _char_key(ch)
        present = remaining.get(key, 0) > 0
        if present:
            remaining[key] -= 1
        if i in expected_gone:
            if present:
                survived.append(i)
        elif not present:
            vanished.append(i)
    return vanished, survived


def _extend(text: PageText, target: _Target, vanished: Sequence[int]) -> Run | None:
    """The run grown to include ``vanished`` when they lie in the same span (else None)."""
    if any(text.span_of(i) is not target.span for i in vanished):
        return None
    first = min(target.indexes[0], min(vanished))
    last = max(target.indexes[-1], max(vanished))
    if any(text.span_of(i) is not target.span for i in range(first, last + 1)):
        return None
    return Run(first, last)


def replace_run(
    doc: pymupdf.Document,
    page_index: int,
    page_text: PageText,
    run: Run,
    new_text: str,
    *,
    fonts: SystemFonts | None = None,
) -> TextEditResult:
    """Replace ``run`` of page ``page_index`` (whose current text is ``page_text``) by
    ``new_text`` ("" removes it). Caller holds the lock. Raises :class:`TextEditError`
    (the page is left as it was) or ``ValueError`` for a multi-line ``new_text``."""
    if "\n" in new_text or "\r" in new_text:
        raise ValueError("the new text must be a single line")
    target = _resolve(page_text, run)
    _check_editable(page_text, target, new_text)
    copies, twins = _twins(page_text, target.indexes)
    page = doc[page_index]
    page_xref = page.xref
    before = page.read_contents()
    if len(before) > MAX_CONTENT_BYTES:
        raise TextEditError("the page content is too large", EditReason.TOO_COMPLEX)
    resources = _resources_value(doc, page_xref)
    annots = _key(doc, page_xref, "Annots")
    try:
        return _edit(
            doc,
            page,
            page_text,
            target,
            run,
            new_text,
            copies,
            twins,
            before,
            resources,
            annots,
            fonts,
        )
    except TextEditError:
        _restore(doc, page_index, before, resources)
        raise
    except Exception as exc:  # MuPDF raises FzError* (not RuntimeError), KeyError...
        log.warning("text edit failed on page %d", page_index, exc_info=True)
        _restore(doc, page_index, before, resources)
        raise TextEditError(str(exc)) from exc


def _restore(doc: pymupdf.Document, page_index: int, before: bytes, resources: str) -> None:
    """Best effort after a failure: the original content and ``/Resources`` value (the
    old content needs only the original names)."""
    try:
        page = doc[page_index]
        if page.read_contents() != before:
            page = set_page_content(doc, page, before)
        doc.xref_set_key(page.xref, "Resources", resources)
    except Exception:  # noqa: BLE001 - best effort after a failure
        log.warning("could not restore the content of page %d", page_index, exc_info=True)


def _edit(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    text: PageText,
    target: _Target,
    run: Run,
    new_text: str,
    copies: int,
    twins: list[int],
    before: bytes,
    resources: str,
    annots: tuple[str, str],
    fonts: SystemFonts | None,
) -> TextEditResult:
    page_index = page.number
    current = target
    expected_gone = set(target.indexes) | set(twins)
    rounds = 0
    while True:
        rect = _union(ch.bbox for ch in current.chars)
        page = _remove(doc, page, rect, resources, annots)
        after_text = pagetext.extract_page_text(page)
        vanished, survived = _diff(text, after_text, expected_gone)
        if survived:
            raise TextEditError("some glyphs of the run could not be removed")
        if not vanished:
            break
        rounds += 1
        grown = _extend(text, current, vanished)
        if grown is None or rounds > MAX_COLLATERAL_ROUNDS:
            raise TextEditError("neighbouring text could not be preserved")
        log.info("page %d: edit grows to chars %d..%d", page_index, grown.first, grown.last)
        page = set_page_content(doc, page, before)
        current = _resolve(text, grown)
        copies, twins = _twins(text, current.indexes)
        expected_gone = set(current.indexes) | set(twins)
    extended = Run(current.indexes[0], current.indexes[-1]) if current is not target else None
    old_text = "".join(ch.c for ch in current.chars)
    prefix = "".join(text.chars[i].c for i in current.indexes if i < run.first)
    suffix = "".join(text.chars[i].c for i in current.indexes if i > run.last)
    write_text = prefix + new_text + suffix

    content = _balanced(page, page.read_contents())
    plan: FontPlan | None = None
    scaling, overflow, natural, width = 100.0, False, 0.0, 0.0
    if write_text:
        plan, font, resource, own = _choose_font(doc, page.xref, current.span, write_text, fonts)
        try:
            hexstr = fontembed.encode(font, write_text, plan.codes or None)
        except KeyError as exc:
            raise TextEditError(f"no glyph for {exc.args[0]!r}", EditReason.NO_FONT) from exc
        codes = _codes(font, write_text, plan)
        natural = _natural_width(font, write_text, codes, current.span.size)
        width = _run_width(current, own if own.xref else None)
        scaling, overflow = fit_scaling(natural, width)
        ops = _ops(page, current, resource, hexstr, scaling)
        content = content + b"\n" + b"\n".join([ops] * copies)
    page = set_page_content(doc, page, content)
    after = page.read_contents()
    return TextEditResult(
        page=page_index,
        before=before,
        after=after,
        run=run,
        old_text=old_text,
        new_text=write_text,
        requested_text=new_text,
        extended_run=extended,
        plan=plan,
        scaling=scaling,
        overflow=overflow,
        natural_width=natural,
        target_width=width,
        copies=copies,
    )
