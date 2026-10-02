"""M7-T8: hardening of page text editing — many edits on one page, a page too large to
edit, odd fonts (ligature glyph, Base-14 not embedded or embedded as Type1, CFF installed
faces)."""

from __future__ import annotations

import time
from pathlib import Path

import pymupdf
import pytest
from pdfcheck import strict_read
from textedit_fixtures import (
    ARIAL_PATH,
    BASE14_LINE,
    CALIBRI_PATH,
    LIGATURE_LINE,
    LIGATURE_WORD,
    TIMES_PATH,
    make_base14_pdf,
    make_cff_font,
    make_ligature_pdf,
    make_text_edit_pdf,
    needs_text_fonts,
)

from pdfeditor.core import textedit
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.fontmatch import FontWarning, PlanKind, SystemFonts
from pdfeditor.core.pagetext import PageText
from pdfeditor.core.textedit import EditReason, Run, TextEditError

pytestmark = needs_text_fonts

#: 200 edits of one word on the Word-like page (≈ 4.8 s measured: ≈ 24 ms per edit).
MANY_EDITS = 200
MANY_EDITS_BUDGET_S = 10.0
#: The page content never grows by more than this over its original size: every edit
#: removes the old glyphs' operators (the redaction) before appending one text block.
CONTENT_GROWTH_BOUND = 2 * 1024
#: The fully saved file never grows by more than this (one subset of the fallback font).
FILE_GROWTH_BOUND = 64 * 1024
#: Refusing a page above textedit.MAX_CONTENT_BYTES (≈ 50 ms measured).
TOO_COMPLEX_BUDGET_S = 2.0


@pytest.fixture(scope="module")
def fonts() -> SystemFonts:
    return SystemFonts.from_paths([CALIBRI_PATH, ARIAL_PATH, TIMES_PATH])


def _norm(s: str) -> str:
    return s.replace("\xa0", " ")


def find_run(text: PageText, word: str) -> Run:
    flat = _norm("".join(ch.c for ch in text.chars))
    start = flat.index(word)
    return Run(start, start + len(word) - 1)


def content_size(doc: PdfDocument, page: int = 0) -> int:
    with doc.lock:
        return len(doc.fitz[page].read_contents())


def page_text(doc: PdfDocument) -> str:
    return _norm(doc.page_text(0).text)


# -- many edits ---------------------------------------------------------------------------
def test_many_edits_are_fast_and_bounded(tmp_path: Path, fonts: SystemFonts) -> None:
    path = make_text_edit_pdf(tmp_path / "w.pdf")
    original_file = path.stat().st_size
    doc = PdfDocument.open(path)
    original = content_size(doc)
    # Jean -> Paul (embedded font) -> Zoé ('Z' missing: Calibri fallback, then extended)
    words = ["Jean", "Paul", "Zoé"]
    largest = original
    start = time.perf_counter()
    for k in range(MANY_EDITS):
        old, new = words[k % 3], words[(k + 1) % 3]
        doc.replace_text_run(0, find_run(doc.page_text(0), old), new, fonts=fonts)
        largest = max(largest, content_size(doc))
    elapsed = time.perf_counter() - start
    assert elapsed < MANY_EDITS_BUDGET_S, f"{MANY_EDITS} edits took {elapsed:.1f} s"
    assert largest - original <= CONTENT_GROWTH_BOUND
    final = words[MANY_EDITS % 3]
    text = page_text(doc)
    assert final in text and all(w not in text for w in words if w != final)
    with doc.lock:
        own = [f for f in doc.fitz[0].get_fonts(full=True) if str(f[4]).startswith("PdfEd")]
    assert len(own) == 1  # one fallback font per document and face, extended in place
    doc.save()
    assert path.stat().st_size - original_file <= FILE_GROWTH_BOUND
    strict_read(path)
    doc.close()


# -- too complex --------------------------------------------------------------------------
def _pad_content(path: Path, out: Path, size: int) -> Path:
    """``path``'s page with its content padded by spaces to ``size`` bytes (compresses to
    a few KB; MuPDF's lexer skips it quickly)."""
    pdf = pymupdf.open(path)
    page = pdf[0]
    content = page.read_contents()
    xref = pdf.get_new_xref()
    pdf.update_object(xref, "<< >>")
    pdf.update_stream(xref, content + b"\n" + b" " * (size - len(content) - 1), compress=True)
    pdf.xref_set_key(page.xref, "Contents", f"{xref} 0 R")
    pdf.save(out, garbage=3, deflate=True)
    pdf.close()
    return out


def test_huge_page_is_refused_quickly(tmp_path: Path, fonts: SystemFonts) -> None:
    size = textedit.MAX_CONTENT_BYTES + 1024
    path = _pad_content(make_text_edit_pdf(tmp_path / "w.pdf"), tmp_path / "big.pdf", size)
    doc = PdfDocument.open(path)
    text = doc.page_text(0)
    assert content_size(doc) == size
    start = time.perf_counter()
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, find_run(text, "Jean"), "Paul", fonts=fonts)
    elapsed = time.perf_counter() - start
    assert info.value.reason is EditReason.TOO_COMPLEX
    assert elapsed < TOO_COMPLEX_BUDGET_S
    assert content_size(doc) == size
    assert doc.can_save_incrementally()  # nothing was redacted
    with pytest.raises(TextEditError) as info:  # an undo/redo of that size too
        doc.set_page_content(0, b" " * size)
    assert info.value.reason is EditReason.TOO_COMPLEX
    doc.close()


def test_page_just_below_the_limit_edits(tmp_path: Path, fonts: SystemFonts) -> None:
    size = textedit.MAX_CONTENT_BYTES - 4096
    path = _pad_content(make_text_edit_pdf(tmp_path / "w.pdf"), tmp_path / "big.pdf", size)
    doc = PdfDocument.open(path)
    doc.replace_text_run(0, find_run(doc.page_text(0), "Jean"), "Paul", fonts=fonts)
    assert "Paul" in page_text(doc) and "Jean" not in page_text(doc)
    doc.close()


# -- odd fonts ----------------------------------------------------------------------------
def test_ligature_glyph_is_retyped_as_letters(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = PdfDocument.open(make_ligature_pdf(tmp_path / "lig.pdf"))
    text = doc.page_text(0)
    assert _norm(text.text).strip() == LIGATURE_LINE
    run = find_run(text, LIGATURE_WORD)
    assert run.length == 7  # "ﬁ" is one character
    result = doc.replace_text_run(0, run, "bénéfices", fonts=fonts)
    assert result.old_text == LIGATURE_WORD
    assert result.plan is not None and result.plan.kind is PlanKind.REUSE  # f, i on the page
    after = page_text(doc)
    assert "ﬁ" not in after
    assert "bénéfices" in after and "fiscal" in after
    doc.save()
    strict_read(tmp_path / "lig.pdf")
    doc.close()


@pytest.mark.parametrize("embedded", [False, True], ids=["no-fontfile", "type1c"])
def test_base14_fonts_fall_back(tmp_path: Path, fonts: SystemFonts, embedded: bool) -> None:
    path = make_base14_pdf(tmp_path / "h.pdf", embedded=embedded)
    doc = PdfDocument.open(path)
    assert page_text(doc).strip() == BASE14_LINE
    with doc.lock:
        (entry,) = doc.fitz[0].get_fonts(full=True)
    assert entry[2] == "Type1" and entry[1] == ("cff" if embedded else "n/a")
    result = doc.replace_text_run(0, find_run(doc.page_text(0), "Dupont"), "Zidane", fonts=fonts)
    assert result.plan is not None
    assert result.plan.kind is not PlanKind.REUSE  # a Type1 program is never reused
    assert result.plan.family == "Arial"
    assert result.plan.warning is FontWarning.SUBSTITUTED
    after = page_text(doc)
    assert "Zidane" in after and "Dupont" not in after
    doc.save()
    strict_read(path)
    doc.close()


def test_cff_installed_face_is_skipped(tmp_path: Path, fonts: SystemFonts) -> None:
    # An installed CFF .otf "Helvetica" covering the text: fontembed cannot subset it,
    # so the plan falls through to the generic Arial instead of refusing the edit.
    otf = make_cff_font(tmp_path / "helvetica.otf", "Helvetica", "ZidanMonsieurJt")
    with_cff = SystemFonts.from_paths([otf, CALIBRI_PATH, ARIAL_PATH, TIMES_PATH])
    (face,) = with_cff.family_faces("Helvetica")
    assert not face.truetype and not face.embeddable
    assert with_cff.find("Helvetica") is None
    assert all(f.truetype for f in fonts.faces)
    doc = PdfDocument.open(make_base14_pdf(tmp_path / "h.pdf"))
    run = find_run(doc.page_text(0), "Dupont")
    result = doc.replace_text_run(0, run, "Zidane", fonts=with_cff)
    assert result.plan is not None and result.plan.kind is PlanKind.GENERIC
    assert result.plan.family == "Arial"
    assert "Zidane" in page_text(doc)
    doc.close()


def test_only_cff_faces_installed_refuses_cleanly(tmp_path: Path) -> None:
    otf = make_cff_font(tmp_path / "arial.otf", "Arial", "ZidanMonsieurJt")
    only_cff = SystemFonts.from_paths([otf])
    doc = PdfDocument.open(make_base14_pdf(tmp_path / "h.pdf"))
    with doc.lock:
        before = doc.fitz[0].read_contents()
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, find_run(doc.page_text(0), "Dupont"), "Zidane", fonts=only_cff)
    assert info.value.reason is EditReason.NO_FONT
    with doc.lock:
        assert doc.fitz[0].read_contents() == before
    assert page_text(doc).strip() == BASE14_LINE
    doc.close()
