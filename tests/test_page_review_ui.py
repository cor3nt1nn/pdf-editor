"""M6a review and fuzzer findings: UI fixes (undo failures, thumbnails, Pages actions)."""

from __future__ import annotations

import os
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


# -- M4: the Pages actions work on what the sidebar highlights --------------------------
def _click(qtbot, sidebar, row: int, modifier=None) -> None:
    from PySide6.QtCore import Qt

    sidebar.scrollTo(sidebar.model().index(row, 0))
    rect = sidebar.visualRect(sidebar.model().index(row, 0))
    qtbot.mouseClick(
        sidebar.viewport(),
        Qt.MouseButton.LeftButton,
        modifier or Qt.KeyboardModifier.NoModifier,
        rect.center(),
    )


def test_target_pages_follow_a_single_remaining_selection(qtbot, window, six_pdf) -> None:
    from PySide6.QtCore import Qt

    ctrl = Qt.KeyboardModifier.ControlModifier
    assert window.open_file(str(six_pdf))
    sidebar = window.thumbnails
    _click(qtbot, sidebar, 2)
    assert window.target_pages() == [2]
    _click(qtbot, sidebar, 4, ctrl)
    assert window.target_pages() == [2, 4]
    _click(qtbot, sidebar, 2, ctrl)  # Ctrl+click removes the current page from it
    assert sidebar.selected_pages() == [4]
    assert window.page_view.current_page == 2
    assert window.target_pages() == [4]
    window.act_delete_pages.trigger()
    doc = window.document_view.document
    with doc.lock:
        texts = [doc.fitz[i].get_text().strip() for i in range(doc.page_count)]
    assert texts == ["Page 1", "Page 2", "Page 3", "Page 4", "Page 6"]
    # Nothing selected: the current page.
    sidebar.clearSelection()
    assert window.target_pages() == [window.page_view.current_page]


# -- m5: the form tool follows its fields across page operations -------------------------
def test_form_tool_focus_follows_page_operations(window, tmp_path) -> None:
    path = fixtures.make_multipage_radio_pdf(tmp_path / "radio.pdf")
    assert window.open_file(str(path))
    tool = window.form_tool
    assert window.tool_manager.active_tool is tool
    doc = window.document_view.document
    button = doc.widgets(1)[0]  # the radio button on page 2
    tool.focus(button)
    assert tool.focused_button is not None and tool.focused_button.page == 1
    assert window.move_pages([1], 0)
    assert tool.focused_button is not None
    assert tool.focused_button.page == 0
    assert tool.focused_button.name == button.name
    assert tool.focused_button.unrotated_rect == button.unrotated_rect
    assert tool._last is not None and tool._last.page == 0
    assert window.delete_pages([0])
    assert tool.focused_button is None and tool._last is None
    window.undo()
    assert tool.focused_button is None


# -- m7: UI polish -------------------------------------------------------------------------
def test_own_path_warning_is_titled_by_action(window, tmp_path, monkeypatch) -> None:
    import shutil as _shutil

    from pdfeditor.ui import page_dialogs

    path = tmp_path / "doc-01.pdf"
    _shutil.copy(fixtures.make_simple_pdf(tmp_path / "simple.pdf"), path)
    assert window.open_file(str(path))
    monkeypatch.setattr(dialogs, "get_extract_path", lambda *_a: str(path))
    assert not window.extract_pages([0])
    assert window.warnings[-1][0] == "Extract Pages"
    monkeypatch.setattr(
        page_dialogs,
        "split_document",
        lambda *_a, **_k: ([[0], [1, 2]], [str(path), str(tmp_path / "doc-02.pdf")]),
    )
    assert not window.split_document()
    assert window.warnings[-1][0] == "Split Document"
    assert not (tmp_path / "doc-02.pdf").exists()


def test_split_dialog_explains_disabled_ok(qtbot, tmp_path) -> None:
    from pdfeditor.ui.page_dialogs import SplitDialog

    dialog = SplitDialog(4, "out", "report", document_dir=str(tmp_path))
    qtbot.addWidget(dialog)
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok.isEnabled()
    _groups, paths = dialog.result_value()
    assert paths[0] == str(tmp_path / "out" / "report-01.pdf")  # relative to the document
    dialog.base_edit.setText("")
    assert not ok.isEnabled() and dialog.summary_label.text() == "Enter a base name for the files."
    dialog.base_edit.setText("a/b")
    assert not ok.isEnabled()
    assert dialog.summary_label.text().startswith("A file name cannot contain")
    dialog.base_edit.setText("report")
    dialog.folder_edit.setText("  ")
    assert not ok.isEnabled()
    assert dialog.summary_label.text() == "Choose the folder of the files."
    dialog.folder_edit.setText(str(tmp_path))
    assert ok.isEnabled()
    assert dialog.result_value()[1][0] == str(tmp_path / "report-01.pdf")


def test_split_partial_failure_names_written_files(window, simple_pdf, tmp_path, monkeypatch):
    from pdfeditor.core import document as document_module
    from pdfeditor.ui import page_dialogs

    assert window.open_file(str(simple_pdf))
    out = tmp_path / "out"
    out.mkdir()
    paths = [str(out / f"part-0{n}.pdf") for n in (1, 2, 3)]
    monkeypatch.setattr(
        page_dialogs, "split_document", lambda *_a, **_k: ([[0], [1], [2]], list(paths))
    )
    real_write = document_module._write_atomically

    def write(path, data):
        if path.endswith("part-03.pdf"):
            raise OSError("disk full")
        real_write(path, data)

    monkeypatch.setattr(document_module, "_write_atomically", write)
    assert not window.split_document()
    title, text = window.warnings[-1]
    assert "The pages could not be written." in text
    assert "“part-01.pdf”, “part-02.pdf”" in text
    assert os.path.exists(paths[1]) and not os.path.exists(paths[2])


def test_context_menu_insert_anchors_on_the_clicked_page(window, six_pdf, monkeypatch) -> None:
    from PySide6.QtGui import QContextMenuEvent

    class NoMenu:
        def exec(self, _pos):
            return None

        def deleteLater(self):  # noqa: N802 - Qt name
            pass

    assert window.open_file(str(six_pdf))
    sidebar = window.thumbnails
    sidebar.select_pages([1, 2, 3], current=1)
    seen: list = []
    monkeypatch.setattr(
        window,
        "page_context_menu",
        lambda rows, clicked=None: seen.append((rows, clicked)) or NoMenu(),
    )
    rect = sidebar.visualRect(sidebar.model().index(2, 0))
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, rect.center(), sidebar.viewport().mapToGlobal(rect.center())
    )
    sidebar.contextMenuEvent(event)
    assert seen == [([1, 2, 3], 2)]
    assert sidebar.take_context_row() is None  # taken once
    menu = MainWindow.page_context_menu(window, [1, 2, 3], clicked=2)
    blank = next(a for a in menu.actions() if a.objectName() == "insert_blank_page")
    blank.trigger()
    doc = window.document_view.document
    with doc.lock:
        texts = [doc.fitz[i].get_text().strip() for i in range(doc.page_count)]
    assert texts[:5] == ["Page 1", "Page 2", "Page 3", "", "Page 4"]
    menu.deleteLater()


def test_banner_follows_page_operations(window, tmp_path) -> None:
    import pymupdf

    path = tmp_path / "locked_form.pdf"
    src = pymupdf.open()
    src.new_page()
    src.new_page()
    widget = pymupdf.Widget()
    widget.field_name = "name"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.rect = pymupdf.Rect(50, 50, 200, 80)
    src[0].add_widget(widget)
    src.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="o",
        user_pw="",
        permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ASSEMBLE,
    )
    src.close()
    assert window.open_file(str(path))
    view = window.document_view
    assert view.banner.message is not None and "not permitted" in view.banner.message[0]
    assert window.delete_pages([0])  # the form's only page
    assert view.banner.message is None
    window.undo()
    assert view.banner.message is not None
