"""M6a review and fuzzer findings: UI fixes (undo failures, thumbnails, Pages actions)."""

from __future__ import annotations

import shutil

import fixtures
import pytest
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox

from pdfeditor.core.document import PdfDocument
from pdfeditor.core.snapshots import SnapshotStore
from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.page_dialogs import InsertPagesDialog


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        dialogs, "warn", lambda parent, title, text, details=None: warnings.append((title, text))
    )
    asked: list[str] = []

    def confirm(_parent, name):
        asked.append(name)
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(dialogs, "confirm_save_changes", confirm)
    w = MainWindow(settings)
    w.warnings = warnings
    w.asked = asked
    qtbot.addWidget(w)
    w.resize(900, 700)
    w.show()
    qtbot.waitExposed(w)
    yield w
    w.undo_stack.setClean()
    w.close()


@pytest.fixture
def six_pdf(tmp_path):
    return fixtures.make_many_pages_pdf(tmp_path / "six.pdf", count=6)


# -- M2: a failing undo clears the history and keeps the document dirty ----------------
def test_failed_undo_clears_history_and_stays_dirty(window, six_pdf, tmp_path) -> None:
    assert window.open_file(str(six_pdf))
    doc = window.document_view.document
    doc.snapshots = SnapshotStore(memory_limit=0, directory=tmp_path)
    assert window.rotate_pages([0], 90)
    assert window.delete_pages([2])
    assert doc.page_count == 5
    shutil.rmtree(doc.snapshots.spill_directory)
    window.undo()
    assert doc.page_count == 5  # the deletion could not be undone
    assert window.undo_stack.count() == 0
    assert not window.undo_stack.canUndo() and not window.undo_stack.canRedo()
    assert window.document_view.is_dirty
    assert window.isWindowModified()
    assert len(window.warnings) == 1
    assert "could not be undone" in window.warnings[0][1]
    # The close prompt still appears.
    assert window.maybe_save()
    assert window.asked


def test_failed_redo_clears_history(window, six_pdf, monkeypatch) -> None:
    assert window.open_file(str(six_pdf))
    doc = window.document_view.document
    assert window.delete_pages([1])
    window.undo()
    assert doc.page_count == 6
    assert window.undo_stack.canRedo()

    def boom(*_a, **_k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(doc, "delete_pages", boom)
    window.redo()
    assert doc.page_count == 6
    assert window.undo_stack.count() == 0
    assert window.document_view.is_dirty
    assert "could not be redone" in window.warnings[-1][1]


# -- m1: Insert Pages from File refuses a source that forbids copying ---------------------
def test_insert_dialog_refuses_copy_protected_source(qtbot, settings, simple_pdf, owner_locked_pdf):
    doc = PdfDocument.open(str(simple_pdf))
    try:
        dialog = InsertPagesDialog(doc, 0, settings)
        qtbot.addWidget(dialog)
        assert not dialog.set_path(str(owner_locked_pdf))
        assert "not permitted" in dialog.error_label.text()
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    finally:
        doc.close()
