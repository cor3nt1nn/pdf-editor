"""M6a review and fuzzer findings: core fixes (page commands, form hygiene, XFA)."""

from __future__ import annotations

import shutil

import pytest
from PySide6.QtGui import QUndoStack

from pdfeditor.core.commands import DeletePagesCommand, RotatePagesCommand
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
