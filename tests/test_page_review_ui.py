"""M6a review and fuzzer findings: UI fixes (undo failures, thumbnails, Pages actions)."""

from __future__ import annotations

import shutil

import fixtures
import pytest
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
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


# -- C1/M5/m4: thumbnail drag and drop ---------------------------------------------------
def _drag_event(cls, sidebar, rows, pos, mime=None):
    from PySide6.QtCore import QPointF, Qt

    data = mime or sidebar.model().mimeData([sidebar.model().index(r, 0) for r in rows])
    event = cls(
        QPointF(pos) if cls is QDropEvent else pos,
        Qt.DropAction.MoveAction,
        data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    _KEEP.append(data)  # the event does not own its QMimeData
    return event


_KEEP: list = []


def test_drag_over_another_thumbnail_is_accepted(window, six_pdf) -> None:
    """C1: Qt 6.11's IconMode refused a drag over an item that is not drop-enabled."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QApplication

    assert window.open_file(str(six_pdf))
    sidebar = window.thumbnails
    model = sidebar.model()
    sidebar.scrollToTop()
    rect = sidebar.visualRect(model.index(2, 0))
    upper = QPoint(rect.center().x(), rect.top() + 5)
    enter = _drag_event(QDragEnterEvent, sidebar, [0], upper)
    QApplication.sendEvent(sidebar.viewport(), enter)
    assert enter.isAccepted()
    move = _drag_event(QDragMoveEvent, sidebar, [0], upper)
    QApplication.sendEvent(sidebar.viewport(), move)
    assert move.isAccepted()
    assert move.dropAction() == Qt.DropAction.MoveAction
    assert sidebar.drop_target == 2
    lower = QPoint(rect.center().x(), rect.bottom() - 5)
    move = _drag_event(QDragMoveEvent, sidebar, [0], lower)
    QApplication.sendEvent(sidebar.viewport(), move)
    assert move.isAccepted() and sidebar.drop_target == 3
    sidebar.viewport().grab()  # paints the insertion line
    above, below = rect.bottom(), sidebar.visualRect(model.index(3, 0)).top()
    assert above <= sidebar.drop_indicator_y(3) <= below
    drop = _drag_event(QDropEvent, sidebar, [0], lower)
    QApplication.sendEvent(sidebar.viewport(), drop)
    assert drop.isAccepted() and sidebar.drop_target is None
    # MainWindow moved page 1 after page 3.
    assert window.undo_stack.count() == 1
    doc = window.document_view.document
    with doc.lock:
        texts = [doc.fitz[i].get_text().strip() for i in range(4)]
    assert texts == ["Page 2", "Page 3", "Page 1", "Page 4"]


def test_drop_row_uses_y_only(window, six_pdf) -> None:
    """M5: a drop in a gap or beside a thumbnail goes between its neighbours."""
    from PySide6.QtCore import QPoint

    assert window.open_file(str(six_pdf))
    sidebar = window.thumbnails
    model = sidebar.model()
    sidebar.scrollToTop()
    r2 = sidebar.visualRect(model.index(2, 0))
    r3 = sidebar.visualRect(model.index(3, 0))
    gap = QPoint(r2.center().x(), (r2.bottom() + r3.top()) // 2)
    assert sidebar.drop_row(gap) == 3
    beside_upper = QPoint(r2.right() + 30, r2.top() + 3)
    assert sidebar.drop_row(beside_upper) == 2
    beside_lower = QPoint(r2.right() + 30, r2.bottom() - 3)
    assert sidebar.drop_row(beside_lower) == 3
    assert sidebar.drop_row(QPoint(5, -50)) == 0
    last = sidebar.visualRect(model.index(5, 0))
    assert sidebar.drop_row(QPoint(5, last.bottom() + 40)) == 6


def test_foreign_drags_are_ignored(window, six_pdf) -> None:
    from PySide6.QtCore import QByteArray, QMimeData, QPoint
    from PySide6.QtWidgets import QApplication

    from pdfeditor.ui.thumbnails import PAGES_MIME, PAGES_SOURCE_MIME

    assert window.open_file(str(six_pdf))
    sidebar = window.thumbnails
    other = QMimeData()  # pages from another window: other token
    other.setData(PAGES_MIME, QByteArray(b"1"))
    other.setData(PAGES_SOURCE_MIME, QByteArray(b"someone-else"))
    enter = _drag_event(QDragEnterEvent, sidebar, [], QPoint(20, 20), mime=other)
    QApplication.sendEvent(sidebar.viewport(), enter)
    assert not enter.isAccepted() and sidebar.drop_target is None


def test_drag_disabled_without_page_permission(window, owner_locked_pdf, six_pdf) -> None:
    """m4: no thumbnail drag where page operations are not allowed."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    assert window.open_file(str(owner_locked_pdf))
    sidebar = window.thumbnails
    assert not sidebar.dragEnabled()
    enter = _drag_event(QDragEnterEvent, sidebar, [0], QPoint(20, 20))
    QApplication.sendEvent(sidebar.viewport(), enter)
    assert not enter.isAccepted() and sidebar.drop_target is None
    assert window.open_file(str(six_pdf))
    assert sidebar.dragEnabled()


def test_drag_near_the_bottom_scrolls(qtbot, window, tmp_path) -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    assert window.open_file(str(fixtures.make_many_pages_pdf(tmp_path / "m.pdf", count=30)))
    sidebar = window.thumbnails
    sidebar.scrollToTop()
    bar = sidebar.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == 0
    bottom = QPoint(20, sidebar.viewport().height() - 2)
    enter = _drag_event(QDragEnterEvent, sidebar, [0], bottom)
    QApplication.sendEvent(sidebar.viewport(), enter)  # a move needs an enter first
    assert enter.isAccepted()
    QApplication.sendEvent(sidebar.viewport(), _drag_event(QDragMoveEvent, sidebar, [0], bottom))
    qtbot.waitUntil(lambda: bar.value() > 0, timeout=2000)
    QApplication.sendEvent(sidebar.viewport(), QDragLeaveEvent())
    assert sidebar.drop_target is None
