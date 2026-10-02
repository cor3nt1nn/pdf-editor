"""M6a review and fuzzer findings: core fixes (page commands, form hygiene, XFA)."""

from __future__ import annotations

import shutil

import fixtures
import pymupdf
import pytest
from PySide6.QtCore import QSizeF
from PySide6.QtGui import QUndoStack

from pdfeditor.core import pages
from pdfeditor.core.annotations import is_synthetic
from pdfeditor.core.commands import (
    DeleteAnnotCommand,
    DeletePagesCommand,
    EditAnnotCommand,
    InsertBlankPageCommand,
    MovePagesCommand,
    RotatePagesCommand,
)
from pdfeditor.core.document import PageError, PdfDocument
from pdfeditor.core.forms import XfaKind, detect_xfa
from pdfeditor.core.snapshots import SnapshotStore


@pytest.fixture
def simple(simple_pdf):
    d = PdfDocument.open(simple_pdf)
    yield d
    d.close()


# -- M2: a failing undo/redo never raises out of the command --------------------------
def test_failed_undo_is_recorded_not_raised(simple, tmp_path) -> None:
    simple.snapshots = SnapshotStore(memory_limit=0, directory=tmp_path)
    stack = QUndoStack()
    delete = DeletePagesCommand(simple, [0])
    delete.apply_now()
    stack.push(delete)
    rotate = RotatePagesCommand(simple, [0], 90)
    rotate.apply_now()
    stack.push(rotate)
    shutil.rmtree(simple.snapshots.spill_directory)  # a temp cleaner removed the copy
    stack.undo()
    assert rotate.error is None
    stack.undo()  # must not raise
    assert isinstance(delete.error, PageError)
    assert delete.error.reason == "snapshot"
    assert simple.page_count == 2  # nothing restored


def test_error_reset_by_next_success(simple) -> None:
    rotate = RotatePagesCommand(simple, [0], 90)
    rotate.apply_now()
    rotate.error = RuntimeError("stale")
    rotate.undo()
    assert rotate.error is None
    assert simple.page_rotation(0) == 0


# -- M3: every redo of a deletion takes a fresh undo copy -----------------------------
def test_redo_delete_keeps_lazily_claimed_name(annotated_pdf) -> None:
    """r1: the foreign annotation gets its /NM after the deletion's first copy; undo
    then redo of the deletion must not lose it (the later edit's redo looks it up)."""
    doc = PdfDocument.open(annotated_pdf)
    try:
        stack = QUndoStack()
        foreign = next(a for a in doc.annots(0) if a.text == fixtures.FOREIGN_TEXT)
        assert is_synthetic(foreign.name)
        delete = DeletePagesCommand(doc, [1])
        delete.apply_now()
        stack.push(delete)
        edit = EditAnnotCommand(doc, foreign, text="changed")
        edit.apply_now()
        stack.push(edit)
        stack.undo()
        stack.undo()
        stack.redo()
        stack.redo()
        assert delete.error is None and edit.error is None
        assert any(a.text == "changed" for a in doc.annots(0))
        stack.undo()
        stack.undo()
        assert edit.error is None and delete.error is None
        assert any(a.text == fixtures.FOREIGN_TEXT for a in doc.annots(0))
        for _ in range(2):  # the claimed /NM is written again by every redo of the edit
            stack.redo()
            stack.redo()
            assert delete.error is None and edit.error is None
            assert [a.name for a in doc.annots(0) if a.text == "changed"] == [edit.info.name]
            stack.undo()
            stack.undo()
            assert delete.error is None and edit.error is None
            assert doc.page_count == 2
    finally:
        doc.close()


def test_redo_annot_delete_after_restore(annotated_pdf) -> None:
    doc = PdfDocument.open(annotated_pdf)
    try:
        stack = QUndoStack()
        foreign = next(a for a in doc.annots(0) if a.text == fixtures.FOREIGN_TEXT)
        delete = DeletePagesCommand(doc, [1])
        delete.apply_now()
        stack.push(delete)
        remove = DeleteAnnotCommand(doc, foreign)
        remove.apply_now()
        stack.push(remove)
        stack.undo()
        stack.undo()
        stack.redo()
        stack.redo()
        assert delete.error is None and remove.error is None
        assert not any(a.text == fixtures.FOREIGN_TEXT for a in doc.annots(0))
    finally:
        doc.close()


def test_each_redo_replaces_the_undo_copy(simple) -> None:
    stack = QUndoStack()
    delete = DeletePagesCommand(simple, [0])
    delete.apply_now()
    stack.push(delete)
    first = delete.snapshot_id
    assert len(simple.snapshots) == 1
    stack.undo()
    stack.redo()
    assert delete.error is None
    assert delete.snapshot_id != first
    assert len(simple.snapshots) == 1  # the first copy was discarded
    stack.undo()
    assert delete.error is None
    assert simple.page_count == 3


def test_failed_redo_delete_keeps_the_previous_copy(simple, monkeypatch) -> None:
    stack = QUndoStack()
    delete = DeletePagesCommand(simple, [0])
    delete.apply_now()
    stack.push(delete)
    stack.undo()
    first = delete.snapshot_id

    def boom(*_a, **_k):
        raise PageError("refused")

    monkeypatch.setattr(simple, "delete_pages", boom)
    stack.redo()
    assert isinstance(delete.error, PageError)
    assert delete.snapshot_id == first
    assert len(simple.snapshots) == 1


# -- M1: prune_fields keeps fields that have no widget ---------------------------------
def _hidden_field_doc() -> pymupdf.Document:
    """Three pages, a text field on page 1 and a widget-less calculated field "total"
    (with /V, listed in /CO) and a widget-less parent "group" with a widget-less kid."""
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    widget = pymupdf.Widget()
    widget.field_name = "visible"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.rect = pymupdf.Rect(50, 50, 200, 80)
    doc[0].add_widget(widget)
    hidden = doc.get_new_xref()
    doc.update_object(hidden, "<</FT/Tx/T(total)/V(42)>>")
    group = doc.get_new_xref()
    kid = doc.get_new_xref()
    doc.update_object(group, f"<</T(group)/Kids[{kid} 0 R]>>")
    doc.update_object(kid, f"<</FT/Tx/T(sub)/V(7)/Parent {group} 0 R>>")
    cat = doc.pdf_catalog()
    fields = pages._refs(doc.xref_get_key(cat, "AcroForm/Fields")[1])
    doc.xref_set_key(cat, "AcroForm/Fields", pages._array([*fields, hidden, group]))
    doc.xref_set_key(cat, "AcroForm/CO", f"[{hidden} 0 R]")
    data = doc.tobytes(garbage=0)
    doc.close()
    return pymupdf.open(stream=data, filetype="pdf")


def _field_names(doc: pymupdf.Document) -> list[str]:
    fields = pages._refs(doc.xref_get_key(doc.pdf_catalog(), "AcroForm/Fields")[1])
    return [doc.xref_get_key(x, "T")[1] for x in fields]


def test_prune_keeps_widgetless_fields() -> None:
    doc = _hidden_field_doc()
    try:
        assert _field_names(doc) == ["visible", "total", "group"]
        pages.delete_pages(doc, [2])  # a page without fields
        assert _field_names(doc) == ["visible", "total", "group"]
        pages.delete_pages(doc, [0])  # the page of "visible"
        assert _field_names(doc) == ["total", "group"]
        cat = doc.pdf_catalog()
        hidden = pages._refs(doc.xref_get_key(cat, "AcroForm/Fields")[1])[0]
        assert doc.xref_get_key(hidden, "V") == ("string", "42")
        assert pages._refs(doc.xref_get_key(cat, "AcroForm/CO")[1]) == [hidden]
    finally:
        doc.close()


def test_prune_still_drops_a_field_whose_widgets_are_gone(lo_form_pdf) -> None:
    doc = pymupdf.open(lo_form_pdf)
    try:
        before = len(_field_names(doc))
        pages.delete_pages(doc, [0])
        assert len(_field_names(doc)) < before
    finally:
        doc.close()


# -- B2: a static XFA form stays static whatever pages are deleted -------------------
def test_static_xfa_stays_static_without_fields(static_xfa_pdf, tmp_path) -> None:
    """Fuzzer B2: insert two blank pages, delete the page holding every field, move."""
    doc = PdfDocument.open(static_xfa_pdf)
    try:
        assert doc.xfa_kind is XfaKind.STATIC
        stack = QUndoStack()
        for make in (
            lambda: InsertBlankPageCommand(doc, 1, QSizeF(300, 300)),
            lambda: InsertBlankPageCommand(doc, 2, QSizeF(300, 300)),
            lambda: DeletePagesCommand(doc, [0]),
            lambda: MovePagesCommand(doc, [0], 2),
        ):
            cmd = make()
            cmd.apply_now()
            stack.push(cmd)
            assert doc.xfa_kind is XfaKind.STATIC
        assert not doc.is_form
        out = tmp_path / "x.pdf"
        doc.save_as(out)  # a page-edited static XFA form loses its /XFA (Deviation 73)
    finally:
        doc.close()


def test_saved_restructured_static_xfa_has_no_xfa(qapp, static_xfa_pdf, tmp_path) -> None:
    from pdfeditor.ui.document_view import DocumentView

    view = DocumentView()
    try:
        view._replace(PdfDocument.open(static_xfa_pdf))
        doc = view.document
        cmd = InsertBlankPageCommand(doc, 1, QSizeF(300, 300))
        cmd.apply_now()
        view.push(cmd)
        cmd = DeletePagesCommand(doc, [0])
        cmd.apply_now()
        view.push(cmd)
        out = tmp_path / "saved.pdf"
        view.save_as(str(out))
        assert doc.xfa_kind is XfaKind.NONE
        with pymupdf.open(out) as saved:
            assert detect_xfa(saved) is XfaKind.NONE
    finally:
        view.close_document()
        view.deleteLater()


def test_export_and_extract_drop_xfa_after_page_ops(static_xfa_pdf, tmp_path) -> None:
    doc = PdfDocument.open(static_xfa_pdf)
    try:
        cmd = InsertBlankPageCommand(doc, 1, QSizeF(300, 300))
        cmd.apply_now()
        doc.export_copy(tmp_path / "copy.pdf")
        doc.extract_pages([0], tmp_path / "one.pdf")
        for name in ("copy.pdf", "one.pdf"):
            with pymupdf.open(tmp_path / name) as out:
                assert detect_xfa(out) is XfaKind.NONE, name
    finally:
        doc.close()
