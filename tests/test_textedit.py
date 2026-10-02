"""M7-T4: replacing a run of page text (core/textedit.py, PdfDocument.replace_text_run,
set_page_content, can_modify)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from pdfcheck import strict_read
from textedit_fixtures import (
    ARIAL_PATH,
    CALIBRI_PATH,
    FAKE_BOLD_LINE,
    LINE1,
    LINE2,
    OWNER_PASSWORD,
    PART_BOLD_LINE,
    PART_BOLD_WORD,
    PASSWORD,
    PRINT_LINES,
    RTL_TEXT,
    TIMES_PATH,
    TITLE,
    chars_of,
    font_xref,
    make_fake_bold_pdf,
    make_inherited_resources_pdf,
    make_kerned_tj_pdf,
    make_ocr_pdf,
    make_overlap_pdf,
    make_print_like_pdf,
    make_rtl_pdf,
    make_simple_font_pdf,
    make_text_edit_pdf,
    make_type3_pdf,
    make_xobject_text_pdf,
    needs_text_fonts,
    pixel_diff_bbox,
)

from pdfeditor.core import fontembed, textedit
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.fontmatch import FontWarning, PlanKind, SystemFonts
from pdfeditor.core.pagetext import PageText
from pdfeditor.core.textedit import EditReason, Run, TextEditError, TextEditResult

pytestmark = needs_text_fonts

SCALE = pymupdf.Matrix(3, 3)
#: One edit (redaction, rescan, font plan, write) plus its undo on the Word-like page.
EDIT_BUDGET_S = 0.5


@pytest.fixture(scope="module")
def fonts() -> SystemFonts:
    return SystemFonts.from_paths([CALIBRI_PATH, ARIAL_PATH, TIMES_PATH])


# -- helpers -----------------------------------------------------------------------------
def _norm(s: str) -> str:
    return s.replace("\xa0", " ")


def find_run(text: PageText, word: str, *, occurrence: int = 0) -> Run:
    """The run of ``word`` (nth occurrence) in the page's chars (content order)."""
    flat = _norm("".join(ch.c for ch in text.chars))
    start = -1
    for _ in range(occurrence + 1):
        start = flat.index(word, start + 1)
    return Run(start, start + len(word) - 1)


def line_text(doc: PdfDocument, page: int, y: float) -> str:
    """Text of the chars whose baseline is ``y`` (page space), left to right."""
    chars = [ch for ch in doc.page_text(page).chars if abs(ch.origin.y() - y) < 0.05]
    return _norm("".join(ch.c for ch in sorted(chars, key=lambda c: c.origin.x())))


def render(doc: PdfDocument, page: int = 0) -> pymupdf.Pixmap:
    with doc.lock:
        return doc.fitz[page].get_pixmap(matrix=SCALE)


def scaled_box(text: PageText, run: Run, pad: int = 1) -> tuple[int, int, int, int]:
    r = text.chars[run.first].bbox
    for i in run.indexes:
        r = r.united(text.chars[i].bbox)
    return (
        int(r.left() * 3) - pad,
        int(r.top() * 3) - pad,
        int(r.right() * 3) + pad,
        int(r.bottom() * 3) + pad,
    )


def inside(box: tuple[int, int, int, int] | None, outer: tuple[int, int, int, int]) -> bool:
    if box is None:
        return True
    return outer[0] <= box[0] and outer[1] <= box[1] and box[2] <= outer[2] and box[3] <= outer[3]


def open_doc(path: Path, password: str | None = None) -> PdfDocument:
    return PdfDocument.open(path, password=password)


def page_fonts(doc: PdfDocument, page: int = 0) -> list[tuple[int, str, str]]:
    with doc.lock:
        return [(int(f[0]), str(f[3]), str(f[4])) for f in doc.fitz[page].get_fonts(full=True)]


def other_chars(cs: list, run_text: PageText, run: Run) -> list:
    """``chars_of`` entries that are not the run's chars."""
    keys = {
        (ch.c, round(ch.origin.x(), 3), round(ch.origin.y(), 3))
        for ch in (run_text.chars[i] for i in run.indexes)
    }
    return [c for c in cs if (c[0], c[1][0], c[1][1]) not in keys]


# -- Run ----------------------------------------------------------------------------------
def test_run_is_normalised() -> None:
    run = Run(7, 3)
    assert (run.first, run.last, run.length) == (3, 7, 5)
    assert list(run.indexes) == [3, 4, 5, 6, 7]
    assert run.contains(5) and not run.contains(8)
    assert Run.from_refs(2, 4) == Run(2, 4)
    with pytest.raises(ValueError):
        Run(-1, 2)


def test_fit_scaling() -> None:
    assert textedit.fit_scaling(30.0, 33.0) == (100.0, False)
    assert textedit.fit_scaling(33.5, 33.0) == (100.0, False)  # within the slack
    tz, overflow = textedit.fit_scaling(36.0, 33.0)
    assert abs(tz - 91.666) < 0.01 and not overflow
    assert textedit.fit_scaling(50.0, 33.0) == (textedit.MAX_TZ_SQUEEZE, True)
    assert textedit.fit_scaling(0.0, 33.0) == (100.0, False)


# -- the Word-like page -------------------------------------------------------------------
def test_reuse_embedded_font(tmp_path: Path, fonts: SystemFonts, qtbot) -> None:
    doc = open_doc(make_text_edit_pdf(tmp_path / "w.pdf"))
    assert doc.can_modify
    fonts_before = page_fonts(doc)
    with doc.lock:
        cs0 = chars_of(doc.fitz[0])
    pix0 = render(doc)
    text = doc.page_text(0)
    run = find_run(text, "courier")
    y = text.chars[run.first].origin.y()
    with qtbot.waitSignal(doc.page_changed) as blocker:
        result = doc.replace_text_run(0, run, "courrier", fonts=fonts)
    assert blocker.args == [0]
    assert isinstance(result, TextEditResult)
    assert result.plan is not None and result.plan.kind is PlanKind.REUSE
    assert result.font_kind is PlanKind.REUSE and not result.substituted
    assert result.font_family == "Calibri"
    assert result.old_text == "courier" and result.new_text == "courrier"
    assert not result.extended and result.extended_run is None
    assert result.copies == 1
    assert result.before != result.after and result.page == 0
    # same font objects, no new one
    assert page_fonts(doc) == fonts_before
    # the line reads right, every other char is untouched
    assert line_text(doc, 0, y) == _norm(LINE2).replace("courier", "courrier")
    with doc.lock:
        cs1 = chars_of(doc.fitz[0])
    others = other_chars(cs0, text, run)
    assert all(c in cs1 for c in others)
    # pixels outside the run's box are identical at 3x
    box = pixel_diff_bbox(pix0, render(doc))
    assert box is not None and inside(box, scaled_box(text, run))
    # the next save is a full one (MuPDF's redaction flag), then strict
    assert not doc.can_save_incrementally()
    doc.save()
    strict_read(tmp_path / "w.pdf")
    assert line_text(doc, 0, y) == _norm(LINE2).replace("courier", "courrier")
    doc.close()


def test_fallback_font_embedded(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_text_edit_pdf(tmp_path / "w.pdf"))
    text = doc.page_text(0)
    run = find_run(text, "Dupont")
    y = text.chars[run.first].origin.y()
    result = doc.replace_text_run(0, run, "Zidane", fonts=fonts)
    assert result.plan is not None
    assert result.plan.kind is PlanKind.SYSTEM
    assert result.plan.warning is FontWarning.SUBSTITUTED and result.substituted
    assert result.plan.family == "Calibri"
    assert line_text(doc, 0, y) == _norm(LINE1).replace("Dupont", "Zidane")
    names = {name for _, _, name in page_fonts(doc)}
    assert "PdfEd1" in names
    with doc.lock:
        assert fontembed.registered_fonts(doc.fitz)
    # the new word is drawn where the old one was
    new = doc.page_text(0)
    assert new.chars[find_run(new, "Zidane").first].origin == text.chars[run.first].origin
    doc.save()
    strict_read(tmp_path / "w.pdf")
    doc.close()


def test_wider_text_is_narrowed_to_fit(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_text_edit_pdf(tmp_path / "w.pdf"))
    text = doc.page_text(0)
    run = find_run(text, "courier")
    old_first, old_last = text.chars[run.first], text.chars[run.last]
    with doc.lock:
        calibri = font_xref(doc.fitz[0], "Calibri")[0]
        from pdfeditor.core import fontread

        embedded = fontread.read_embedded_font(doc.fitz, calibri)
    size = text.span_of(run.first).size
    old_end = old_last.origin.x() + embedded.widths[embedded.code_for("r")] * size / 1000
    result = doc.replace_text_run(0, run, "courrier", fonts=fonts)
    assert result.narrowed and not result.overflow
    assert textedit.MAX_TZ_SQUEEZE < result.scaling < 100
    assert result.natural_width > result.target_width
    new = doc.page_text(0)
    new_run = find_run(new, "courrier")
    first, last = new.chars[new_run.first], new.chars[new_run.last]
    assert abs(first.origin.x() - old_first.origin.x()) < 0.01
    new_end = last.origin.x() + (
        embedded.widths[embedded.code_for("r")] * size / 1000 * result.scaling / 100
    )
    assert abs(new_end - old_end) < 0.1
    doc.close()


def test_much_wider_text_overflows(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_text_edit_pdf(tmp_path / "w.pdf"))
    text = doc.page_text(0)
    run = find_run(text, "Dupont")
    y = text.chars[run.first].origin.y()
    result = doc.replace_text_run(0, run, "Durandeau", fonts=fonts)
    assert result.plan is not None and result.plan.kind is PlanKind.REUSE
    assert result.scaling == textedit.MAX_TZ_SQUEEZE and result.overflow and result.narrowed
    # the new word runs into " est" (overflow), so compare in content order
    flat = _norm("".join(ch.c for ch in doc.page_text(0).chars))
    assert "Durandeau" in flat and "Dupont" not in flat
    assert line_text(doc, 0, y).startswith("Monsieur Jean Durandea")
    doc.close()


def test_annotations_survive_and_absent_annots_stays_absent(
    tmp_path: Path, fonts: SystemFonts
) -> None:
    path = make_text_edit_pdf(tmp_path / "w.pdf")
    doc = open_doc(path)
    with doc.lock:
        assert doc.fitz.xref_get_key(doc.fitz[0].xref, "Annots")[0] == "null"
    text = doc.page_text(0)
    doc.replace_text_run(0, find_run(text, "Jean"), "Paul", fonts=fonts)
    with doc.lock:
        page_xref = doc.fitz[0].xref
        assert doc.fitz.xref_get_key(page_xref, "Annots")[0] == "null"
        assert "Annots" not in doc.fitz.xref_get_keys(page_xref)
    doc.save()
    strict_read(path)
    doc.close()
    # a FreeText over the run (same /NM) and a Link survive
    pdf = pymupdf.open(path)
    page = pdf[0]
    text_rect = pymupdf.Rect(60, 85, 200, 110)  # covers "Monsieur Paul"
    annot = page.add_freetext_annot(text_rect, "note", fontsize=8)
    pdf.xref_set_key(annot.xref, "NM", "(nm-1)")
    link_xref = pdf.get_new_xref()
    pdf.update_object(
        link_xref,
        "<</Type/Annot/Subtype/Link/Rect[60 732 200 757]/Border[0 0 0]"
        "/A<</S/URI/URI(http://example.org)>>>>",
    )
    kind, value = pdf.xref_get_key(page.xref, "Annots")
    pdf.xref_set_key(page.xref, "Annots", value[:-1] + f" {link_xref} 0 R]")
    pdf.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    pdf.close()
    doc = open_doc(path)
    text = doc.page_text(0)
    run = find_run(text, "Paul")
    doc.replace_text_run(0, run, "Jean", fonts=fonts)
    with doc.lock:
        page = doc.fitz[0]
        kinds = [(a.type[1], a.info.get("id")) for a in page.annots()]
        links = page.get_links()
    assert ("FreeText", "nm-1") in kinds
    assert len(links) == 1 and links[0]["uri"] == "http://example.org"
    doc.save()
    strict_read(path)
    doc.close()


def test_indirect_annots_array_is_untouched(tmp_path: Path, fonts: SystemFonts) -> None:
    path = make_text_edit_pdf(tmp_path / "w.pdf")
    pdf = pymupdf.open(path)
    page = pdf[0]
    annot = page.add_freetext_annot(pymupdf.Rect(300, 300, 400, 320), "note", fontsize=8)
    arr = pdf.get_new_xref()
    pdf.update_object(arr, f"[{annot.xref} 0 R]")
    pdf.xref_set_key(page.xref, "Annots", f"{arr} 0 R")
    path = tmp_path / "annots.pdf"
    pdf.save(path, garbage=0, deflate=True)
    pdf.close()
    doc = open_doc(path)
    with doc.lock:
        page_xref = doc.fitz[0].xref
        before = doc.fitz.xref_get_key(page_xref, "Annots")
        arr_xref = int(before[1].split()[0])
        arr_before = doc.fitz.xref_object(arr_xref)
    doc.replace_text_run(0, find_run(doc.page_text(0), "Jean"), "Paul", fonts=fonts)
    with doc.lock:
        assert doc.fitz.xref_get_key(page_xref, "Annots") == before
        assert doc.fitz.xref_object(arr_xref) == arr_before
        assert [a.type[1] for a in doc.fitz[0].annots()] == ["FreeText"]
    doc.save()
    strict_read(path)
    doc.close()


def test_removing_the_title_keeps_its_font(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_text_edit_pdf(tmp_path / "w.pdf"))
    text = doc.page_text(0)
    run = find_run(text, TITLE)
    y = text.chars[run.first].origin.y()
    result = doc.replace_text_run(0, run, "", fonts=fonts)
    assert result.plan is None and result.new_text == "" and _norm(result.old_text) == TITLE
    assert line_text(doc, 0, y) == ""
    names = {name for _, _, name in page_fonts(doc)}
    assert "Times" in names
    with doc.lock:
        kind, value = doc.fitz.xref_get_key(doc.fitz[0].xref, "Resources")
        assert kind == "xref"  # the original indirect dictionary is back
    # undo brings the title back with its font
    doc.set_page_content(0, result.before, expect=result.after)
    assert line_text(doc, 0, y) == TITLE
    doc.close()


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
@pytest.mark.parametrize("cropbox", [False, True])
def test_rotation_and_cropbox(
    tmp_path: Path, fonts: SystemFonts, rotate: int, cropbox: bool
) -> None:
    doc = open_doc(make_text_edit_pdf(tmp_path / "w.pdf", rotate=rotate, cropbox=cropbox))
    with doc.lock:
        cs0 = chars_of(doc.fitz[0])
    pix0 = render(doc)
    text = doc.page_text(0)
    run = find_run(text, "courier")
    old_first = text.chars[run.first]
    result = doc.replace_text_run(0, run, "courrier", fonts=fonts)
    assert result.plan is not None and result.plan.kind is PlanKind.REUSE
    new = doc.page_text(0)
    new_run = find_run(new, "courrier")
    o = new.chars[new_run.first].origin
    assert abs(o.x() - old_first.origin.x()) < 0.01 and abs(o.y() - old_first.origin.y()) < 0.01
    # the new chars follow the line's direction and keep the size
    line = new.line_of(new_run.first)
    assert line.dir == text.line_of(run.first).dir
    assert abs(new.span_of(new_run.first).size - text.span_of(run.first).size) < 1.0
    with doc.lock:
        cs1 = chars_of(doc.fitz[0])
    assert all(c in cs1 for c in other_chars(cs0, text, run))
    assert inside(pixel_diff_bbox(pix0, render(doc)), scaled_box(text, run))
    # undo is exact
    doc.set_page_content(0, result.before, expect=result.after)
    assert render(doc).samples == pix0.samples
    doc.close()


def test_undo_redo_pixel_identical_also_after_full_save(tmp_path: Path, fonts: SystemFonts) -> None:
    path = make_text_edit_pdf(tmp_path / "w.pdf")
    doc = open_doc(path)
    pix0 = render(doc)
    text = doc.page_text(0)
    r1 = doc.replace_text_run(0, find_run(text, "Dupont"), "Zidane", fonts=fonts)  # new font
    text = doc.page_text(0)
    r2 = doc.replace_text_run(0, find_run(text, "Titre"), "Sous-titre", fonts=fonts)
    pix_after = render(doc)
    assert r2.before == r1.after
    doc.set_page_content(0, r2.before, expect=r2.after)
    doc.set_page_content(0, r1.before, expect=r1.after)
    assert render(doc).samples == pix0.samples
    doc.set_page_content(0, r1.after, expect=r1.before)
    doc.set_page_content(0, r2.after, expect=r2.before)
    assert render(doc).samples == pix_after.samples
    # a full save (garbage=3) renumbers objects; the byte snapshots still apply
    doc.save_as(tmp_path / "saved.pdf")
    strict_read(tmp_path / "saved.pdf")
    assert render(doc).samples == pix_after.samples
    doc.set_page_content(0, r2.before, expect=r2.after)
    doc.set_page_content(0, r1.before, expect=r1.after)
    assert render(doc).samples == pix0.samples
    doc.set_page_content(0, r1.after, expect=r1.before)
    doc.set_page_content(0, r2.after, expect=r2.before)
    assert render(doc).samples == pix_after.samples
    assert doc.can_save_incrementally()
    doc.save()
    strict_read(tmp_path / "saved.pdf")
    doc.close()


def test_set_page_content_guards(tmp_path: Path, fonts: SystemFonts, monkeypatch, qtbot) -> None:
    doc = open_doc(make_text_edit_pdf(tmp_path / "w.pdf"))
    text = doc.page_text(0)
    result = doc.replace_text_run(0, find_run(text, "Jean"), "Paul", fonts=fonts)
    pix = render(doc)
    with pytest.raises(TextEditError) as info:
        doc.set_page_content(0, result.before, expect=b"something else")
    assert info.value.reason is EditReason.STALE
    assert render(doc).samples == pix.samples
    with pytest.raises(IndexError):
        doc.set_page_content(5, result.before)
    monkeypatch.setattr(textedit, "MAX_CONTENT_BYTES", 10)
    with pytest.raises(TextEditError) as info:
        doc.set_page_content(0, result.before)
    assert info.value.reason is EditReason.TOO_COMPLEX
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, find_run(doc.page_text(0), "Paul"), "Jean", fonts=fonts)
    assert info.value.reason is EditReason.TOO_COMPLEX
    assert render(doc).samples == pix.samples
    monkeypatch.setattr(textedit, "MAX_CONTENT_BYTES", 20 * 1024 * 1024)
    with qtbot.waitSignal(doc.page_changed):
        doc.set_page_content(0, result.before, expect=result.after)
    assert "Jean" in _norm(doc.page_text(0).text)
    doc.close()


def test_aes256_edit_full_save_then_incremental(tmp_path: Path, fonts: SystemFonts) -> None:
    path = make_text_edit_pdf(tmp_path / "aes.pdf", encrypted=True)
    doc = open_doc(path, password=PASSWORD)
    assert doc.can_modify
    pix0 = render(doc)
    text = doc.page_text(0)
    run = find_run(text, "Dupont")
    y = text.chars[run.first].origin.y()
    result = doc.replace_text_run(0, run, "Durand", fonts=fonts)
    assert not doc.can_save_incrementally()
    doc.save()
    strict_read(path, password=PASSWORD)
    assert doc.encryption_method is not None and "AES" in doc.encryption_method
    assert line_text(doc, 0, y) == _norm(LINE1).replace("Dupont", "Durand")
    # undo after the reload, then an incremental save
    size = path.stat().st_size
    doc.set_page_content(0, result.before, expect=result.after)
    assert doc.can_save_incrementally()
    doc.save()
    assert path.stat().st_size > size
    strict_read(path, password=PASSWORD)
    assert render(doc).samples == pix0.samples
    doc.close()
    check = pymupdf.open(path)
    assert check.authenticate(PASSWORD)
    assert check[0].get_pixmap(matrix=SCALE).samples == pix0.samples
    check.close()


# -- refusals -----------------------------------------------------------------------------
def test_invisible_ocr_text_refused(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_ocr_pdf(tmp_path / "ocr.pdf"))
    text = doc.page_text(0)
    run = find_run(text, "reconnu")
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, run, "lu", fonts=fonts)
    assert info.value.reason is EditReason.INVISIBLE
    doc.close()


def test_no_permission_refused(tmp_path: Path, fonts: SystemFonts) -> None:
    path = make_text_edit_pdf(tmp_path / "w.pdf")
    pdf = pymupdf.open(path)
    pdf.save(
        tmp_path / "locked.pdf",
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="",
        owner_pw=OWNER_PASSWORD,
        permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ACCESSIBILITY,
    )
    pdf.close()
    doc = open_doc(tmp_path / "locked.pdf")
    assert not doc.can_modify
    text = doc.page_text(0)
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, find_run(text, "Jean"), "Paul", fonts=fonts)
    assert info.value.reason is EditReason.PERMISSION
    with pytest.raises(TextEditError) as info:
        doc.set_page_content(0, b"q Q")
    assert info.value.reason is EditReason.PERMISSION
    doc.close()


def test_xobject_text_refused_but_page_text_edits(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_xobject_text_pdf(tmp_path / "x.pdf"))
    text = doc.page_text(0)
    run = find_run(text, "formulaire")
    assert text.span_of(run.first).in_xobject
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, run, "modèle", fonts=fonts)
    assert info.value.reason is EditReason.XOBJECT
    pix0 = render(doc)
    run = find_run(text, "Dupont")
    y = text.chars[run.first].origin.y()
    result = doc.replace_text_run(0, run, "Durand", fonts=fonts)
    assert line_text(doc, 0, y) == _norm(LINE1).replace("Dupont", "Durand")
    assert "formulaire" in _norm(doc.page_text(0).text)
    assert inside(pixel_diff_bbox(pix0, render(doc)), scaled_box(text, run))
    doc.set_page_content(0, result.before, expect=result.after)
    assert render(doc).samples == pix0.samples
    doc.close()


def test_multi_span_and_stale_runs(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_text_edit_pdf(tmp_path / "w.pdf"))
    text = doc.page_text(0)
    a = find_run(text, "Lyon")
    b = find_run(text, "Deuxième")
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, Run(a.first, b.last), "x", fonts=fonts)
    assert info.value.reason is EditReason.MULTI_SPAN
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, Run(len(text.chars), len(text.chars) + 2), "x", fonts=fonts)
    assert info.value.reason is EditReason.STALE
    with pytest.raises(ValueError):
        doc.replace_text_run(0, a, "two\nlines", fonts=fonts)
    with pytest.raises(IndexError):
        doc.replace_text_run(3, a, "x", fonts=fonts)
    doc.close()


def test_empty_page_has_no_text(tmp_path: Path, fonts: SystemFonts) -> None:
    pdf = pymupdf.open()
    pdf.new_page()
    pdf.save(tmp_path / "blank.pdf")
    pdf.close()
    doc = open_doc(tmp_path / "blank.pdf")
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, Run(0, 0), "x", fonts=fonts)
    assert info.value.reason is EditReason.NO_TEXT
    doc.close()


def test_rtl_text_refused(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_rtl_pdf(tmp_path / "rtl.pdf"))
    text = doc.page_text(0)
    run = find_run(text, RTL_TEXT[::-1])  # MuPDF lists the chars in visual order
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, run, RTL_TEXT[:2], fonts=fonts)
    assert info.value.reason is EditReason.DIRECTION
    with pytest.raises(TextEditError) as info:  # a single RTL char (no direction to read)
        doc.replace_text_run(0, Run(run.first, run.first), "x", fonts=fonts)
    assert info.value.reason is EditReason.DIRECTION
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, find_run(text, "Jean"), "אב", fonts=fonts)
    assert info.value.reason is EditReason.DIRECTION
    assert "Jean" in _norm(doc.page_text(0).text)
    doc.close()


# -- fake bold ----------------------------------------------------------------------------
def test_fake_bold_edits_every_copy(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_fake_bold_pdf(tmp_path / "fb.pdf"))
    text = doc.page_text(0)
    flat = _norm("".join(ch.c for ch in text.chars))
    assert flat.count(FAKE_BOLD_LINE) == 2
    run = find_run(text, "Titre")
    y = text.chars[run.first].origin.y()
    result = doc.replace_text_run(0, run, "Texte", fonts=fonts)
    assert result.copies == 2
    new = doc.page_text(0)
    flat = _norm("".join(ch.c for ch in new.chars))
    assert flat.count("Texte") == 2 and "Titre" not in flat
    # both copies sit at the old origin
    origins = {
        (round(ch.origin.x(), 2), round(ch.origin.y(), 2))
        for ch in new.chars
        if ch.c == "T" and abs(ch.origin.y() - y) < 0.05
    }
    assert origins == {(round(text.chars[run.first].origin.x(), 2), round(y, 2))}
    doc.close()


def test_partial_duplicate_refused(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_fake_bold_pdf(tmp_path / "fb.pdf"))
    text = doc.page_text(0)
    pix0 = render(doc)
    run = find_run(text, PART_BOLD_LINE)
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, run, "Mot simple", fonts=fonts)
    assert info.value.reason is EditReason.DUPLICATE
    assert render(doc).samples == pix0.samples
    # the doubled word alone is editable (both copies), the single word too
    result = doc.replace_text_run(0, find_run(text, PART_BOLD_WORD), "triple", fonts=fonts)
    assert result.copies == 2
    text = doc.page_text(0)
    result = doc.replace_text_run(0, find_run(text, "Mot"), "Nom", fonts=fonts)
    assert result.copies == 1
    flat = _norm("".join(ch.c for ch in doc.page_text(0).chars))
    assert flat.count("triple") == 2 and "Nom" in flat and "Mot" not in flat
    doc.close()


# -- collateral loop ----------------------------------------------------------------------
def test_collateral_loop_extends_the_run(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_overlap_pdf(tmp_path / "ov.pdf"))
    text = doc.page_text(0)
    run = find_run(text, "totale")
    before_e = text.chars[run.first - 2]  # the final "e" of "Facture"
    assert before_e.c == "e"
    assert before_e.bbox.right() > text.chars[run.first].bbox.left()  # boxes overlap
    y = text.chars[run.first].origin.y()
    result = doc.replace_text_run(0, run, "globale", fonts=fonts)
    assert result.extended and result.extended_run is not None
    assert result.extended_run.first < run.first and result.extended_run.last == run.last
    assert result.old_text.endswith(" totale") and result.old_text.startswith("e")
    assert result.new_text == result.old_text[: -len("totale")] + "globale"
    assert result.requested_text == "globale"
    assert line_text(doc, 0, y) == "Facture globale"
    doc.set_page_content(0, result.before, expect=result.after)
    assert line_text(doc, 0, y) == "Facture totale"
    doc.close()


# -- other fixtures edit without error ----------------------------------------------------
def test_simple_font_reuse_and_fallback(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_simple_font_pdf(tmp_path / "s.pdf"))
    text = doc.page_text(0)
    run = find_run(text, "Dupont")
    y = text.chars[run.first].origin.y()
    fonts_before = page_fonts(doc)
    result = doc.replace_text_run(0, run, "Dupant", fonts=fonts)  # every glyph present
    assert result.plan is not None and result.plan.kind is PlanKind.REUSE
    assert page_fonts(doc) == fonts_before
    assert line_text(doc, 0, y) == "Monsieur Jean Dupant à Lyon"
    text = doc.page_text(0)
    result = doc.replace_text_run(0, find_run(text, "Dupant"), "Dupond", fonts=fonts)  # 'd'
    assert result.plan is not None and result.plan.kind is PlanKind.SYSTEM
    assert result.plan.family == "Calibri"
    assert line_text(doc, 0, y) == "Monsieur Jean Dupond à Lyon"
    doc.save()
    strict_read(tmp_path / "s.pdf")
    doc.close()


def test_print_like_pdf_falls_back_to_arial(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_print_like_pdf(tmp_path / "p.pdf"))
    text = doc.page_text(0)
    run = find_run(text, "Facture")
    y = text.chars[run.first].origin.y()
    before = line_text(doc, 0, y)  # the hyphen reads U+00AD through MuPDF's ToUnicode
    assert before.startswith(PRINT_LINES[0][:10])
    result = doc.replace_text_run(0, run, "Factures", fonts=fonts)  # no 's' in the subset
    assert result.plan is not None and result.plan.kind is PlanKind.SYSTEM
    assert result.plan.family == "Arial"
    assert line_text(doc, 0, y) == before.replace("Facture", "Factures")
    doc.close()


def test_type3_kerned_and_inherited_fixtures(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = open_doc(make_type3_pdf(tmp_path / "t3.pdf"))
    text = doc.page_text(0)
    run = find_run(text, "abba")
    y = text.chars[run.first].origin.y()
    result = doc.replace_text_run(0, run, "abab", fonts=fonts)
    assert result.plan is not None and result.plan.kind is PlanKind.GENERIC
    assert line_text(doc, 0, y) == "abab"
    text = doc.page_text(0)
    run = find_run(text, "Dupont")
    doc.replace_text_run(0, run, "Durand", fonts=fonts)
    assert "Durand" in _norm(doc.page_text(0).text)
    doc.close()

    doc = open_doc(make_kerned_tj_pdf(tmp_path / "k.pdf"))
    text = doc.page_text(0)
    run = find_run(text, "Facture")
    y = text.chars[run.first].origin.y()
    result = doc.replace_text_run(0, run, "Fracture", fonts=fonts)
    assert result.plan is not None and result.plan.kind is PlanKind.REUSE
    assert line_text(doc, 0, y) == "Fracture totale due"
    doc.close()

    doc = open_doc(make_inherited_resources_pdf(tmp_path / "i.pdf"))
    with doc.lock:
        page_xref = doc.fitz[0].xref
        assert doc.fitz.xref_get_key(page_xref, "Resources")[0] == "null"
    text = doc.page_text(0)
    run = find_run(text, "Ressources")
    y = text.chars[run.first].origin.y()
    pix0 = render(doc)
    result = doc.replace_text_run(0, run, "Ressource", fonts=fonts)
    assert line_text(doc, 0, y) == "Ressource héritées du nœud Pages"
    doc.set_page_content(0, result.before, expect=result.after)
    assert render(doc).samples == pix0.samples
    doc.save()
    strict_read(tmp_path / "i.pdf")
    doc.close()


def test_shared_resources_other_page_unchanged(tmp_path: Path, fonts: SystemFonts) -> None:
    path = make_text_edit_pdf(tmp_path / "w.pdf")
    pdf = pymupdf.open(path)
    p0 = pdf[0]
    kind, value = pdf.xref_get_key(p0.xref, "Resources")
    assert kind == "xref"
    p1 = pdf.new_page(width=595, height=842)
    pdf.xref_set_key(p1.xref, "Resources", value)
    cx = pdf.get_new_xref()
    pdf.update_object(cx, "<<>>")
    pdf.update_stream(cx, b"BT /Calibri 11 Tf 72 700 Td <0018011E> Tj ET")
    pdf.xref_set_key(p1.xref, "Contents", f"{cx} 0 R")
    path = tmp_path / "shared.pdf"
    pdf.save(path, garbage=0, deflate=True)
    pdf.close()
    doc = open_doc(path)
    pix1 = render(doc, 1)
    text = doc.page_text(0)
    run = find_run(text, "Jean")
    doc.replace_text_run(0, run, "Paul", fonts=fonts)
    assert render(doc, 1).samples == pix1.samples
    with doc.lock:
        assert doc.fitz.xref_get_key(doc.fitz[0].xref, "Resources") == (kind, value)
        assert doc.fitz.xref_get_key(doc.fitz[1].xref, "Resources") == (kind, value)
    doc.save()
    strict_read(path)
    doc.close()


def test_text_cache_refreshed_and_edit_is_fast(tmp_path: Path, fonts: SystemFonts) -> None:
    from timing import best_time

    doc = open_doc(make_text_edit_pdf(tmp_path / "w.pdf"))
    text = doc.page_text(0)
    run = find_run(text, "Jean")
    result = doc.replace_text_run(0, run, "Paul", fonts=fonts)
    assert doc.page_text(0) is not text
    assert "Paul" in _norm(doc.page_text(0).text) and "Jean" not in _norm(doc.page_text(0).text)

    def edit() -> None:
        doc.replace_text_run(0, find_run(doc.page_text(0), "Paul"), "Jean", fonts=fonts)
        doc.set_page_content(0, result.before)

    best, _runs = best_time(edit, EDIT_BUDGET_S)
    assert best < EDIT_BUDGET_S
    doc.close()
