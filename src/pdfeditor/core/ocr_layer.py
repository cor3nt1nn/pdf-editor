"""The searchable OCR text layer of a page: invisible text over the scan (M8).

:func:`add_layer` appends the recognised words to the page as text in render mode 3
(invisible: selectable, searchable and copyable in every viewer, no pixel changes; plan
verdict 5 and O12–O14). The page gets two new content streams around its own:

* a *prefix* ``% PDFEditor OCR prefix v1`` + ``q`` before the original content, and
* the *layer* ``% PDFEditor OCR layer v1`` + ``Q`` (back to the page's initial graphics
  state) + one ``BT … ET`` block: per word ``/PdfEdOcr <size> Tf <Tz> Tz <Tm> (text ) Tj``
  with Helvetica (WinAnsi; characters outside cp1252 become ``?``), the size 0.85 × the
  word's height, the baseline 0.22 × the height above its bottom, and the horizontal
  scaling fitting the word's width. A trailing space per word keeps the words apart in
  extractors that do not split on gaps (pypdf).

The font is a direct dictionary ``<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica
/Encoding /WinAnsiEncoding >>`` named ``/PdfEdOcr`` in the page's ``/Resources /Font``
(an inherited dictionary is copied to the page first, as M7 does; a shared indirect one is
extended). :func:`remove_layer` (undo) drops the two streams by their first line — so it
survives the object renumbering of a full save — and leaves the unused font entry (a
shared resource dictionary may serve other pages' layers). Objects of a removed layer
created since the load are freed by ``core/orphans.py`` before an incremental save.

Callers hold ``PdfDocument.lock``. Text matrices come from the inverse of MuPDF's
``pdf_page_transform`` (page space → content space; exact for every /Rotate with or
without a cropbox, O13) and the line direction, so a layer is placed right also after the
page was turned (the :class:`~pdfeditor.core.ocr.PageOcr` must be in the page's current
rotation, see ``PageOcr.rotated``).
"""

from __future__ import annotations

import io
import logging
from functools import cache

import pymupdf

from pdfeditor.core.ocr import OcrError, OcrLine, PageOcr
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


def encode(text: str) -> bytes:
    """``text`` as a PDF literal string in WinAnsi (cp1252; others become ``?``)."""
    raw = text.encode("cp1252", "replace")
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


def _line_ops(line: OcrLine, inv: pymupdf.Matrix) -> list[bytes]:
    """``Tf Tz Tm Tj`` of each word of ``line``."""
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
        size = SIZE_RATIO * height
        shown = word.text.encode("cp1252", "replace").decode("cp1252")
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


def layer_stream(page: pymupdf.Page, ocr: PageOcr) -> bytes:
    """The layer's content stream for ``ocr`` (page space of ``page`` as it is now)."""
    inv = page_to_content_matrix(page)
    out = io.BytesIO()
    out.write(LAYER_MARKER + b"\nQ\nq\nBT\n3 Tr\n")
    for line in ocr.lines:
        for op in _line_ops(line, inv):
            out.write(op)
    out.write(b"ET\nQ\n")
    return out.getvalue()


def _first_line(doc: pymupdf.Document, xref: int) -> bytes:
    try:
        data = doc.xref_stream(xref) or b""
    except Exception:  # MuPDF raises FzError* on broken streams
        return b""
    return data[:64].split(b"\n", 1)[0].strip()


def layer_streams(doc: pymupdf.Document, page: pymupdf.Page) -> list[int]:
    """Xrefs of the page's content streams that belong to an OCR layer (prefix and
    layer streams)."""
    return [x for x in page.get_contents() if _first_line(doc, x) in (LAYER_MARKER, PREFIX_MARKER)]


def has_layer(doc: pymupdf.Document, page: pymupdf.Page) -> bool:
    """The page has an OCR layer written by :func:`add_layer`."""
    return any(_first_line(doc, x) == LAYER_MARKER for x in page.get_contents())


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
    of words written. Raises :class:`OcrError` (``"exists"``) when the page already has
    one, ``ValueError`` when ``ocr`` was made for another rotation. The caller reloads
    the page (or re-fetches it) before reading it again."""
    rotation = int(page.rotation) % 360
    if ocr.rotation != rotation:
        raise ValueError(f"OCR result for rotation {ocr.rotation}, page has {rotation}")
    if has_layer(doc, page):
        raise OcrError("the page already has an OCR text layer", "exists")
    data = layer_stream(page, ocr)
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
    none. The ``/PdfEdOcr`` font entry stays (unused, a few bytes)."""
    ours = set(layer_streams(doc, page))
    if not ours:
        return False
    keep = [x for x in page.get_contents() if x not in ours]
    _set_contents(doc, page.xref, keep)
    return True
