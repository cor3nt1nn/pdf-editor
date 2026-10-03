"""The searchable OCR text layer of a page: invisible text over the scan (M8).

:func:`add_layer` appends the recognised words to the page as text in render mode 3
(invisible: selectable, searchable and copyable in every viewer, no pixel changes; plan
verdict 5 and O12–O14). The page gets two new content streams around its own:

* a *prefix* ``% PDFEditor OCR prefix v1`` + ``q`` before the original content, and
* the *layer* ``% PDFEditor OCR layer v1`` + ``Q`` (back to the page's initial graphics
  state) + one ``BT … ET`` block: per word ``/PdfEdOcr <size> Tf <Tz> Tz <Tm> (text ) Tj``
  with Helvetica (WinAnsi; characters outside cp1252 become their NFKC compatibility
  form when it fits — ligatures are split — else ``?``), the size 0.85 × the
  word's height, the baseline 0.22 × the height above its bottom, and the horizontal
  scaling fitting the word's width. A trailing space per word keeps the words apart in
  extractors that do not split on gaps (pypdf).

The font is a direct dictionary ``<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica
/Encoding /WinAnsiEncoding >>`` named ``/PdfEdOcr`` in the page's ``/Resources /Font``
(an inherited dictionary is copied to the page first, as M7 does; a shared indirect one is
extended). :func:`remove_layer` (undo) drops the two streams found by their exact bytes
(:func:`is_prefix_stream`, :func:`is_layer_stream`: never by object number, so it survives
the renumbering of a full save) and leaves the unused font entry (a shared resource
dictionary may serve other pages' layers). When another program merged the streams
(qpdf ``--coalesce-contents``, pikepdf), a marker sits inside a stream that is not ours:
:func:`has_layer` still reports the layer (it is never written twice) but
:func:`remove_layer` refuses, as it does when removal would leave the page without content.
Objects of a removed layer created since the load are freed by ``core/orphans.py`` before
an incremental save.

Callers hold ``PdfDocument.lock``. Text matrices come from the inverse of MuPDF's
``pdf_page_transform`` (page space → content space; exact for every /Rotate with or
without a cropbox, O13) and the line direction, so a layer is placed right also after the
page was turned (the :class:`~pdfeditor.core.ocr.PageOcr` must be in the page's current
rotation, see ``PageOcr.rotated``).
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from collections.abc import Sequence
from functools import cache

import pymupdf

from pdfeditor.core.ocr import OcrError, OcrLine, PageOcr, Rect, text_profile
from pdfeditor.core.pdfdict import get_nested, inherited, inherited_owner, set_nested

log = logging.getLogger(__name__)

LAYER_MARKER = b"% PDFEditor OCR layer v1"
PREFIX_MARKER = b"% PDFEditor OCR prefix v1"
#: Resource name of the layer's font in ``/Resources /Font``.
FONT_NAME = "PdfEdOcr"
FONT_SOURCE = "<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>"
#: Font size and baseline height as ratios of a word box's height (O13).
SIZE_RATIO = 0.85
BASELINE_RATIO = 0.22
#: Words lower than this (points) are left out (narrow ones, e.g. a ":", are kept).
MIN_WORD_HEIGHT = 0.5
#: Horizontal scaling limits (percent): a word box much wider or narrower than its text
#: in Helvetica is still squeezed into it, within reason.
MIN_TZ, MAX_TZ = 5.0, 1000.0


@cache
def _helvetica() -> pymupdf.Font:
    return pymupdf.Font("helv")


def page_to_content_matrix(page: pymupdf.Page) -> pymupdf.Matrix:
    """Page space (rotated, cropbox-relative) → content space: the inverse of MuPDF's
    ``pdf_page_transform`` (as ``textedit.page_to_pdf_matrix``)."""
    mupdf = pymupdf.mupdf
    mediabox, ctm = mupdf.FzRect(), mupdf.FzMatrix()
    mupdf.pdf_page_transform(mupdf.pdf_page_from_fz_page(page.this), mediabox, ctm)
    return ~pymupdf.Matrix(ctm.a, ctm.b, ctm.c, ctm.d, ctm.e, ctm.f)


def winansi(text: str) -> str:
    """``text`` in the characters WinAnsi (cp1252) has: a character it lacks is replaced
    by its NFKC compatibility form when that one fits (ligatures "ﬁ" → "fi", full-width
    letters, "…" stays), else by ``?``."""
    out = []
    for c in text:
        try:
            c.encode("cp1252")
        except UnicodeEncodeError:
            alt = unicodedata.normalize("NFKC", c)
            try:
                alt.encode("cp1252")
            except UnicodeEncodeError:
                alt = "?"
            out.append(alt)
        else:
            out.append(c)
    return "".join(out)


def encode(text: str) -> bytes:
    """``text`` as a PDF literal string in WinAnsi (:func:`winansi`, then cp1252)."""
    raw = winansi(text).encode("cp1252")
    out = bytearray(b"(")
    for b in raw:
        if b in b"()\\":
            out += b"\\" + bytes([b])
        elif b < 32 or b == 127:
            out += b"\\%03o" % b
        else:
            out.append(b)
    out += b")"
    return bytes(out)


def _unit(d: tuple[float, float]) -> tuple[float, float]:
    n = (d[0] * d[0] + d[1] * d[1]) ** 0.5 or 1.0
    return d[0] / n, d[1] / n


#: An OCR word is left out of the layer when existing painted text covers this fraction
#: of its box (a scanner stamp that is real text already, M8 review m6).
OVERLAP_RATIO = 0.3


def _covered(rect: tuple[float, float, float, float], boxes: Sequence[Rect]) -> bool:
    x0, y0, x1, y1 = rect
    area = (x1 - x0) * (y1 - y0)
    if area <= 0:
        return False
    hit = 0.0
    for bx0, by0, bx1, by1 in boxes:
        w = min(x1, bx1) - max(x0, bx0)
        h = min(y1, by1) - max(y0, by0)
        if w > 0 and h > 0:
            hit += w * h
    return hit >= OVERLAP_RATIO * area


def _line_ops(line: OcrLine, inv: pymupdf.Matrix, skip: Sequence[Rect] = ()) -> list[bytes]:
    """``Tf Tz Tm Tj`` of each word of ``line`` (words over the ``skip`` boxes, the page's
    own painted text, left out)."""
    dx, dy = _unit(line.dir)
    nx, ny = -dy, dx  # across the line, towards the bottom of the glyphs
    lin = pymupdf.Matrix(inv.a, inv.b, inv.c, inv.d, 0, 0)
    ex = pymupdf.Point(dx, dy) * lin
    ey = pymupdf.Point(-nx, -ny) * lin
    font = _helvetica()
    ops = []
    for word in line.words:
        x0, y0, x1, y1 = word.rect
        corners = ((x0, y0), (x1, y0), (x0, y1), (x1, y1))
        along = [x * dx + y * dy for x, y in corners]
        across = [x * nx + y * ny for x, y in corners]
        a0, a1 = min(along), max(along)
        c0, c1 = min(across), max(across)
        width, height = a1 - a0, c1 - c0
        if width <= 0 or height < MIN_WORD_HEIGHT or not word.text.strip():
            continue
        if skip and _covered(word.rect, skip):
            continue
        size = SIZE_RATIO * height
        shown = winansi(word.text)
        natural = font.text_length(shown, fontsize=size)
        tz = min(max(100.0 * width / natural, MIN_TZ), MAX_TZ) if natural > 0 else 100.0
        base = c1 - BASELINE_RATIO * height
        p = pymupdf.Point(a0 * dx + base * nx, a0 * dy + base * ny) * inv
        ops.append(
            b"/%s %.2f Tf %.2f Tz %.5f %.5f %.5f %.5f %.2f %.2f Tm %s Tj\n"
            % (
                FONT_NAME.encode(),
                size,
                tz,
                ex.x,
                ex.y,
                ey.x,
                ey.y,
                p.x,
                p.y,
                encode(word.text + " "),
            )
        )
    return ops


def layer_stream(page: pymupdf.Page, ocr: PageOcr, skip: Sequence[Rect] = ()) -> bytes:
    """The layer's content stream for ``ocr`` (page space of ``page`` as it is now);
    words over the ``skip`` boxes (page space) are left out."""
    inv = page_to_content_matrix(page)
    out = io.BytesIO()
    out.write(LAYER_MARKER + b"\nQ\nq\nBT\n3 Tr\n")
    for line in ocr.lines:
        for op in _line_ops(line, inv, skip):
            out.write(op)
    out.write(b"ET\nQ\n")
    return out.getvalue()


#: First bytes of a layer stream and its last ones (:func:`layer_stream`).
LAYER_HEAD = LAYER_MARKER + b"\nQ\nq\nBT\n3 Tr\n"
LAYER_TAIL = b"ET\nQ\n"
#: The whole prefix stream.
PREFIX_STREAM = PREFIX_MARKER + b"\nq\n"
_NUM = rb"-?\d+(?:\.\d+)?"
#: One word of a layer stream: ``/PdfEdOcr <size> Tf <Tz> Tz <a b c d e f> Tm (text) Tj``
#: — text showing only, nothing that paints. The string's bytes are those of
#: :func:`encode` (``( ) \`` escaped, control bytes as octal).
_WORD_OP = re.compile(
    rb"/%s (?:%s) Tf (?:%s) Tz (?:(?:%s) ){6}Tm "
    rb"\((?:[^\\()\x00-\x1f\x7f]|\\[()\\]|\\[0-7]{3})*\) Tj"
    % (FONT_NAME.encode(), _NUM, _NUM, _NUM)
)


def _stream(doc: pymupdf.Document, xref: int) -> bytes:
    try:
        return doc.xref_stream(xref) or b""
    except Exception:  # MuPDF raises FzError* on broken streams
        return b""


def is_prefix_stream(data: bytes) -> bool:
    """``data`` is exactly a prefix stream written by :func:`add_layer`."""
    return data == PREFIX_STREAM


def is_layer_stream(data: bytes) -> bool:
    """``data`` is exactly a layer stream written by :func:`add_layer`: its head, word
    operators only (no painting operator), its tail."""
    if not (data.startswith(LAYER_HEAD) and data.endswith(LAYER_TAIL)):
        return False
    body = data[len(LAYER_HEAD) : len(data) - len(LAYER_TAIL)]
    if body and not body.endswith(b"\n"):
        return False
    return all(_WORD_OP.fullmatch(line) for line in body.split(b"\n")[:-1])


def layer_streams(doc: pymupdf.Document, page: pymupdf.Page) -> list[int]:
    """Xrefs of the page's content streams that are exactly an OCR layer's prefix or
    layer stream (a stream that merely starts with a marker, e.g. after another program
    coalesced the page's streams into one, is not)."""
    out = []
    for xref in page.get_contents():
        data = _stream(doc, xref)
        if is_prefix_stream(data) or is_layer_stream(data):
            out.append(xref)
    return out


def has_layer(doc: pymupdf.Document, page: pymupdf.Page) -> bool:
    """The page's content holds an OCR layer written by :func:`add_layer` — either marker
    anywhere in any of its streams, so a layer whose streams another program merged with
    the page's own is still found (and never written twice)."""
    for xref in page.get_contents():
        data = _stream(doc, xref)
        if LAYER_MARKER in data or PREFIX_MARKER in data:
            return True
    return False


def _new_stream(doc: pymupdf.Document, data: bytes) -> int:
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, data, compress=True)
    return xref


def _ensure_font(doc: pymupdf.Document, page_xref: int) -> None:
    """Name the layer font ``/PdfEdOcr`` in the page's ``/Resources /Font`` (kept when
    already there: any earlier layer used the same definition)."""
    kind, _value = doc.xref_get_key(page_xref, "Resources")
    if kind == "null":
        # Inherited resources: give the page its own copy (same entries, superset after).
        inh_kind, inh_value = inherited(doc, page_xref, "Resources")
        doc.xref_set_key(page_xref, "Resources", inh_value if inh_kind != "null" else "<<>>")
    owner = inherited_owner(doc, page_xref, "Resources") or page_xref
    if get_nested(doc, owner, f"Resources/Font/{FONT_NAME}")[0] != "null":
        return
    set_nested(doc, page_xref, f"Resources/Font/{FONT_NAME}", FONT_SOURCE)


def _set_contents(doc: pymupdf.Document, page_xref: int, xrefs: list[int]) -> None:
    if not xrefs:
        mupdf = pymupdf.mupdf
        obj = mupdf.pdf_new_indirect(pymupdf._as_pdf_document(doc), page_xref, 0)
        mupdf.pdf_dict_dels(obj, "Contents")
    elif len(xrefs) == 1:
        doc.xref_set_key(page_xref, "Contents", f"{xrefs[0]} 0 R")
    else:
        doc.xref_set_key(page_xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in xrefs) + "]")


def add_layer(doc: pymupdf.Document, page: pymupdf.Page, ocr: PageOcr) -> int:
    """Append ``ocr``'s words to ``page`` as an invisible text layer; returns the number
    of words written (words over the page's own painted text are left out). Raises
    :class:`OcrError` (``"exists"``) when the page already has one, ``ValueError`` when
    ``ocr`` was made for another rotation. The caller reloads the page (or re-fetches
    it) before reading it again."""
    rotation = int(page.rotation) % 360
    if ocr.rotation != rotation:
        raise ValueError(f"OCR result for rotation {ocr.rotation}, page has {rotation}")
    if has_layer(doc, page):
        raise OcrError("the page already has an OCR text layer", "exists")
    # A scan with a little real text (a stamp): the stamp's words are not written again.
    data = layer_stream(page, ocr, text_profile(page).visible)
    words = data.count(b" Tj\n")
    contents = list(page.get_contents())
    prefix = _new_stream(doc, PREFIX_MARKER + b"\nq\n")
    layer = _new_stream(doc, data)
    _ensure_font(doc, page.xref)
    _set_contents(doc, page.xref, [prefix, *contents, layer])
    log.info("page %d: OCR layer of %d words (%d bytes)", page.number + 1, words, len(data))
    return words


def remove_layer(doc: pymupdf.Document, page: pymupdf.Page) -> bool:
    """Remove the page's OCR layer streams (undo of :func:`add_layer`); False when it has
    none, or when it cannot be removed safely: a marker sits inside a stream that is not
    exactly ours (another program merged the streams), or removing the streams would
    leave the page without content. The page is then unchanged. The ``/PdfEdOcr`` font
    entry stays (unused, a few bytes)."""
    contents = list(page.get_contents())
    ours: set[int] = set()
    for xref in contents:
        data = _stream(doc, xref)
        if is_prefix_stream(data) or is_layer_stream(data):
            ours.add(xref)
        elif LAYER_MARKER in data or PREFIX_MARKER in data:
            log.warning(
                "page %d: the OCR layer is merged into stream %d; not removed",
                page.number + 1,
                xref,
            )
            return False
    if not ours:
        return False
    keep = [x for x in contents if x not in ours]
    if not keep:
        log.warning("page %d: removing the OCR layer would empty the page", page.number + 1)
        return False
    _set_contents(doc, page.xref, keep)
    return True
