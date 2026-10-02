"""M7-T5: ReplaceTextCommand (undo/redo of a page text edit, core/commands.py)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QCoreApplication, QObject, QSizeF
from PySide6.QtGui import QUndoStack
from textedit_fixtures import (
    ARIAL_PATH,
    CALIBRI_PATH,
    TIMES_PATH,
    make_ocr_pdf,
    make_text_edit_pdf,
    needs_text_fonts,
)

import pdfeditor.i18n as i18n
from pdfeditor.core import orphans
from pdfeditor.core.commands import (
    InsertBlankPageCommand,
    MovePagesCommand,
    ReplaceTextCommand,
)
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.fontmatch import SystemFonts
from pdfeditor.core.pagetext import PageText
from pdfeditor.core.textedit import EditReason, Run, TextEditError

pytestmark = needs_text_fonts

STALE_EN = "The page changed since this edit; it cannot be undone."


@pytest.fixture(scope="module")
def fonts() -> SystemFonts:
    return SystemFonts.from_paths([CALIBRI_PATH, ARIAL_PATH, TIMES_PATH])


@pytest.fixture
def french(qapp):
    i18n.install_translators(qapp, "fr")
    yield
    i18n.remove_translators(qapp)


def _norm(s: str) -> str:
    return s.replace("\xa0", " ")


def find_run(text: PageText, word: str) -> Run:
    flat = _norm("".join(ch.c for ch in text.chars))
    start = flat.index(word)
    return Run(start, start + len(word) - 1)


def render(doc: PdfDocument, page: int = 0) -> bytes:
    with doc.lock:
        return doc.fitz[page].get_pixmap(matrix=pymupdf.Matrix(2, 2)).samples


def edit(
    doc: PdfDocument, page: int, word: str, new: str, fonts: SystemFonts
) -> ReplaceTextCommand:
    return ReplaceTextCommand(doc, page, find_run(doc.page_text(page), word), new, fonts=fonts)


def page_text(doc: PdfDocument, page: int = 0) -> str:
    return _norm(doc.page_text(page).text)


def redact_objects(doc: PdfDocument) -> list[int]:
    found = []
    with doc.lock:
        f = doc.fitz
        for x in range(1, f.xref_length()):
            try:
                source = f.xref_object(x, compressed=True)
            except RuntimeError:  # freed object
                continue
            if "/Redact" in source:
                found.append(x)
    return found


def test_apply_undo_redo_pixel_identical(tmp_path: Path, fonts: SystemFonts, qtbot) -> None:
    doc = PdfDocument.open(make_text_edit_pdf(tmp_path / "w.pdf"))
    pix0 = render(doc)
    stack = QUndoStack()
    cmd = edit(doc, 0, "Jean", "Pierre", fonts)
    assert cmd.text() == "Edit page text" and cmd.result is None
    with qtbot.waitSignals([doc.page_changed]):
        cmd.apply_now()
    assert cmd.result is not None and cmd.result.new_text == "Pierre"
    assert cmd.result.old_text == "Jean" and cmd.page == 0
    stack.push(cmd)  # skipped redo: applied once
    assert cmd.error is None and stack.count() == 1
    pix1 = render(doc)
    assert pix1 != pix0 and "Pierre" in page_text(doc)
    emitted: list[int] = []
    doc.page_changed.connect(emitted.append)
    for _ in range(2):
        stack.undo()
        assert cmd.error is None and render(doc) == pix0
        assert "Jean Dupont" in page_text(doc)
        stack.redo()
        assert cmd.error is None and render(doc) == pix1
    assert emitted == [0, 0, 0, 0]  # one page_changed per step
    doc.close()


@pytest.mark.parametrize("how", ["save", "save_as"])
def test_undo_after_save(tmp_path: Path, fonts: SystemFonts, how: str) -> None:
    path = make_text_edit_pdf(tmp_path / "w.pdf")
    doc = PdfDocument.open(path)
    pix0 = render(doc)
    stack = QUndoStack()
    cmd = edit(doc, 0, "Dupont", "Zidane", fonts)  # fallback font embedded
    cmd.apply_now()
    stack.push(cmd)
    pix1 = render(doc)
    assert not doc.can_save_incrementally()  # MuPDF's redaction flag
    if how == "save":
        doc.save()
    else:
        doc.save_as(tmp_path / "copy.pdf")
    assert render(doc) == pix1
    stack.undo()
    assert cmd.error is None and render(doc) == pix0
    stack.redo()
    assert cmd.error is None and render(doc) == pix1
    stack.undo()
    doc.save()  # incremental again after the reload
    reopened = PdfDocument.open(doc.path)
    assert render(reopened) == pix0
    reopened.close()
    doc.close()


def test_stale_page_records_error_and_changes_nothing(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = PdfDocument.open(make_text_edit_pdf(tmp_path / "w.pdf"))
    stack = QUndoStack()
    cmd = edit(doc, 0, "Jean", "Paul", fonts)
    cmd.apply_now()
    stack.push(cmd)
    # something else changes the page content
    doc.replace_text_run(0, find_run(doc.page_text(0), "courier"), "courrier", fonts=fonts)
    pix = render(doc)
    stack.undo()
    assert isinstance(cmd.error, TextEditError)
    assert cmd.error.reason is EditReason.STALE and str(cmd.error) == STALE_EN
    assert render(doc) == pix
    stack.redo()
    assert isinstance(cmd.error, TextEditError) and cmd.error.reason is EditReason.STALE
    assert render(doc) == pix
    doc.close()


def test_apply_now_failure_leaves_the_stack_untouched(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = PdfDocument.open(make_ocr_pdf(tmp_path / "ocr.pdf"))
    pix = render(doc)
    stack = QUndoStack()
    cmd = edit(doc, 0, "reconnu", "lu", fonts)
    with pytest.raises(TextEditError) as info:
        cmd.apply_now()
    assert info.value.reason is EditReason.INVISIBLE
    assert stack.count() == 0 and cmd.result is None and render(doc) == pix
    doc.close()


def test_french_text_and_stale_message(tmp_path: Path, fonts: SystemFonts, french) -> None:
    doc = PdfDocument.open(make_text_edit_pdf(tmp_path / "w.pdf"))
    cmd = edit(doc, 0, "Jean", "Paul", fonts)
    assert cmd.text() == "la modification du texte de la page"
    owner = QObject()
    stack = QUndoStack()
    undo = stack.createUndoAction(owner, QCoreApplication.translate("MainWindow", "Undo"))
    cmd.apply_now()
    stack.push(cmd)
    assert undo.text() == "Annuler la modification du texte de la page"
    doc.replace_text_run(0, find_run(doc.page_text(0), "Lyon"), "Nice", fonts=fonts)
    stack.undo()
    assert str(cmd.error) == (
        "La page a changé depuis cette modification ; elle ne peut pas être annulée."
    )
    doc.close()


def test_redaction_annot_freed_before_an_incremental_write(
    tmp_path: Path, fonts: SystemFonts
) -> None:
    doc = PdfDocument.open(make_text_edit_pdf(tmp_path / "w.pdf"))
    stack = QUndoStack()
    cmd = edit(doc, 0, "Jean", "Paul", fonts)
    cmd.apply_now()
    stack.push(cmd)
    pix1 = render(doc)
    leftovers = redact_objects(doc)
    assert leftovers  # apply_redactions leaves the unreferenced annotation behind
    with doc.lock:
        dead = orphans.session_orphans(doc.fitz, doc._first_new_xref)
        assert set(leftovers) <= set(dead)
        assert doc._drop_session_orphans()
    assert redact_objects(doc) == [] and render(doc) == pix1
    stack.undo()
    stack.redo()
    assert cmd.error is None and render(doc) == pix1
    doc.save()
    assert b"/Redact" not in Path(doc.path).read_bytes()
    doc.close()


def test_page_id_across_a_page_move(tmp_path: Path, fonts: SystemFonts) -> None:
    doc = PdfDocument.open(make_text_edit_pdf(tmp_path / "w.pdf"))
    stack = QUndoStack()
    stack.push(InsertBlankPageCommand(doc, 0, QSizeF(300, 300)))
    blank = render(doc, 0)
    pix0 = render(doc, 1)
    cmd = edit(doc, 1, "Jean", "Paul", fonts)
    cmd.apply_now()
    stack.push(cmd)
    pix1 = render(doc, 1)
    stack.push(MovePagesCommand(doc, [1], 0))
    assert cmd.page == 0 and render(doc, 0) == pix1
    # undone/redone by page id while the page sits at another index
    cmd.undo()
    assert cmd.error is None and render(doc, 0) == pix0 and render(doc, 1) == blank
    cmd.redo()
    assert cmd.error is None and render(doc, 0) == pix1
    stack.undo()  # move
    stack.undo()  # edit
    assert cmd.error is None and render(doc, 1) == pix0 and render(doc, 0) == blank
    stack.redo()
    stack.redo()
    assert render(doc, 0) == pix1 and render(doc, 1) == blank
    # a page that is gone: error recorded, nothing raised
    doc.delete_pages([0])
    cmd.undo()
    assert isinstance(cmd.error, TextEditError)
    doc.close()
