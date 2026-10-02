"""Test PDFs and helpers for editing page text (M7, docs/M7_PLAN.md §3).

Generated on the fly like ``tests/fixtures.py`` (no binary fixtures). Most use the
Windows fonts Calibri, Arial and Times New Roman: guard such tests with
``pytest.mark.skipif(not TEXT_FONTS_AVAILABLE, reason=...)`` (or :data:`needs_text_fonts`).

* :func:`make_text_edit_pdf` — "Word-like": shapes first, text on top; Calibri 11 lines
  (:data:`LINE1`, :data:`LINE2`), a blue Times 14 title (the only use of its font), a grey
  9 pt footer, a thin underline rect, a grey band behind line 2, a small image. Fonts are
  Type0/Identity-H subsets (``insert_text(fontfile=…)`` + ``subset_fonts()``).
* :func:`make_print_like_pdf` — "Print to PDF"-like: TextWriter Arial whose names are
  renamed ``CIDFont+F1``; ``strip_name_table=True`` also hides the program's name table.
* :func:`make_simple_font_pdf` — simple TrueType Calibri (WinAnsi, ``\\xe0`` = "à").
* :func:`make_xobject_text_pdf`, :func:`make_ocr_pdf`, :func:`make_type3_pdf`,
  :func:`make_kerned_tj_pdf`, :func:`make_inherited_resources_pdf`.
* :func:`chars_of`, :func:`pixels_equal`, :func:`pixel_diff_bbox`, :func:`font_xref`.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import pymupdf
import pytest

FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
CALIBRI_PATH = FONT_DIR / "calibri.ttf"
CALIBRI_BOLD_PATH = FONT_DIR / "calibrib.ttf"
CALIBRI_ITALIC_PATH = FONT_DIR / "calibrii.ttf"
ARIAL_PATH = FONT_DIR / "arial.ttf"
TIMES_PATH = FONT_DIR / "times.ttf"
CAMBRIA_TTC_PATH = FONT_DIR / "cambria.ttc"
#: Calibri, Arial and Times New Roman are installed (every fixture below needs them).
TEXT_FONTS_AVAILABLE = all(p.exists() for p in (CALIBRI_PATH, ARIAL_PATH, TIMES_PATH))
needs_text_fonts = pytest.mark.skipif(
    not TEXT_FONTS_AVAILABLE, reason="Windows fonts Calibri/Arial/Times New Roman missing"
)

PAGE_SIZE = (595.0, 842.0)
PASSWORD = "secret"
OWNER_PASSWORD = "secret-owner"

# -- make_text_edit_pdf -------------------------------------------------------------------
LINE1 = "Monsieur Jean Dupont est né le 12/03/1985 à Lyon."
LINE2 = "Deuxième ligne avec une faute: recevoir du courier."
TITLE = "Titre en Times"
FOOTER = "Page 1 sur 3 – plage de pointage"
#: Baseline origins (unrotated, uncropped page space).
LINE1_ORIGIN = (72.0, 100.0)
LINE2_ORIGIN = (72.0, 120.0)
TITLE_ORIGIN = (72.0, 150.0)
FOOTER_ORIGIN = (72.0, 180.0)
BODY_SIZE = 11.0
TITLE_SIZE = 14.0
FOOTER_SIZE = 9.0
TITLE_COLOR = (0.0, 0.0, 1.0)
FOOTER_COLOR = (0.3, 0.3, 0.3)
UNDERLINE_RECT = (72.0, 102.0, 320.0, 102.5)
BAND_RECT = (70.0, 108.0, 330.0, 124.0)
BAND_GREY = 0.9
IMAGE_RECT = (400.0, 90.0, 440.0, 130.0)
#: Cropbox of ``make_text_edit_pdf(..., cropbox=True)``.
TEXT_EDIT_CROPBOX = (30.0, 40.0, 560.0, 800.0)


def _red_pixmap() -> pymupdf.Pixmap:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    return pix


def make_text_edit_pdf(
    path: Path, *, rotate: int = 0, cropbox: bool = False, encrypted: bool = False
) -> Path:
    """The Word-like page (module docstring). ``encrypted`` saves with AES-256, user
    password :data:`PASSWORD`, owner :data:`OWNER_PASSWORD`, every permission."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(UNDERLINE_RECT))
    shape.finish(fill=(0, 0, 0), color=None, width=0)
    shape.draw_rect(pymupdf.Rect(BAND_RECT))
    shape.finish(fill=(BAND_GREY,) * 3, color=None, width=0)
    shape.commit()
    page.insert_image(pymupdf.Rect(IMAGE_RECT), pixmap=_red_pixmap())
    calibri = {"fontname": "Calibri", "fontfile": str(CALIBRI_PATH)}
    page.insert_text(LINE1_ORIGIN, LINE1, fontsize=BODY_SIZE, **calibri)
    page.insert_text(LINE2_ORIGIN, LINE2, fontsize=BODY_SIZE, **calibri)
    page.insert_text(
        TITLE_ORIGIN,
        TITLE,
        fontsize=TITLE_SIZE,
        fontname="Times",
        fontfile=str(TIMES_PATH),
        color=TITLE_COLOR,
    )
    page.insert_text(FOOTER_ORIGIN, FOOTER, fontsize=FOOTER_SIZE, color=FOOTER_COLOR, **calibri)
    if cropbox:
        page.set_cropbox(pymupdf.Rect(TEXT_EDIT_CROPBOX))
    if rotate:
        page.set_rotation(rotate)
    doc.subset_fonts()
    if encrypted:
        doc.save(
            path,
            garbage=3,
            deflate=True,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=PASSWORD,
            owner_pw=OWNER_PASSWORD,
            permissions=-1,
        )
    else:
        doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


# -- make_print_like_pdf ------------------------------------------------------------------
PRINT_LINES = ("Facture n° 2024-0042 du 15 janvier 2024", "Client : Société Exemple SARL")
PRINT_ORIGINS = ((72.0, 100.0), (72.0, 120.0))
PRINT_BASE_FONT = "CIDFont+F1"


def strip_name_table(doc: pymupdf.Document, program_xref: int) -> None:
    """Hide the ``name`` table of an embedded TrueType program (its directory tag
    becomes ``xame``; MuPDF does not need it to render)."""
    data = bytearray(doc.xref_stream(program_xref))
    num = int.from_bytes(data[4:6], "big")
    for i in range(num):
        rec = 12 + 16 * i
        if bytes(data[rec : rec + 4]) == b"name":
            data[rec : rec + 4] = b"xame"
    doc.update_stream(program_xref, bytes(data), compress=True)


def make_print_like_pdf(path: Path, *, strip_name: bool = False) -> Path:
    """Arial text written by a TextWriter (Type0), every name renamed
    :data:`PRINT_BASE_FONT` like Windows "Microsoft Print to PDF"."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
    font = pymupdf.Font(fontfile=str(ARIAL_PATH))
    writer = pymupdf.TextWriter(page.rect)
    for origin, text in zip(PRINT_ORIGINS, PRINT_LINES, strict=True):
        writer.append(origin, text, font=font, fontsize=BODY_SIZE)
    writer.write_text(page)
    for xref, *_ in page.get_fonts():
        doc.xref_set_key(xref, "BaseFont", f"/{PRINT_BASE_FONT}")
        kind, value = doc.xref_get_key(xref, "DescendantFonts")
        if kind == "array":
            desc = int(value.strip("[]").split()[0])
            doc.xref_set_key(desc, "BaseFont", f"/{PRINT_BASE_FONT}")
            fd = int(doc.xref_get_key(desc, "FontDescriptor")[1].split()[0])
            doc.xref_set_key(fd, "FontName", f"/{PRINT_BASE_FONT}")
    doc.subset_fonts()
    if strip_name:
        for xref, *_ in doc[0].get_fonts():
            desc = int(doc.xref_get_key(xref, "DescendantFonts")[1].strip("[]").split()[0])
            fd = int(doc.xref_get_key(desc, "FontDescriptor")[1].split()[0])
            strip_name_table(doc, int(doc.xref_get_key(fd, "FontFile2")[1].split()[0]))
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


# -- make_simple_font_pdf -----------------------------------------------------------------
SIMPLE_TEXT = "Monsieur Jean Dupont à Lyon"
SIMPLE_RESOURCE = "CalS"
SIMPLE_ORIGIN = (72.0, 100.0)  # page space (content: 72 742)


def make_simple_font_pdf(path: Path) -> Path:
    """Simple TrueType Calibri (WinAnsi, subset) showing :data:`SIMPLE_TEXT` at 11 pt."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
    page.insert_font(fontname=SIMPLE_RESOURCE, fontfile=str(CALIBRI_PATH), set_simple=True)
    x, y = SIMPLE_ORIGIN
    ops = (
        f"BT /{SIMPLE_RESOURCE} 11 Tf 1 0 0 1 {x:g} {PAGE_SIZE[1] - y:g} Tm (".encode()
        + SIMPLE_TEXT.encode("cp1252")
        + b") Tj ET"
    )
    _set_contents(doc, page, ops)
    doc.subset_fonts()
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


def _set_contents(doc: pymupdf.Document, page: pymupdf.Page, ops: bytes) -> None:
    xref = doc.get_new_xref()
    doc.update_object(xref, "<< >>")
    doc.update_stream(xref, ops)
    doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")


# -- other fixtures -----------------------------------------------------------------------
XOBJECT_TEXT = "Texte dans un formulaire XObject"
XOBJECT_RECT = (0.0, 0.0, 595.0, 842.0)


def make_xobject_text_pdf(path: Path) -> Path:
    """A page whose Calibri text lives in a Form XObject (``show_pdf_page``) plus one line
    of direct text (:data:`LINE1`)."""
    src = pymupdf.open()
    sp = src.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
    sp.insert_text(
        (72, 200), XOBJECT_TEXT, fontsize=BODY_SIZE, fontname="Calibri", fontfile=str(CALIBRI_PATH)
    )
    src.subset_fonts()
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
    page.insert_text(
        LINE1_ORIGIN, LINE1, fontsize=BODY_SIZE, fontname="Calibri", fontfile=str(CALIBRI_PATH)
    )
    doc.subset_fonts()  # before the XObject: subsetting both merged their glyphs wrongly
    page.show_pdf_page(pymupdf.Rect(XOBJECT_RECT), src, 0)
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    src.close()
    return path


OCR_TEXT = "Texte reconnu par OCR"
OCR_ORIGIN = (72.0, 100.0)


def make_ocr_pdf(path: Path) -> Path:
    """A "scanned" page: a full-page grey image under invisible (render mode 3) text."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 60, 85), False)
    pix.set_rect(pix.irect, (235,))
    page.insert_image(page.rect, pixmap=pix)
    page.insert_text(OCR_ORIGIN, OCR_TEXT, fontsize=BODY_SIZE, fontname="helv", render_mode=3)
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


TYPE3_TEXT = "abba"
TYPE3_RESOURCE = "T3"


def make_type3_pdf(path: Path) -> Path:
    """Text "abba" in a hand-made Type3 font (glyphs "a" = box, "b" = bar) next to
    :data:`LINE1` in Calibri."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
    page.insert_text(
        LINE1_ORIGIN, LINE1, fontsize=BODY_SIZE, fontname="Calibri", fontfile=str(CALIBRI_PATH)
    )
    procs = {
        "a": b"600 0 0 0 500 700 d1 50 0 450 700 re f",
        "b": b"600 0 0 0 500 700 d1 250 0 100 700 re f",
    }
    proc_refs = []
    for name, data in procs.items():
        x = doc.get_new_xref()
        doc.update_object(x, "<< >>")
        doc.update_stream(x, data)
        proc_refs.append(f"/{name} {x} 0 R")
    font = doc.get_new_xref()
    doc.update_object(
        font,
        "<< /Type /Font /Subtype /Type3 /FontBBox [0 0 600 700] "
        "/FontMatrix [0.001 0 0 0.001 0 0] "
        f"/CharProcs << {' '.join(proc_refs)} >> "
        "/Encoding << /Type /Encoding /Differences [97 /a /b] >> "
        "/FirstChar 97 /LastChar 98 /Widths [600 600] /Resources << >> >>",
    )
    kind, value = doc.xref_get_key(page.xref, "Resources")
    res = int(value.split()[0]) if kind == "xref" else page.xref
    prefix = "" if kind == "xref" else "Resources/"
    doc.xref_set_key(res, f"{prefix}Font/{TYPE3_RESOURCE}", f"{font} 0 R")
    extra = f"BT /{TYPE3_RESOURCE} 12 Tf 1 0 0 1 72 642 Tm ({TYPE3_TEXT}) Tj ET".encode()
    x = doc.get_new_xref()
    doc.update_object(x, "<< >>")
    doc.update_stream(x, extra)
    contents = " ".join(f"{c} 0 R" for c in page.get_contents())
    doc.xref_set_key(page.xref, "Contents", f"[{contents} {x} 0 R]")
    doc.subset_fonts()
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


KERNED_TEXT = "Facture totale due"
KERNED_RESOURCE = "CalK"


def make_kerned_tj_pdf(path: Path) -> Path:
    """Simple Calibri text shown by one ``TJ`` with kerning, ``Tc``, ``Tw`` and ``Tz``
    (reads as :data:`KERNED_TEXT`)."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
    page.insert_font(fontname=KERNED_RESOURCE, fontfile=str(CALIBRI_PATH), set_simple=True)
    ops = (
        f"BT /{KERNED_RESOURCE} 11 Tf 0.3 Tc 2 Tw 95 Tz 1 0 0 1 72 742 Tm "
        "[(F) 80 (ac) -20 (ture t) 40 (otale ) (d) 15 (ue)] TJ ET"
    ).encode()
    _set_contents(doc, page, ops)
    doc.subset_fonts()
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


INHERITED_TEXT = "Ressources héritées du nœud Pages"


def make_inherited_resources_pdf(path: Path) -> Path:
    """A Calibri page without its own ``/Resources``: they sit on the ``/Pages`` node."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
    page.insert_text(
        LINE1_ORIGIN,
        INHERITED_TEXT,
        fontsize=BODY_SIZE,
        fontname="Calibri",
        fontfile=str(CALIBRI_PATH),
    )
    doc.subset_fonts()
    _, resources = doc.xref_get_key(page.xref, "Resources")
    pages = int(doc.xref_get_key(doc.pdf_catalog(), "Pages")[1].split()[0])
    doc.xref_set_key(pages, "Resources", resources)
    mupdf = pymupdf.mupdf
    obj = mupdf.pdf_load_object(pymupdf._as_pdf_document(doc), page.xref)
    mupdf.pdf_dict_dels(obj, "Resources")
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


# -- helpers -----------------------------------------------------------------------------
Char = tuple[str, tuple[float, float], tuple[float, float, float, float], str, float]


def chars_of(page: pymupdf.Page, *, annots: bool = False) -> list[Char]:
    """Every char as ``(c, origin, bbox, span font, size)`` (rounded to 3 decimals),
    content order, annotations excluded unless ``annots``."""
    dl = page.get_displaylist(annots=annots)
    tp = pymupdf.TextPage(dl.get_textpage(pymupdf.TEXTFLAGS_RAWDICT))
    out: list[Char] = []
    for block in tp.extractRAWDICT()["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                for ch in span["chars"]:
                    out.append(
                        (
                            ch["c"],
                            (round(ch["origin"][0], 3), round(ch["origin"][1], 3)),
                            tuple(round(v, 3) for v in ch["bbox"]),  # type: ignore[misc]
                            span["font"],
                            round(span["size"], 3),
                        )
                    )
    return out


def font_xref(page: pymupdf.Page, base_font_part: str) -> tuple[int, str]:
    """``(xref, resource name)`` of the page font whose ``/BaseFont`` contains
    ``base_font_part``."""
    for entry in page.get_fonts(full=True):
        if base_font_part in entry[3]:
            return int(entry[0]), str(entry[4])
    raise LookupError(base_font_part)


def pixel_diff_bbox(
    a: pymupdf.Pixmap, b: pymupdf.Pixmap, *, threshold: int = 0
) -> tuple[int, int, int, int] | None:
    """Pixel bbox ``(x0, y0, x1, y1)`` (inclusive) of samples differing by more than
    ``threshold``; ``None`` when the pixmaps match. Sizes and channels must agree."""
    if (a.width, a.height, a.n) != (b.width, b.height, b.n):
        raise ValueError("pixmaps differ in size")
    sa, sb, n, w = a.samples, b.samples, a.n, a.width
    box: list[int] | None = None
    stride = w * n
    for y in range(a.height):
        ra, rb = sa[y * stride : (y + 1) * stride], sb[y * stride : (y + 1) * stride]
        if ra == rb:
            continue
        for x in range(w):
            pa, pb = ra[x * n : (x + 1) * n], rb[x * n : (x + 1) * n]
            if pa != pb and max(abs(p - q) for p, q in zip(pa, pb, strict=True)) > threshold:
                if box is None:
                    box = [x, y, x, y]
                else:
                    box = [min(box[0], x), min(box[1], y), max(box[2], x), max(box[3], y)]
    return None if box is None else (box[0], box[1], box[2], box[3])


def pixels_equal(
    a: pymupdf.Pixmap,
    b: pymupdf.Pixmap,
    *,
    threshold: int = 0,
    ignore: Sequence[tuple[int, int, int, int]] = (),
) -> bool:
    """Same pixels (within ``threshold`` per channel), outside the inclusive pixel rects
    ``ignore``."""
    if (a.width, a.height, a.n) != (b.width, b.height, b.n):
        return False
    if not ignore:
        return pixel_diff_bbox(a, b, threshold=threshold) is None
    sa, sb, n, w = a.samples, b.samples, a.n, a.width
    for y in range(a.height):
        for x in range(w):
            if any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in ignore):
                continue
            i = (y * w + x) * n
            if max(abs(p - q) for p, q in zip(sa[i : i + n], sb[i : i + n], strict=True)) > (
                threshold
            ):
                return False
    return True
