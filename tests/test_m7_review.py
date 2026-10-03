"""M7 review findings (core): privacy across undo/redo saves, reloads while a page is
held, our fonts' names, borrowed codes' ToUnicode, collateral neighbours of other spans,
the stale-run guard and refused characters."""

from __future__ import annotations

import re
import zlib
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtGui import QUndoStack
from textedit_fixtures import (
    ARIAL_PATH,
    CALIBRI_PATH,
    LINE1,
    TIMES_PATH,
    make_text_edit_pdf,
    needs_text_fonts,
)

from pdfeditor.core.commands import ReplaceTextCommand
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.fontmatch import SystemFonts
from pdfeditor.core.pagetext import PageText
from pdfeditor.core.textedit import Run

pytestmark = needs_text_fonts


@pytest.fixture(scope="module")
def fonts() -> SystemFonts:
    return SystemFonts.from_paths([CALIBRI_PATH, ARIAL_PATH, TIMES_PATH])


def _norm(s: str) -> str:
    return s.replace("\xa0", " ")


def find_run(text: PageText, word: str) -> Run:
    flat = _norm("".join(ch.c for ch in text.chars))
    start = flat.index(word)
    return Run(start, start + len(word) - 1)


def file_streams(path: Path) -> list[bytes]:
    """Every stream of every revision of the file at ``path`` (inflated when possible)."""
    out: list[bytes] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", path.read_bytes(), re.S):
        raw = m.group(1)
        try:
            out.append(zlib.decompress(raw))
        except zlib.error:
            out.append(raw)
    return out


# -- 4. privacy: save, undo, save, redo, save -----------------------------------------------
def test_removed_text_gone_after_save_undo_save_redo_save(
    tmp_path: Path, fonts: SystemFonts, qapp
) -> None:
    path = make_text_edit_pdf(tmp_path / "w.pdf")
    doc = PdfDocument.open(path)
    with doc.lock:
        content = doc.fitz[0].read_contents()
    line_hex = re.search(rb"\[<([0-9A-Fa-f]+)>\]\s*TJ", content).group(1)  # type: ignore[union-attr]
    start = LINE1.index("Dupont")
    dupont = line_hex[start * 4 : (start + 6) * 4]
    assert any(dupont in s for s in file_streams(path))

    stack = QUndoStack()
    cmd = ReplaceTextCommand(doc, 0, find_run(doc.page_text(0), "Dupont"), "Zidane", fonts=fonts)
    cmd.apply_now()
    stack.push(cmd)
    doc.save()
    assert not any(dupont in s for s in file_streams(path))
    stack.undo()
    assert cmd.error is None
    doc.save()  # the old text is back: it is in the file, as it should be
    assert any(dupont in s for s in file_streams(path))
    stack.redo()
    assert cmd.error is None
    assert not doc.can_save_incrementally()
    doc.save()
    assert not any(dupont in s for s in file_streams(path))
    assert "Zidane" in _norm(doc.page_text(0).text)
    doc.close()
    reopened = pymupdf.open(path)
    assert "Zidane" in _norm(reopened[0].get_text())
    reopened.close()


# -- 3. a Page held elsewhere (render thread, caller) during an edit ----------------------
def _with_annots(tmp_path: Path) -> Path:
    src = make_text_edit_pdf(tmp_path / "src.pdf")
    doc = pymupdf.open(src)
    page = doc[0]
    page.insert_link(
        {"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 90, 200, 102), "uri": "https://a.b"}
    )
    page.add_freetext_annot(pymupdf.Rect(100, 95, 200, 110), "note")
    del page
    out = tmp_path / "annots.pdf"
    doc.save(out)
    doc.close()
    return out


def _edit_words(doc: PdfDocument, fonts: SystemFonts) -> None:
    for word, new in (("Dupont", "Zidane"), ("Jean", "Paul"), ("Zidane", "Martin")):
        doc.replace_text_run(0, find_run(doc.page_text(0), word), new, fonts=fonts)


def _state(doc: PdfDocument) -> tuple[object, ...]:
    with doc.lock:
        page = doc.fitz[0]
        state = (
            page.read_contents(),
            sorted(a.type[1] for a in page.annots()),
            [link["uri"] for link in page.get_links()],
            doc.fitz.xref_get_key(page.xref, "Annots"),
            page.get_pixmap(annots=True).samples,
        )
        del page
    return state


def test_edit_while_another_page_object_is_alive(tmp_path: Path, fonts: SystemFonts, qapp) -> None:
    path = _with_annots(tmp_path)
    plain = PdfDocument.open(path)
    _edit_words(plain, fonts)
    expected = _state(plain)
    plain.close()

    doc = PdfDocument.open(path)
    with doc.lock:
        held = doc.fitz[0]  # e.g. the render thread between its render and its return
    _edit_words(doc, fonts)
    assert _state(doc) == expected
    assert "Martin" in _norm(doc.page_text(0).text)
    with doc.lock:
        assert "Martin" in _norm(held.get_text())
        del held
    doc.close()


# -- 2. our font's name vs a Word-like "ABCDEE+Calibri" -----------------------------------
def _word_named(tmp_path: Path) -> Path:
    """The Word-like page with its Calibri renamed like Word does ("ABCDEE+Calibri")."""
    doc = pymupdf.open(make_text_edit_pdf(tmp_path / "src.pdf"))
    for entry in doc[0].get_fonts(full=True):
        xref, base = int(entry[0]), str(entry[3])
        if "Calibri" not in base:
            continue
        doc.xref_set_key(xref, "BaseFont", "/ABCDEE+Calibri")
        cid = int(doc.xref_get_key(xref, "DescendantFonts")[1].strip("[]").split()[0])
        doc.xref_set_key(cid, "BaseFont", "/ABCDEE+Calibri")
        fd = int(doc.xref_get_key(cid, "FontDescriptor")[1].split()[0])
        doc.xref_set_key(fd, "FontName", "/ABCDEE+Calibri")
    out = tmp_path / "word.pdf"
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    return out


def test_our_font_never_shadows_the_documents_same_family(
    tmp_path: Path, fonts: SystemFonts, qapp
) -> None:
    doc = PdfDocument.open(_word_named(tmp_path))
    first = doc.replace_text_run(0, find_run(doc.page_text(0), "Dupont"), "Zidane", fonts=fonts)
    assert first.substituted and first.font_family == "Calibri"  # 'Z' is not in the subset
    text = doc.page_text(0)
    spans = {
        s.font: (s.font_xref, s.resource_name)
        for b in text.blocks
        for line in b.lines
        for s in line.spans
    }
    assert spans["Calibri"][0] and spans["Calibri"][1] == "Calibri"
    assert spans["Calibri-PDFEditor"][1].startswith("PdfEd")
    # another word of the document's Calibri: its own font again, no warning
    second = doc.replace_text_run(0, find_run(text, "courier"), "courrier", fonts=fonts)
    assert second.plan is not None and second.plan.kind.value == "reuse"
    assert not second.substituted
    # the substituted word again, with a glyph our subset lacks: our font is extended
    third = doc.replace_text_run(0, find_run(doc.page_text(0), "Zidane"), "Zidanes", fonts=fonts)
    assert not third.substituted and third.font_family == "Calibri"
    with doc.lock:
        bases = [str(f[3]) for f in doc.fitz[0].get_fonts(full=True)]
    assert bases.count("PDFEDT+Calibri-PDFEditor") == 1
    assert "Zidanes" in _norm(doc.page_text(0).text)
    doc.close()


def test_own_font_names_map_back_to_the_family() -> None:
    from pdfeditor.core import fontembed
    from pdfeditor.core.fontread import split_base_font

    assert fontembed.base_font_name("Calibri-Bold") == "PDFEDT+Calibri-Bold-PDFEditor"
    assert split_base_font("PDFEDT+Calibri-Bold-PDFEditor") == ("Calibri", "Bold")
    assert split_base_font("Calibri-PDFEditor") == ("Calibri", "")
    assert fontembed.strip_own_suffix("ArialMT-PDFEditor") == "ArialMT"
    assert fontembed.strip_own_suffix("ArialMT") == "ArialMT"  # fonts embedded before


# -- 1. codes borrowed from the installed cmap get a ToUnicode entry -----------------------
TU_TEXT = "été"


def _tounicode_limited(tmp_path: Path, *, tounicode: str = "used") -> Path:
    """A Calibri Type0 subset showing :data:`TU_TEXT` whose ToUnicode maps only the codes
    used (Word-like): "e" is in the subset (a component of "é") but has no entry.
    ``tounicode="none"`` drops the key, ``"name"`` makes it a name (cannot be extended)."""
    from pdfeditor.core import fontembed

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(
        (72, 100), TU_TEXT, fontsize=11, fontname="Calibri", fontfile=str(CALIBRI_PATH)
    )
    doc.subset_fonts()
    xref = int(page.get_fonts(full=True)[0][0])
    tu = int(doc.xref_get_key(xref, "ToUnicode")[1].split()[0])
    hexs = re.search(rb"\[<([0-9A-Fa-f]+)>\]\s*TJ", page.read_contents()).group(1)  # type: ignore[union-attr]
    cids = [int(hexs[i : i + 4], 16) for i in range(0, len(hexs), 4)]
    doc.update_stream(
        tu, fontembed.tounicode_cmap(dict(zip(cids, map(ord, TU_TEXT), strict=True))), compress=True
    )
    if tounicode == "none":
        doc.xref_set_key(xref, "ToUnicode", "null")
    elif tounicode == "name":
        doc.xref_set_key(xref, "ToUnicode", "/Identity-H")
    del page
    out = tmp_path / f"tu_{tounicode}.pdf"
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    return out


def _search(doc: PdfDocument, needle: str) -> bool:
    with doc.lock:
        page = doc.fitz[0]
        found = bool(page.search_for(needle))
        del page
    return found


def test_borrowed_codes_are_searchable_after_undo_redo_and_save(
    tmp_path: Path, fonts: SystemFonts, qapp
) -> None:
    path = _tounicode_limited(tmp_path)
    doc = PdfDocument.open(path)
    run = find_run(doc.page_text(0), TU_TEXT)
    stack = QUndoStack()
    cmd = ReplaceTextCommand(doc, 0, run, "ete", fonts=fonts)
    cmd.apply_now()
    stack.push(cmd)
    assert cmd.result is not None and cmd.result.plan is not None
    assert cmd.result.plan.kind.value == "reuse"  # the document's own font, borrowed code
    assert _norm(doc.page_text(0).text).strip() == "ete"
    assert _search(doc, "ete")
    stack.undo()
    assert _norm(doc.page_text(0).text).strip() == TU_TEXT
    stack.redo()
    assert cmd.error is None and _search(doc, "ete")
    doc.save()
    doc.close()
    reopened = pymupdf.open(path)
    assert reopened[0].get_text().strip() == "ete"
    assert reopened[0].search_for("ete")
    reopened.close()
    # a re-edit of the reopened file reuses the codes it now maps
    doc = PdfDocument.open(path)
    again = doc.replace_text_run(0, find_run(doc.page_text(0), "ete"), "tee", fonts=fonts)
    assert again.plan is not None and again.plan.kind.value == "reuse"
    assert _search(doc, "tee")
    doc.close()


def test_borrowed_codes_without_a_tounicode(tmp_path: Path, fonts: SystemFonts, qapp) -> None:
    doc = PdfDocument.open(_tounicode_limited(tmp_path, tounicode="none"))
    run = find_run(doc.page_text(0), doc.page_text(0).text.strip()[:3])
    doc.replace_text_run(0, run, "ete", fonts=fonts)
    assert _search(doc, "ete")
    doc.close()


def test_unextendable_tounicode_falls_back_to_an_installed_font(
    tmp_path: Path, fonts: SystemFonts, qapp
) -> None:
    doc = PdfDocument.open(_tounicode_limited(tmp_path, tounicode="name"))
    run = Run(0, 2)
    result = doc.replace_text_run(0, run, "ete", fonts=fonts)
    assert result.plan is not None and result.plan.kind.value == "system"
    assert _search(doc, "ete")
    doc.close()


# -- 5. an overflowing edit can be edited again ----------------------------------------------
def _pix(doc: PdfDocument) -> pymupdf.Pixmap:
    with doc.lock:
        page = doc.fitz[0]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
        del page
    return pix


def test_overflowing_word_can_be_edited_again(tmp_path: Path, fonts: SystemFonts, qapp) -> None:
    path = make_text_edit_pdf(tmp_path / "w.pdf")
    direct = PdfDocument.open(path)
    direct.replace_text_run(0, find_run(direct.page_text(0), "Jean"), "Paul", fonts=fonts)
    expected = _pix(direct)
    direct.close()

    doc = PdfDocument.open(path)
    first = doc.replace_text_run(0, find_run(doc.page_text(0), "Jean"), "Pierre", fonts=fonts)
    assert first.overflow  # its last glyphs overlap the next word's space and "D"
    stack = QUndoStack()
    cmd = ReplaceTextCommand(doc, 0, find_run(doc.page_text(0), "Pierre"), "Paul", fonts=fonts)
    cmd.apply_now()
    stack.push(cmd)
    result = cmd.result
    assert result is not None and result.redrawn > 0 and not result.extended
    flat = _norm(doc.page_text(0).text)
    assert "Paul" in flat and "Pierre" not in flat and "Dupont" in flat
    # the neighbours drawn again are where they were: the page looks like Jean -> Paul
    assert _pix(doc).samples == expected.samples
    with doc.lock:
        page = doc.fitz[0]
        assert page.search_for("Dupont") and page.search_for("Paul")
        del page
    stack.undo()
    assert "Pierre" in _norm(doc.page_text(0).text)
    stack.redo()
    assert cmd.error is None and "Paul" in _norm(doc.page_text(0).text)
    doc.close()


# -- 11. control and format characters are refused ------------------------------------------
@pytest.mark.parametrize("bad", ["Pa\x00ul", "a\x07b", "​", "﻿", "Pa ul", "\tx"])
def test_control_and_format_characters_refused(
    tmp_path: Path, fonts: SystemFonts, qapp, bad: str
) -> None:
    from pdfeditor.core.textedit import EditReason, TextEditError

    doc = PdfDocument.open(make_text_edit_pdf(tmp_path / "w.pdf"))
    with doc.lock:
        before = doc.fitz[0].read_contents()
    with pytest.raises((TextEditError, ValueError)) as info:
        doc.replace_text_run(0, find_run(doc.page_text(0), "Jean"), bad, fonts=fonts)
    if isinstance(info.value, TextEditError):
        assert info.value.reason is EditReason.INVALID_TEXT
    with doc.lock:
        assert doc.fitz[0].read_contents() == before
    doc.close()


def test_stale_expect_text(tmp_path: Path, fonts: SystemFonts, qapp) -> None:
    from pdfeditor.core.textedit import EditReason, TextEditError

    doc = PdfDocument.open(make_text_edit_pdf(tmp_path / "w.pdf"))
    run = find_run(doc.page_text(0), "Jean")
    with pytest.raises(TextEditError) as info:
        doc.replace_text_run(0, run, "Paul", fonts=fonts, expect_text="Jeanne")
    assert info.value.reason is EditReason.STALE
    doc.replace_text_run(0, run, "Paul", fonts=fonts, expect_text="Jean")
    assert "Paul" in _norm(doc.page_text(0).text)
    doc.close()
