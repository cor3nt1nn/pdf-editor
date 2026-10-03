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
