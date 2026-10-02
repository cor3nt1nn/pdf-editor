"""M6a review and fuzzer findings: core fixes (page commands, form hygiene, XFA)."""

from __future__ import annotations

import re
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
    InsertPagesCommand,
    MovePagesCommand,
    RotatePagesCommand,
    SetFieldValueCommand,
)
from pdfeditor.core.document import PageError, PdfDocument
from pdfeditor.core.forms import FieldKind, XfaKind, detect_xfa
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


# -- m2: no empty /CO left behind by an insertion -------------------------------------
def _norm(data: bytes) -> bytes:
    return re.sub(rb"/ID\s*\[[^\]]*\]", b"/ID[]", data)


def test_form_into_form_insert_undo_is_exact(lo_form_pdf, tmp_path) -> None:
    """r3: insert a form page into a form without /CO, undo: no ``/CO []`` remains."""
    src_path = fixtures.make_lo_form_pdf(tmp_path / "src.pdf")
    doc = PdfDocument.open(lo_form_pdf)
    try:
        with doc.lock:
            ref = _norm(doc.fitz.tobytes(garbage=3, deflate=True))
        assert not doc.has_calc_order
        with pymupdf.open(src_path) as src:
            data = pages.subdocument_bytes(src, [0])
        cmd = InsertPagesCommand(doc, data, 1, 1)
        cmd.apply_now()
        assert doc.last_insert_renamed_fields
        assert not doc.has_calc_order
        cmd.undo()
        assert cmd.error is None
        assert not doc.has_calc_order
        with doc.lock:
            assert _norm(doc.fitz.tobytes(garbage=3, deflate=True)) == ref
    finally:
        doc.close()


def test_undo_insert_drops_brought_calc_order(lo_form_pdf) -> None:
    """Inserted calculated fields bring a /CO into a form without one; undo removes it."""
    src = _hidden_field_doc()
    try:
        cat = src.pdf_catalog()
        visible = pages._refs(src.xref_get_key(cat, "AcroForm/Fields")[1])[0]
        src.xref_set_key(cat, "AcroForm/CO", f"[{visible} 0 R]")
        data = pages.subdocument_bytes(src, [0])
    finally:
        src.close()
    doc = PdfDocument.open(lo_form_pdf)
    try:
        cmd = InsertPagesCommand(doc, data, 0, 1)
        cmd.apply_now()
        cmd.undo()
        assert cmd.error is None
        assert not doc.has_calc_order
    finally:
        doc.close()


def test_prune_drops_calc_order_entries_of_removed_fields() -> None:
    doc = _hidden_field_doc()
    try:
        cat = doc.pdf_catalog()
        fields = pages._refs(doc.xref_get_key(cat, "AcroForm/Fields")[1])
        visible, hidden = fields[0], fields[1]
        doc.xref_set_key(cat, "AcroForm/CO", f"[{visible} 0 R {hidden} 0 R]")
        pages.delete_pages(doc, [0])
        assert pages._refs(doc.xref_get_key(cat, "AcroForm/CO")[1]) == [hidden]
    finally:
        doc.close()


# -- B1: inserted fields keep stable names across undo/redo ---------------------------
def test_redo_insert_keeps_renamed_field_names(lo_form_pdf, tmp_path) -> None:
    """Fuzzer B1: a value set on a renamed inserted field survives undo/redo of both."""
    with pymupdf.open(fixtures.make_lo_form_pdf(tmp_path / "src.pdf")) as src:
        data = pages.subdocument_bytes(src, [0])
    doc = PdfDocument.open(lo_form_pdf)
    try:
        stack = QUndoStack()
        insert = InsertPagesCommand(doc, data, 2, 1)
        insert.apply_now()
        stack.push(insert)
        renamed = [
            w for w in doc.widgets(2) if w.kind is FieldKind.TEXT and w.name.endswith(" (2)")
        ]
        assert renamed, [w.name for w in doc.widgets(2)]
        info = renamed[0]
        names = sorted(w.name for w in doc.widgets(2))
        edit = SetFieldValueCommand(doc, info, "Rossi")
        edit.apply_now()
        stack.push(edit)
        for _ in range(3):
            stack.undo()
            stack.undo()
            assert insert.error is None and edit.error is None
            assert doc.page_count == 2
            stack.redo()
            stack.redo()
            assert insert.error is None and edit.error is None
            assert sorted(w.name for w in doc.widgets(2)) == names
            assert next(w for w in doc.widgets(2) if w.name == info.name).value == "Rossi"
    finally:
        doc.close()


def test_renamed_fields_are_unique_and_numbered(lo_form_pdf, tmp_path) -> None:
    with pymupdf.open(fixtures.make_lo_form_pdf(tmp_path / "src.pdf")) as src:
        data = pages.subdocument_bytes(src, [0])
    doc = PdfDocument.open(lo_form_pdf)
    try:
        doc.insert_pages(data, 2)
        first = dict(doc.last_insert_field_renames)
        doc.insert_pages(data, 3)
        second = dict(doc.last_insert_field_renames)
        assert first and second
        assert all(new.endswith(" (2)") for new in first.values())
        assert all(new.endswith(" (3)") for new in second.values())
        with doc.lock:
            names = pages.field_names(doc.fitz)
            widgets = [w for i in range(doc.page_count) for w in doc.fitz[i].widgets()]
        assert len({w.field_name for w in widgets}) == len(names)
        assert not any("[" in n for n in names)  # MuPDF never had to rename
    finally:
        doc.close()


def test_field_names_reads_the_parent_chain(lo_form_pdf) -> None:
    with pymupdf.open(lo_form_pdf) as doc:
        expected = {w.field_name for page in doc for w in page.widgets()}
        assert pages.field_names(doc) == expected


# -- m3: page operations on large forms -------------------------------------------------
#: Insert (and undo) of a form page into a 2,000-field form (was ~0.5 s, field names
#: read through ``page.widgets()`` and a quadratic prune).
LARGE_FORM_BUDGET_S = 0.25


def test_insert_into_large_form_is_fast(tmp_path, lo_form_pdf) -> None:
    import time

    big = fixtures.make_many_fields_pdf(tmp_path / "big.pdf", 2000)
    with pymupdf.open(lo_form_pdf) as src:
        data = pages.subdocument_bytes(src, [0])
    doc = PdfDocument.open(big)
    try:
        best_insert = best_undo = float("inf")
        for _ in range(3):
            cmd = InsertPagesCommand(doc, data, 0, 1)
            t0 = time.perf_counter()
            cmd.apply_now()
            t1 = time.perf_counter()
            cmd.undo()
            t2 = time.perf_counter()
            assert cmd.error is None
            best_insert = min(best_insert, t1 - t0)
            best_undo = min(best_undo, t2 - t1)
        print(f"insert {best_insert * 1000:.0f} ms, undo {best_undo * 1000:.0f} ms")
        assert best_insert < LARGE_FORM_BUDGET_S
        assert best_undo < LARGE_FORM_BUDGET_S
        with doc.lock:
            assert len(pages.field_names(doc.fitz)) == 2000
    finally:
        doc.close()


# -- fuzzer minor: no dangling radio /V after deleting the selected button's page ----------
def _radio_value(doc: PdfDocument) -> tuple[str, str]:
    with doc.lock:
        field = next(w.field_xref for w in doc.all_widgets())
        return doc.fitz.xref_get_key(field, "V")


def test_deleting_the_selected_radio_button_turns_the_field_off(tmp_path) -> None:
    path = fixtures.make_multipage_radio_pdf(tmp_path / "radio.pdf")
    doc = PdfDocument.open(path)
    try:
        assert _radio_value(doc) == ("name", "/C")  # the button on page 2
        stack = QUndoStack()
        delete = DeletePagesCommand(doc, [1])
        delete.apply_now()
        stack.push(delete)
        assert _radio_value(doc) == ("name", "/Off")
        first = doc.widgets(0)[0]
        assert doc.field_button_state(first) == "Off"
        select = SetFieldValueCommand(doc, first, True)
        select.apply_now()
        stack.push(select)
        stack.undo()
        assert select.error is None
        assert _radio_value(doc) == ("name", "/Off")
        stack.undo()
        assert _radio_value(doc) == ("name", "/C")
    finally:
        doc.close()


def test_deleting_an_unselected_radio_button_keeps_the_value(tmp_path) -> None:
    path = fixtures.make_multipage_radio_pdf(tmp_path / "radio.pdf")
    doc = PdfDocument.open(path)
    try:
        select = SetFieldValueCommand(doc, doc.widgets(0)[0], True)
        select.apply_now()
        on = _radio_value(doc)
        DeletePagesCommand(doc, [1]).apply_now()
        assert _radio_value(doc) == on != ("name", "/Off")
    finally:
        doc.close()


# -- m1: inserting from a file follows its copy permission ------------------------------
def _restricted_pdf(path) -> None:
    doc = pymupdf.open()
    doc.new_page()
    doc.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="u",
        owner_pw="o",
        permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ACCESSIBILITY,
    )
    doc.close()


def test_open_source_refuses_sources_without_copy_permission(tmp_path, owner_locked_pdf) -> None:
    from pdfeditor.core.document import OpenError

    with pytest.raises(OpenError) as info:
        pages.open_source(str(owner_locked_pdf))
    assert info.value.reason == "no_copy"
    path = tmp_path / "restricted.pdf"
    _restricted_pdf(path)
    with pytest.raises(OpenError) as info:
        pages.open_source(str(path), password="u")
    assert info.value.reason == "no_copy"
    src, used = pages.open_source(str(path), password="o")  # owner password: allowed
    try:
        assert used == "o" and src.page_count == 1
    finally:
        src.close()
