"""M6a-T4: thumbnail sidebar multi-selection, drag-and-drop requests, Delete, context menu."""

from __future__ import annotations

import fixtures
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QDropEvent

from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.thumbnails import PAGES_MIME, decode_rows, encode_rows

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.fixture
def window(qtbot, settings, tmp_path):
    path = fixtures.make_many_pages_pdf(tmp_path / "six.pdf", count=6)
    w = MainWindow(settings)
    # Sidebar requests only (MainWindow's handlers are tested in test_page_tools_ui).
    w.thumbnails.pages_move_requested.disconnect(w.move_pages)
    w.thumbnails.pages_delete_requested.disconnect(w.delete_pages)
    w.thumbnails.context_menu_requested.disconnect(w._show_page_context_menu)
    qtbot.addWidget(w)
    w.resize(1000, 900)
    w.show()
    qtbot.waitExposed(w)
    assert w.open_file(str(path))
    yield w
    w.undo_stack.setClean()
    w.close()


def _click(qtbot, sidebar, row: int, modifier=Qt.KeyboardModifier.NoModifier) -> None:
    sidebar.scrollTo(sidebar.model().index(row, 0))
    rect = sidebar.visualRect(sidebar.model().index(row, 0))
    qtbot.mouseClick(sidebar.viewport(), Qt.MouseButton.LeftButton, modifier, rect.center())


def _drop(sidebar, rows: list[int], pos: QPoint) -> QDropEvent:
    data = sidebar.model().mimeData([sidebar.model().index(r, 0) for r in rows])
    event = QDropEvent(
        QPointF(pos),
        Qt.DropAction.MoveAction,
        data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    sidebar.dropEvent(event)
    return event


def test_mime_round_trip() -> None:
    assert decode_rows(encode_rows([3, 1, 3])) == [1, 3]
    assert decode_rows(b"x,1") == []
    assert decode_rows(b"") == []


def test_model_drag_flags(window: MainWindow) -> None:
    model = window.thumbnail_model
    assert model.flags(model.index(0)) & Qt.ItemFlag.ItemIsDragEnabled
    assert not model.flags(model.index(0)) & Qt.ItemFlag.ItemIsDropEnabled
    assert model.mimeTypes() == [PAGES_MIME]
    assert model.supportedDropActions() == Qt.DropAction.MoveAction
    assert decode_rows(model.mimeData([model.index(4), model.index(2)]).data(PAGES_MIME)) == [2, 4]


def test_ctrl_and_shift_click_extend_selection(qtbot, window: MainWindow) -> None:
    sidebar = window.thumbnails
    _click(qtbot, sidebar, 1)
    _click(qtbot, sidebar, 3, CTRL)
    assert sidebar.selected_pages() == [1, 3]
    _click(qtbot, sidebar, 0)
    assert sidebar.selected_pages() == [0]
    _click(qtbot, sidebar, 2, SHIFT)
    assert sidebar.selected_pages() == [0, 1, 2]


def test_set_current_page_keeps_multi_selection(window: MainWindow) -> None:
    sidebar = window.thumbnails
    sidebar.select_pages([1, 3])
    assert sidebar.selected_pages() == [1, 3]
    assert sidebar.currentIndex().row() == 1
    sidebar.set_current_page(3)
    assert sidebar.selected_pages() == [1, 3]
    assert sidebar.currentIndex().row() == 3
    # Navigating outside the selection collapses it to the new page.
    sidebar.set_current_page(5)
    assert sidebar.selected_pages() == [5]
    # Page view navigation inside the selection keeps it too.
    sidebar.select_pages([0, 2, 4])
    window.page_view.scroll_to_page(2)
    assert sidebar.selected_pages() == [0, 2, 4]
    assert sidebar.currentIndex().row() == 2


def test_select_pages_does_not_navigate(qtbot, window: MainWindow) -> None:
    sidebar = window.thumbnails
    with qtbot.assertNotEmitted(sidebar.page_requested):
        sidebar.select_pages([2, 4, 99], current=4)
    assert sidebar.selected_pages() == [2, 4]
    assert sidebar.currentIndex().row() == 4


def test_drop_emits_move_request(qtbot, window: MainWindow) -> None:
    sidebar = window.thumbnails
    model = window.thumbnail_model
    sidebar.select_pages([1, 3])
    sidebar.scrollToBottom()
    last = sidebar.visualRect(model.index(5))
    below = QPoint(last.center().x(), last.bottom() - 2)
    with qtbot.waitSignal(sidebar.pages_move_requested) as blocker:
        event = _drop(sidebar, [1, 3], below)
    assert blocker.args == [[1, 3], 6]
    assert event.isAccepted()
    assert event.dropAction() == Qt.DropAction.IgnoreAction

    sidebar.scrollToTop()
    first = sidebar.visualRect(model.index(0))
    with qtbot.waitSignal(sidebar.pages_move_requested) as blocker:
        _drop(sidebar, [1, 3], first.topLeft() + QPoint(5, 2))
    assert blocker.args == [[1, 3], 0]

    # Lower half of row 2 -> before row 3.
    sidebar.scrollTo(model.index(2))
    mid = sidebar.visualRect(model.index(2))
    with qtbot.waitSignal(sidebar.pages_move_requested) as blocker:
        _drop(sidebar, [0], QPoint(mid.center().x(), mid.bottom() - 2))
    assert blocker.args == [[0], 3]

    # Model and document untouched.
    assert model.rowCount() == 6
    assert window.document_view.document.page_count == 6
    assert window.undo_stack.count() == 0


def test_foreign_drop_ignored(qtbot, window: MainWindow) -> None:
    from PySide6.QtCore import QMimeData

    sidebar = window.thumbnails
    data = QMimeData()
    data.setText("hello")
    event = QDropEvent(
        QPointF(5, 5),
        Qt.DropAction.MoveAction,
        data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    with qtbot.assertNotEmitted(sidebar.pages_move_requested):
        sidebar.dropEvent(event)
    assert not event.isAccepted()


def test_delete_key_emits_request(qtbot, window: MainWindow) -> None:
    sidebar = window.thumbnails
    sidebar.setFocus()
    sidebar.select_pages([1, 3])
    with qtbot.waitSignal(sidebar.pages_delete_requested) as blocker:
        qtbot.keyClick(sidebar, Qt.Key.Key_Delete)
    assert blocker.args == [[1, 3]]
    assert window.document_view.document.page_count == 6


def test_context_menu_request(qtbot, window: MainWindow) -> None:
    sidebar = window.thumbnails
    model = window.thumbnail_model
    sidebar.select_pages([1, 2])
    sidebar.scrollTo(model.index(2))
    pos = sidebar.visualRect(model.index(2)).center()
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, pos, sidebar.viewport().mapToGlobal(pos)
    )
    with qtbot.waitSignal(sidebar.context_menu_requested) as blocker:
        sidebar.contextMenuEvent(event)
    assert blocker.args[0] == [1, 2]
    assert isinstance(blocker.args[1], QPoint)

    # Right-click outside the selection targets the clicked page only.
    sidebar.scrollTo(model.index(4))
    pos = sidebar.visualRect(model.index(4)).center()
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, pos, sidebar.viewport().mapToGlobal(pos)
    )
    with qtbot.waitSignal(sidebar.context_menu_requested) as blocker:
        sidebar.contextMenuEvent(event)
    assert blocker.args[0] == [4]


def test_selection_follows_pages_after_structure_change(qtbot, window: MainWindow) -> None:
    sidebar = window.thumbnails
    doc = window.document_view.document
    window.page_view.scroll_to_page(4)
    sidebar.select_pages([2, 4], current=4)
    doc.delete_pages([0])
    assert window.thumbnail_model.rowCount() == 5
    assert sidebar.selected_pages() == [1, 3]
    assert window.page_view.current_page == 3
    assert sidebar.currentIndex().row() == 3  # mirrors the page view

    ids = doc.page_ids()
    doc.reorder_pages([ids[3], *ids[:3], ids[4]])  # page at row 3 moves first
    assert sidebar.selected_pages() == [0, 2]
    assert sidebar.currentIndex().row() == window.page_view.current_page == 0

    doc.delete_pages([2])  # one of the two selected pages goes away
    assert sidebar.selected_pages() == [window.page_view.current_page]
    assert sidebar.currentIndex().row() == window.page_view.current_page

    # Undo-like insert path: a single selection follows the page view's new page.
    doc.insert_blank_page(0, doc.page_size(0))
    assert sidebar.currentIndex().row() == window.page_view.current_page == 0
    assert sidebar.selected_pages() == [0]
