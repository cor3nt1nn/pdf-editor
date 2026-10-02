"""M6b review findings (core): right-to-left range quads, foreign quad order, fake bold."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QRectF

from pdfeditor.core import pagetext

ARIAL = Path(r"C:\Windows\Fonts\arial.ttf")
HEBREW = "שלום עולם"  # logical order; drawn reversed (visual order), as producers do
needs_arial = pytest.mark.skipif(not ARIAL.is_file(), reason="Arial is not installed")


def _text_pdf(path: Path, text: str) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="FA", fontfile=str(ARIAL))
    page.insert_text((72, 100), text, fontname="FA", fontsize=14)
    doc.save(path)
    doc.close()
    return path


def _page_text(path: Path) -> pagetext.PageText:
    with pymupdf.open(path) as fd:
        return pagetext.extract_page_text(fd[0])


def _union(chars) -> QRectF:
    r = QRectF()
    for c in chars:
        r = c.bbox if r.isNull() else r.united(c.bbox)
    return r


@needs_arial
def test_rtl_line_quad_covers_the_glyphs(tmp_path):
    pt = _page_text(_text_pdf(tmp_path / "rtl.pdf", HEBREW[::-1]))
    n = len(pt.chars)
    assert pt.text.replace(chr(0xA0), " ") == HEBREW  # MuPDF reports logical order...
    assert pt.chars[0].bbox.left() > pt.chars[1].bbox.left()  # ...with boxes right to left
    (quad,) = pt.range_quads(0, n - 1)
    box, glyphs = quad.bounding_rect(), _union(pt.chars)
    assert box.left() == pytest.approx(glyphs.left(), abs=0.01)
    assert box.right() == pytest.approx(glyphs.right(), abs=0.01)
    assert len(pt.chars_in_rect(quad)) == n
    (two,) = pt.range_quads(1, 2)
    pair = _union(pt.chars[1:3])
    assert two.bounding_rect().width() == pytest.approx(pair.width(), abs=0.01)
    assert two.bounding_rect().width() > 1.0
    assert [r.index for r in pt.chars_in_rect(two)] == [1, 2]


@needs_arial
def test_mixed_latin_hebrew_line(tmp_path):
    pt = _page_text(_text_pdf(tmp_path / "mixed.pdf", "abc " + "שלום"[::-1] + " def"))
    n = len(pt.chars)
    (quad,) = pt.range_quads(0, n - 1)
    assert quad.bounding_rect().width() == pytest.approx(_union(pt.chars).width(), abs=0.01)
    assert len(pt.chars_in_rect(quad)) == n
    i = pt.text.index("ש")
    (word,) = pt.range_quads(i, i + 3)
    assert word.bounding_rect().width() == pytest.approx(
        _union(pt.chars[i : i + 4]).width(), abs=0.01
    )
    assert {r.index for r in pt.chars_in_rect(word)} == set(range(i, i + 4))


def test_ltr_quads_unchanged_by_the_union(tmp_path):
    """Left-to-right lines keep first-left / last-right extents."""
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Hello world", fontsize=12)
    doc.save(tmp_path / "ltr.pdf")
    doc.close()
    pt = _page_text(tmp_path / "ltr.pdf")
    (quad,) = pt.range_quads(0, 4)
    assert quad.ul.x() == pytest.approx(pt.chars[0].bbox.left())
    assert quad.ur.x() == pytest.approx(pt.chars[4].bbox.right())
    assert quad.ul.y() == pytest.approx(min(c.bbox.top() for c in pt.chars[:5]))


@needs_arial
def test_rtl_markup_text_and_copy(tmp_path):
    from pdfeditor.core.annotations import AnnotKind, markup_spec
    from pdfeditor.core.document import PdfDocument
    from pdfeditor.ui.tools.markup_tools import markup_text

    doc = PdfDocument.open(str(_text_pdf(tmp_path / "rtl.pdf", HEBREW[::-1])))
    try:
        pt = doc.page_text(0)
        last = len(pt.chars) - 1
        info = doc.add_annot(
            markup_spec(0, AnnotKind.HIGHLIGHT, pt.range_quads(0, last), (1, 1, 0))
        )
        assert markup_text(doc, info) == pt.text_of(pt.chars_between(0, last)) == pt.text
        word = doc.add_annot(markup_spec(0, AnnotKind.UNDERLINE, pt.range_quads(1, 2), (1, 0, 0)))
        assert markup_text(doc, word) == pt.text[1:3]
    finally:
        doc.close()


# -- 11: foreign /QuadPoints in the specification's point order ----------------------------
def _foreign_highlight(path: Path, quadpoints: str, rotation: int = 0) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "The quick brown fox", fontsize=12, fontname="helv")
    page.set_rotation(rotation)
    ref = doc.page_xref(0)
    hl = doc.get_new_xref()
    doc.update_object(
        hl,
        f"<</Type/Annot/Subtype/Highlight/Rect[70 686 104 708]/QuadPoints[{quadpoints}]"
        f"/C[1 1 0]/F 4/P {ref} 0 R>>",
    )
    doc.xref_set_key(ref, "Annots", f"[{hl} 0 R]")
    doc.save(path)
    doc.close()
    return path


# "The" at x 72..~94, baseline y 692 (PDF space): ll, lr, ur, ul vs ul, ur, ll, lr.
SPEC_ORDER = "71 688 93 688 93 706 71 706"
ACROBAT_ORDER = "71 706 93 706 71 688 93 688"


@pytest.mark.parametrize("rotation", [0, 90])
@pytest.mark.parametrize("order", [SPEC_ORDER, ACROBAT_ORDER])
def test_foreign_quad_point_order_is_normalised(tmp_path, order, rotation):
    from pdfeditor.core.document import PdfDocument
    from pdfeditor.ui.tools.markup_tools import markup_text

    doc = PdfDocument.open(str(_foreign_highlight(tmp_path / "f.pdf", order, rotation)))
    try:
        (info,) = [a for a in doc.annots(0) if a.is_markup]
        (quad,) = info.quads
        poly = quad.polygon()
        # A simple (not self-intersecting) outline: its area equals the bounding rect's.
        area = 0.0
        for k in range(4):
            a, b = poly[k], poly[(k + 1) % 4]
            area += a.x() * b.y() - b.x() * a.y()
        box = quad.bounding_rect()
        assert abs(area) / 2 == pytest.approx(box.width() * box.height(), rel=1e-6)
        assert markup_text(doc, info) == "The"
    finally:
        doc.close()
