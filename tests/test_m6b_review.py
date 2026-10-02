"""M6b review findings (core): right-to-left range quads, foreign quad order, fake bold."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
import textedit_fixtures
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


# -- 8: fake bold (coincident duplicate text) once in selection, copy and markups ----------
@textedit_fixtures.needs_text_fonts
def test_fake_bold_is_deduplicated_for_selection_only(tmp_path):
    from pdfeditor.core.annotations import AnnotKind, markup_spec
    from pdfeditor.core.document import PdfDocument
    from pdfeditor.ui.tools.markup_tools import markup_text

    path = textedit_fixtures.make_fake_bold_pdf(tmp_path / "bold.pdf")
    doc = PdfDocument.open(str(path))
    try:
        pt = doc.page_text(0)
        line = textedit_fixtures.FAKE_BOLD_LINE
        n = len(line)
        # The model keeps both copies (M7 rewrites each one).
        assert [ln.text.replace(chr(0xA0), " ") for ln in pt.lines][:2] == [line, line]
        assert pt.twins_of(0) == [n]
        assert pt.twins_of(n) == [0]
        dups = pt.duplicates
        assert set(range(n, 2 * n)) <= dups
        assert not dups & set(range(n))
        word = textedit_fixtures.PART_BOLD_WORD
        assert len(dups) == 2 * n + len(word) - n  # the doubled word's second copy too
        last = len(pt.chars) - 1
        both = (line + "\n" + textedit_fixtures.PART_BOLD_LINE).replace(" ", chr(0xA0))
        assert pt.text_of(pt.chars_between(0, last), dedupe=True) == both
        assert len(pt.text_of(pt.chars_between(0, last))) > len(both)
        assert len(pt.range_quads(0, last, dedupe=True)) == 2
        assert len(pt.range_quads(0, last)) == 4
        # Only duplicates selected: nothing left with dedupe (callers fall back).
        assert pt.range_quads(n, 2 * n - 1, dedupe=True) == []
        # A markup over the whole page text reads each line once.
        info = doc.add_annot(
            markup_spec(0, AnnotKind.HIGHLIGHT, pt.range_quads(0, last, dedupe=True), (1, 1, 0))
        )
        assert markup_text(doc, info) == both
    finally:
        doc.close()


@textedit_fixtures.needs_text_fonts
def test_selection_of_fake_bold(qtbot, tmp_path):
    from pdfeditor.ui.document_view import DocumentView

    view = DocumentView()
    qtbot.addWidget(view)
    try:
        view.open(str(textedit_fixtures.make_fake_bold_pdf(tmp_path / "b.pdf")))
        sel = view.text_selection
        n = len(textedit_fixtures.FAKE_BOLD_LINE)
        sel.set(0, 0, 2 * n - 1)  # the line and its copy
        assert sel.text().replace(chr(0xA0), " ") == textedit_fixtures.FAKE_BOLD_LINE
        assert len(sel.quads()) == 1
        sel.set(0, n, 2 * n - 1)  # the copy alone (not reachable by the mouse)
        assert sel.text().replace(chr(0xA0), " ") == textedit_fixtures.FAKE_BOLD_LINE
        assert len(sel.quads()) == 1
    finally:
        view.shutdown()


# -- review test gap: a foreign markup with a /Popup ---------------------------------------
def _popup_highlight(path: Path) -> tuple[Path, int, int]:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "The quick brown fox jumps", fontsize=12, fontname="helv")
    ref = doc.page_xref(0)
    hl, pop = doc.get_new_xref(), doc.get_new_xref()
    doc.update_object(
        pop,
        f"<</Type/Annot/Subtype/Popup/Rect[400 600 580 700]/Open false/Parent {hl} 0 R"
        f"/P {ref} 0 R>>",
    )
    doc.update_object(
        hl,
        "<</Type/Annot/Subtype/Highlight/Rect[94 687 128 706]"
        "/QuadPoints[96 705 124 705 96 688 124 688]/C[1 0.5 0]/F 4/T(Alice)"
        f"/Contents(a note)/Popup {pop} 0 R/P {ref} 0 R/RC(<body>rich</body>)>>",
    )
    doc.xref_set_key(ref, "Annots", f"[{hl} 0 R {pop} 0 R]")
    doc.save(path)
    doc.close()
    return path, hl, pop


def test_foreign_markup_with_popup_recolour_delete_undo(tmp_path):
    from pdfeditor.core.commands import DeleteAnnotCommand, EditAnnotCommand
    from pdfeditor.core.document import PdfDocument
    from pdfeditor.ui.tools.markup_tools import markup_text

    path, hl, pop = _popup_highlight(tmp_path / "popup.pdf")
    doc = PdfDocument.open(str(path))
    try:
        (info,) = doc.annots(0)  # the popup is not listed
        assert info.is_markup and info.text == "a note"
        assert markup_text(doc, info) == "quick"
        # Recolour: the foreign keys stay, undo restores the colour.
        cmd = EditAnnotCommand(doc, info, color=(0, 0, 1))
        cmd.apply_now()
        assert cmd.error is None
        with doc.lock:
            keys = {k: doc.fitz.xref_get_key(hl, k) for k in ("T", "Popup", "RC", "C")}
        assert keys["T"] == ("string", "Alice")
        assert keys["Popup"] == ("xref", f"{pop} 0 R")
        assert keys["RC"] == ("string", "<body>rich</body>")
        assert keys["C"] == ("array", "[0 0 1]")
        cmd.undo()
        with doc.lock:
            assert doc.fitz.xref_get_key(hl, "C") == ("array", "[1 .5 0]")
        # Delete: the markup and its popup go; undo re-creates the markup.
        (info,) = doc.annots(0)
        delete = DeleteAnnotCommand(doc, info)
        delete.apply_now()
        assert delete.error is None
        assert doc.annots(0) == []
        with doc.lock:
            assert list(doc.fitz[0].annots()) == []
            assert doc.fitz.xref_get_key(doc.fitz.page_xref(0), "Annots") == ("array", "[]")
        delete.undo()
        (back,) = doc.annots(0)
        assert back.kind is info.kind and back.text == "a note"
        assert back.color == pytest.approx(info.color)
        assert markup_text(doc, back) == "quick"
        doc.save()
    finally:
        doc.close()
    with pymupdf.open(path) as fd:
        assert [a.type[1] for a in fd[0].annots()] == ["Highlight"]
        annots = fd.xref_get_key(fd.page_xref(0), "Annots")[1]
        assert annots.count(" 0 R") == 1  # no orphan popup left in /Annots
