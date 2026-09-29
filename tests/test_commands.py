from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QUndoStack

from pdfeditor.core.commands import DocumentCommand, RotatePageCommand
from pdfeditor.core.document import PdfDocument
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.tools.base import Tool, ToolEvent
from pdfeditor.ui.tools.hand_tool import HandTool


@pytest.fixture
def doc(simple_pdf):
    d = PdfDocument.open(simple_pdf)
    yield d
    d.close()


def test_rotate_undo_redo(qtbot, doc: PdfDocument) -> None:
    stack = QUndoStack()
    cmd = RotatePageCommand(doc, 1, 90)
    assert isinstance(cmd, DocumentCommand)
    assert cmd.text() == "Rotate page"
    with qtbot.waitSignal(doc.page_changed) as blocker:
        stack.push(cmd)
    assert blocker.args == [1]
    assert doc.page_rotation(1) == 90
    with qtbot.waitSignal(doc.page_changed):
        stack.undo()
    assert doc.page_rotation(1) == 0
    with qtbot.waitSignal(doc.page_changed):
        stack.redo()
    assert doc.page_rotation(1) == 90


def test_consecutive_rotations_merge(doc: PdfDocument) -> None:
    stack = QUndoStack()
    stack.push(RotatePageCommand(doc, 0, 90))
    stack.push(RotatePageCommand(doc, 0, 90))
    assert stack.count() == 1
    assert doc.page_rotation(0) == 180
    stack.push(RotatePageCommand(doc, 1, -90))
    assert stack.count() == 2
    assert doc.page_rotation(1) == 270
    stack.undo()
    stack.undo()
    assert doc.page_rotation(0) == 0
    assert doc.page_rotation(1) == 0


def test_rotate_back_to_original_is_obsolete(doc: PdfDocument) -> None:
    stack = QUndoStack()
    stack.push(RotatePageCommand(doc, 0, 90))
    stack.push(RotatePageCommand(doc, 0, -90))
    assert doc.page_rotation(0) == 0
    assert stack.count() == 0


def test_no_merge_across_clean_state(doc: PdfDocument) -> None:
    stack = QUndoStack()
    stack.push(RotatePageCommand(doc, 0, 90))
    stack.setClean()
    stack.push(RotatePageCommand(doc, 0, 90))
    assert stack.count() == 2
    stack.undo()
    assert stack.isClean()


def test_invalid_delta(doc: PdfDocument) -> None:
    with pytest.raises(ValueError):
        RotatePageCommand(doc, 0, 45)


@pytest.fixture
def window(qtbot, settings, simple_pdf):
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.resize(900, 700)
    w.show()
    qtbot.waitExposed(w)
    w.open_file(str(simple_pdf))
    yield w
    w.undo_stack.setClean()
    w.close()


def test_window_rotate_dirty_and_save(qtbot, window: MainWindow) -> None:
    assert window.act_undo.text() == "Undo"
    assert not window.act_undo.isEnabled()
    assert not window.isWindowModified()
    window.page_view.scroll_to_page(1)
    window.act_rotate_cw.trigger()
    doc = window.document_view.document
    assert doc.page_rotation(1) == 90
    assert window.isWindowModified()
    assert window.windowTitle() == "simple.pdf[*] — PDF Editor"
    assert window.act_undo.text() == "Undo Rotate page"
    window.act_rotate_ccw.trigger()
    window.act_rotate_ccw.trigger()
    assert doc.page_rotation(1) == 270
    assert window.undo_stack.count() == 1
    assert window.save()
    assert not window.isWindowModified()
    window.act_undo.trigger()
    assert doc.page_rotation(1) == 0
    assert window.isWindowModified()
    window.act_redo.trigger()
    assert not window.isWindowModified()
    assert window.act_rotate_cw.shortcut().toString() == "Ctrl+R"
    assert window.act_rotate_ccw.shortcut().toString() == "Ctrl+Shift+R"


def test_rotation_updates_view(window: MainWindow) -> None:
    item = window.page_view.page_item(0)
    window.page_view.scroll_to_page(0)
    window.act_rotate_cw.trigger()
    assert item.size.width() == 842
    window.act_undo.trigger()
    assert item.size.width() == 595


class RecordingTool(Tool):
    name = "recorder"

    def __init__(self) -> None:
        super().__init__()
        self.events: list[ToolEvent] = []

    def mouse_press(self, event: ToolEvent) -> bool:
        self.events.append(event)
        return True


def test_tool_manager_wiring(qtbot, window: MainWindow) -> None:
    tm = window.tool_manager
    assert isinstance(tm.active_tool, HandTool)
    assert window.page_view.tool_manager is tm
    assert window.act_hand_tool.isChecked()
    # HandTool lets the view pan (returns False)
    assert tm.active_tool.mouse_press(None) is False

    recorder = RecordingTool()
    tm.register(recorder)
    with qtbot.waitSignal(tm.tool_changed) as blocker:
        tm.set_active("recorder")
    assert blocker.args == ["recorder"]
    pv = window.page_view
    pv.set_zoom_percent(100)
    pv.scroll_to_page(0)
    vp_rect = pv.page_rect_to_viewport(0, pv.page_item(0).boundingRect())
    pos = QPointF(vp_rect.center())
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos,
        pv.viewport().mapToGlobal(pos),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    pv.mousePressEvent(event)
    assert len(recorder.events) == 1
    ev = recorder.events[0]
    assert ev.page_index == 0
    assert 0 <= ev.page_pos.x() <= 595
    assert 0 <= ev.page_pos.y() <= 842
    tm.set_active("hand")
    assert window.act_hand_tool.isChecked()
