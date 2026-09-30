"""M3 review fixes in the annotation core: /CL, callout and locked FreeText, \\r\\n
contents, fitted height capped at the page, lazy /NM (reading never writes)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pymupdf
import pytest
from fixtures import A4, ANNOT_TEXT_NAME, FOREIGN_TEXT
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QUndoStack

from pdfeditor.core import annotations
from pdfeditor.core.annotations import AnnotKind, AnnotSpec, stamp_rect
from pdfeditor.core.commands import AddAnnotCommand, DeleteAnnotCommand, EditAnnotCommand
from pdfeditor.core.document import AnnotError, PdfDocument

BLACK = (0.0, 0.0, 0.0)


def _cl(doc: PdfDocument, name: str, page: int = 0) -> str:
    with doc.lock:
        return doc.fitz.xref_get_key(doc.annot(page, name).xref, "CL")[0]


@pytest.fixture
def blank(tmp_path):
    pdf = pymupdf.open()
    pdf.new_page(width=A4[0], height=A4[1])
    path = tmp_path / "blank.pdf"
    pdf.save(path)
    pdf.close()
    d = PdfDocument.open(path)
    yield d
    d.close()


@pytest.fixture
def ann(annotated_pdf):
    d = PdfDocument.open(annotated_pdf)
    yield d
    d.close()


# -- finding 2: /CL ---------------------------------------------------------------------------
def test_callout_line_never_written(blank: PdfDocument) -> None:
    spec = AnnotSpec(0, AnnotKind.TEXT, "abc", 11, BLACK, QRectF(100, 100, 180, 30))
    text = blank.add_annot(spec, fit_height=True)
    assert _cl(blank, text.name) == "null"
    for change in (
        {"rect": QRectF(120, 140, 180, 16)},  # move
        {"rect": QRectF(120, 140, 90, 40)},  # resize
        {"text": "longer text that wraps onto several lines", "fit_height": True},
        {"color": (1.0, 0.0, 0.0)},
    ):
        blank.update_annot(0, text.name, **change)
        assert _cl(blank, text.name) == "null", change
    rect, fs = stamp_rect(QPointF(300, 300), 12, "4")
    stamp = blank.add_annot(AnnotSpec(0, AnnotKind.STAMP, "4", fs, BLACK, rect))
    blank.update_annot(0, stamp.name, rect=QRectF(300, 300, 20, 20))
    assert _cl(blank, stamp.name) == "null"


def test_commands_do_not_write_callout_lines(blank: PdfDocument) -> None:
    stack = QUndoStack()
    add = AddAnnotCommand(
        blank, AnnotSpec(0, AnnotKind.TEXT, "x", 11, BLACK, QRectF(50, 50, 99, 9))
    )
    stack.push(add)
    info = blank.annot(0, add.name)
    stack.push(EditAnnotCommand(blank, info, rect=info.rect.translated(10, 10)))
    assert _cl(blank, add.name) == "null"
    stack.undo()
    assert _cl(blank, add.name) == "null"


# -- finding 3: callout and locked FreeText ---------------------------------------------------
def _special_pdf(path: Path) -> Path:
    d = pymupdf.open()
    d.new_page(width=A4[0], height=A4[1])
    pref = d.page_xref(0)
    objs = []
    for body in (
        "/IT/FreeTextCallout/Rect[300 600 500 640]/CL[100 500 200 550 300 620]/LE/OpenArrow"
        "/F 4/Contents(callout)/NM(callout)",
        "/Rect[300 300 500 340]/F 132/Contents(locked)/NM(locked)",
        "/Rect[300 200 500 240]/F 68/Contents(readonly)/NM(readonly)",
        "/Rect[300 100 500 140]/F 516/Contents(fixed text)/NM(contents)",
        "/Rect[50 100 250 140]/F 4/Contents(a\r\nb\rc)/NM(crlf)",
    ):
        xref = d.get_new_xref()
        d.update_object(
            xref,
            f"<</Type/Annot/Subtype/FreeText/P {pref} 0 R/DA(0 g /Helv 12 Tf){body}>>",
        )
        objs.append(f"{xref} 0 R")
    d.xref_set_key(pref, "Annots", "[" + " ".join(objs) + "]")
    d.save(path)
    d.close()
    return path


@pytest.fixture
def special(tmp_path):
    d = PdfDocument.open(_special_pdf(tmp_path / "special.pdf"))
    yield d
    d.close()


@pytest.mark.parametrize("name", ["callout", "locked", "readonly"])
def test_callout_and_locked_are_not_editable(special: PdfDocument, name: str) -> None:
    info = special.annot(0, name)
    assert info is not None and info.locked and not info.editable
    with pytest.raises(AnnotError):
        special.update_annot(0, name, rect=info.rect.translated(10, 0))
    with pytest.raises(AnnotError):
        special.delete_annot(0, name)
    with pytest.raises(AnnotError):
        EditAnnotCommand(special, info, rect=info.rect.translated(10, 0)).apply_now()
    with pytest.raises(AnnotError):
        DeleteAnnotCommand(special, info).apply_now()
    assert special.annot(0, name) == info  # untouched (callout keeps /CL and /IT)
    if name == "callout":
        with special.lock:
            assert special.fitz.xref_get_key(info.xref, "CL")[0] == "array"


def test_locked_contents_can_move_but_not_edit_text(special: PdfDocument) -> None:
    info = special.annot(0, "contents")
    assert info.editable and info.locked_contents and not info.text_editable
    moved = special.update_annot(0, "contents", rect=info.rect.translated(0, 20))
    assert moved.rect.top() == pytest.approx(info.rect.top() + 20)


# -- finding 14: \r\n ------------------------------------------------------------------------
def test_contents_line_breaks_normalised(special: PdfDocument) -> None:
    assert special.annot(0, "crlf").text == "a\nb\nc"


# -- finding 12: fitted height capped at the page ---------------------------------------------
def test_fit_height_capped_at_page_bottom(blank: PdfDocument) -> None:
    text = "\n".join(f"line {i}" for i in range(80))  # ~ 80 x 13.2 pt > the page
    spec = AnnotSpec(0, AnnotKind.TEXT, text, 11, BLACK, QRectF(100, 700, 200, 20))
    info = blank.add_annot(spec, fit_height=True)
    assert info.rect.top() == pytest.approx(700, abs=0.01)
    assert info.rect.bottom() <= A4[1] + 0.01
    edited = blank.update_annot(0, info.name, text=text + "\nmore", fit_height=True)
    assert edited.rect.bottom() <= A4[1] + 0.01


def test_fit_height_capped_on_rotated_page(tmp_path) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=A4[0], height=A4[1])
    page.set_rotation(90)
    pdf.save(tmp_path / "rot.pdf")
    pdf.close()
    doc = PdfDocument.open(tmp_path / "rot.pdf")
    size = doc.page_size(0)
    text = "\n".join(f"line {i}" for i in range(60))
    spec = AnnotSpec(0, AnnotKind.TEXT, text, 11, BLACK, QRectF(100, size.height() - 60, 200, 20))
    info = doc.add_annot(spec, fit_height=True)
    assert info.rect.bottom() <= size.height() + 0.01
    assert info.rect.top() == pytest.approx(size.height() - 60, abs=0.01)
    doc.close()


# -- finding 15: lazy /NM ------------------------------------------------------------------
def test_reading_hovering_and_saving_a_clean_document_changes_no_byte(
    annotated_pdf: Path,
) -> None:
    original = annotated_pdf.read_bytes()
    doc = PdfDocument.open(annotated_pdf)
    for i in range(doc.page_count):
        foreign = [a for a in doc.annots(i) if annotations.is_synthetic(a.name)]
        doc.page_shapes(i)
        doc.render(i, 1.0)
        for a in foreign:
            assert doc.annot(i, a.name) == a
    assert any(annotations.is_synthetic(a.name) for a in doc.annots(0))
    doc.save()
    assert annotated_pdf.read_bytes() == original
    doc.close()


def test_owner_locked_document_is_never_written(owner_locked_pdf: Path) -> None:
    original = owner_locked_pdf.read_bytes()
    doc = PdfDocument.open(owner_locked_pdf)
    assert not doc.can_annotate
    for i in range(doc.page_count):
        doc.annots(i)
        doc.page_shapes(i)
    with pytest.raises(AnnotError):
        doc.claim_annot_name(0, annotations.synthetic_name(1, "0"))
    doc.save()
    assert owner_locked_pdf.read_bytes() == original
    doc.close()


def test_editing_a_foreign_freetext_claims_a_name_across_full_saves(ann: PdfDocument) -> None:
    stack = QUndoStack()
    foreign = next(a for a in ann.annots(0) if annotations.is_synthetic(a.name))
    edit = EditAnnotCommand(ann, foreign, text="Edited once")
    edit.apply_now()
    stack.push(edit)
    real = edit.name
    uuid.UUID(real)
    assert ann.annot(0, real).text == "Edited once"
    ann.save(force_full=True)  # renumbers xrefs
    stack.undo()
    assert ann.annot(0, real).text == FOREIGN_TEXT
    ann.save(force_full=True)
    stack.redo()
    assert ann.annot(0, real).text == "Edited once"
    ann.save()
    reopened = PdfDocument.open(ann.path)
    try:
        assert reopened.annot(0, real).text == "Edited once"
    finally:
        reopened.close()


def test_deleting_a_foreign_freetext_then_undo_across_saves(ann: PdfDocument) -> None:
    stack = QUndoStack()
    foreign = next(a for a in ann.annots(0) if annotations.is_synthetic(a.name))
    delete = DeleteAnnotCommand(ann, foreign)
    delete.apply_now()
    stack.push(delete)
    real = delete.name
    assert not annotations.is_synthetic(real)
    assert all(a.text != FOREIGN_TEXT for a in ann.annots(0))
    ann.save(force_full=True)
    stack.undo()
    restored = ann.annot(0, real)
    assert restored is not None and restored.text == FOREIGN_TEXT
    ann.save(force_full=True)
    stack.redo()
    assert ann.annot(0, real) is None
    stack.undo()
    assert ann.annot(0, real) is not None


def test_synthetic_name_is_stale_after_reload(ann: PdfDocument) -> None:
    foreign = next(a for a in ann.annots(0) if annotations.is_synthetic(a.name))
    ann.save()  # incremental: reloaded
    assert ann.annot(0, foreign.name) is None
    fresh = next(a for a in ann.annots(0) if annotations.is_synthetic(a.name))
    assert fresh.name != foreign.name and fresh.text == FOREIGN_TEXT
    assert ann.annot(0, ANNOT_TEXT_NAME) is not None
    with pytest.raises(AnnotError):
        DeleteAnnotCommand(ann, foreign).apply_now()
