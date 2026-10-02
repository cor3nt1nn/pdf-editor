"""M6a review and fuzzer findings: core fixes (page commands, form hygiene, XFA)."""

from __future__ import annotations

import shutil

import fixtures
import pytest
from PySide6.QtGui import QUndoStack

from pdfeditor.core.annotations import is_synthetic
from pdfeditor.core.commands import (
    DeleteAnnotCommand,
    DeletePagesCommand,
    EditAnnotCommand,
    RotatePagesCommand,
)
from pdfeditor.core.document import PageError, PdfDocument
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
