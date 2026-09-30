"""M3-T5: text and stamp tools, DocumentView wiring and tool actions."""

from __future__ import annotations

import fixtures
import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import QMessageBox, QPlainTextEdit

from pdfeditor.core.annotations import STAMP_CENTRE, AnnotKind
from pdfeditor.core.commands import (
    AddAnnotCommand,
    DeleteAnnotCommand,
    EditAnnotCommand,
    RotatePageCommand,
)
from pdfeditor.core.settings import Settings
from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.overlays.annot_items import Handle
from pdfeditor.ui.tools.annot_tools import form_field_message, resized_rect

ALT = Qt.KeyboardModifier.AltModifier
NO_MOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
#: Empty spot of the word_form page (no shape nearby).
EMPTY = QPointF(400, 600)
NOT_PERMITTED = "Adding text and stamps is not permitted by this document’s security settings."


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: None)
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Discard
    )
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.resize(900, 700)
    w.show()
    qtbot.waitExposed(w)
    w.activateWindow()
    yield w
    w.undo_stack.setClean()
    w.close()


@pytest.fixture
def word_window(window, word_form_pdf):
    assert window.open_file(str(word_form_pdf))
    window.act_text_tool.trigger()
    assert window.tool_manager.active_tool is window.text_tool
    return window


def _vp(w: MainWindow, page: int, point: QPointF) -> QPoint:
    """Viewport position of page-space ``point`` (scrolled into view first)."""
    pv = w.page_view
    scene = pv.page_item(page).mapToScene(point)
    pv.ensureVisible(QRectF(scene.x() - 1, scene.y() - 1, 2, 2), 60, 60)
    return pv.mapFromScene(pv.page_item(page).mapToScene(point))


def _click(qtbot, w: MainWindow, point: QPointF, page: int = 0, modifier=NO_MOD) -> None:
    qtbot.mouseClick(w.page_view.viewport(), LEFT, modifier, _vp(w, page, point))


def _drag(qtbot, w: MainWindow, point: QPointF, dx: int, dy: int = 0) -> None:
    vp = w.page_view.viewport()
    start = _vp(w, 0, point)
    qtbot.mousePress(vp, LEFT, NO_MOD, start)
    qtbot.mouseMove(vp, start + QPoint(dx // 2, dy // 2))
    qtbot.mouseMove(vp, start + QPoint(dx, dy))
    qtbot.mouseRelease(vp, LEFT, NO_MOD, start + QPoint(dx, dy))


def _editor(w: MainWindow) -> QPlainTextEdit:
    widget = w.document_view.annot_editor.editor
    assert isinstance(widget, QPlainTextEdit)
    return widget


def _annots(w: MainWindow, page: int = 0):
    return w.document_view.document.annots(page)


def _create_text(qtbot, w: MainWindow, text: str = "abc", at: QPointF = EMPTY):
    _click(qtbot, w, at)
    assert w.document_view.annot_editor.is_open
    qtbot.keyClicks(_editor(w), text)
    qtbot.keyClick(_editor(w), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    (info,) = [a for a in _annots(w) if a.text == text]
    return info


def _messages(tool) -> list[str]:
    messages: list[str] = []
    tool.message.connect(messages.append)
    return messages


# -- text tool ------------------------------------------------------------------------
def test_text_click_opens_editor_and_commit_adds(qtbot, word_window) -> None:
    w = word_window
    _click(qtbot, w, EMPTY)
    assert w.document_view.annot_editor.is_open
    assert w.undo_stack.count() == 0
    qtbot.keyClicks(_editor(w), "abc")
    qtbot.keyClick(_editor(w), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert not w.document_view.annot_editor.is_open
    assert w.undo_stack.count() == 1
    assert isinstance(w.undo_stack.command(0), AddAnnotCommand)
    assert w.isWindowModified()  # the title shows *
    (info,) = _annots(w)
    assert info.text == "abc" and info.kind is AnnotKind.TEXT
    assert info.rect.left() == pytest.approx(EMPTY.x(), abs=1.5)
    # The new box is selected but a single click only arms it (no editor yet).
    assert w.document_view.annot_selection.current.name == info.name


def test_second_click_selects_then_click_again_edits(qtbot, word_window) -> None:
    w = word_window
    info = _create_text(qtbot, w)
    selection = w.document_view.annot_selection
    selection.clear()
    _click(qtbot, w, info.rect.center())
    assert selection.current is not None and selection.current.name == info.name
    assert not w.document_view.annot_editor.is_open
    assert w.act_delete_annot.isEnabled()
    _click(qtbot, w, info.rect.center())
    assert w.document_view.annot_editor.is_open
    assert _editor(w).toPlainText() == "abc"
    assert w.undo_stack.count() == 1


def test_drag_moves_with_one_command(qtbot, word_window) -> None:
    w = word_window
    info = _create_text(qtbot, w, at=QPointF(100, 600))
    scale = w.page_view.view_scale
    _drag(qtbot, w, info.rect.center(), 30)
    assert w.undo_stack.count() == 2
    command = w.undo_stack.command(1)
    assert isinstance(command, EditAnnotCommand)
    assert command.text() == "Move annotation"
    moved = w.document_view.document.annot(0, info.name)
    assert moved.rect.left() - info.rect.left() == pytest.approx(30 / scale, abs=1 / scale)
    assert moved.rect.top() == pytest.approx(info.rect.top(), abs=1e-3)
    assert moved.rect.width() == pytest.approx(info.rect.width(), abs=1e-3)
    assert w.document_view.annot_selection.item.ghost is None
    # Moves are clamped to the page.
    _drag(qtbot, w, moved.rect.center(), -2000)
    assert w.document_view.document.annot(0, info.name).rect.left() == pytest.approx(0, abs=1e-3)


def test_bottom_right_handle_resizes(qtbot, word_window) -> None:
    w = word_window
    info = _create_text(qtbot, w, at=QPointF(100, 600))
    scale = w.page_view.view_scale
    assert w.document_view.annot_selection.current.name == info.name
    _drag(qtbot, w, info.rect.bottomRight(), 40, 20)
    assert w.undo_stack.count() == 2
    command = w.undo_stack.command(1)
    assert isinstance(command, EditAnnotCommand) and command.text() == "Resize annotation"
    resized = w.document_view.document.annot(0, info.name)
    assert resized.rect.width() - info.rect.width() == pytest.approx(40 / scale, abs=1 / scale)
    assert resized.rect.topLeft() == info.rect.topLeft()
    # The height hugs the (single) text line again.
    assert resized.rect.height() == pytest.approx(info.rect.height(), abs=1e-3)


def test_delete_undo_redo(qtbot, word_window) -> None:
    w = word_window
    info = _create_text(qtbot, w)
    qtbot.keyClick(w.page_view, Qt.Key.Key_Delete)
    assert w.undo_stack.count() == 2
    assert isinstance(w.undo_stack.command(1), DeleteAnnotCommand)
    assert _annots(w) == []
    assert w.document_view.annot_selection.current is None
    assert not w.act_delete_annot.isEnabled()
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert [a.name for a in _annots(w)] == [info.name]
    qtbot.keyClick(w.page_view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert _annots(w) == []
    qtbot.keyClick(w.page_view, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert [a.name for a in _annots(w)] == [info.name]
    qtbot.keyClick(w.page_view, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert _annots(w) == []
    assert w.undo_stack.index() == 2


def test_double_click_reopens_prefilled(qtbot, word_window) -> None:
    w = word_window
    info = _create_text(qtbot, w)
    w.document_view.annot_selection.clear()
    qtbot.mouseDClick(w.page_view.viewport(), LEFT, NO_MOD, _vp(w, 0, info.rect.center()))
    assert w.document_view.annot_editor.is_open
    assert _editor(w).toPlainText() == "abc"
    qtbot.keyClicks(_editor(w), "d")
    qtbot.keyClick(_editor(w), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert w.undo_stack.count() == 2
    assert w.undo_stack.command(1).text() == "Edit text"
    assert w.document_view.document.annot(0, info.name).text == "abcd"


def test_emptying_existing_text_deletes_it(qtbot, word_window) -> None:
    w = word_window
    info = _create_text(qtbot, w)
    w.text_tool.click_selected(info)
    _editor(w).clear()
    qtbot.keyClick(_editor(w), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert isinstance(w.undo_stack.command(1), DeleteAnnotCommand)
    assert _annots(w) == []


def test_escape_with_empty_new_editor_creates_nothing(qtbot, word_window) -> None:
    w = word_window
    _click(qtbot, w, EMPTY)
    assert w.document_view.annot_editor.is_open
    qtbot.keyClick(_editor(w), Qt.Key.Key_Escape)
    assert not w.document_view.annot_editor.is_open
    assert w.undo_stack.count() == 0
    assert _annots(w) == []
    assert not w.isWindowModified()


def test_text_snaps_into_table_cell(qtbot, word_window) -> None:
    w = word_window
    _click(qtbot, w, QPointF(120, 165))
    rect = w.document_view.annot_editor.anchor.rect
    assert rect.left() == pytest.approx(fixtures.WORD_TABLE_X[0] + 2, abs=0.5)
    assert rect.width() == pytest.approx(100 - 4, abs=0.6)


def test_hover_previews_snap_target(qtbot, word_window) -> None:
    w = word_window
    qtbot.mouseMove(w.page_view.viewport(), _vp(w, 0, QPointF(120, 165)))
    page, rect = w.text_tool.preview
    assert page == 0
    assert rect.left() == pytest.approx(72, abs=0.6) and rect.right() == pytest.approx(172, abs=0.6)
    qtbot.mouseMove(w.page_view.viewport(), _vp(w, 0, EMPTY))
    page, rect = w.text_tool.preview
    assert rect.left() == pytest.approx(EMPTY.x(), abs=1.5)
    w.act_hand_tool.trigger()
    assert w.text_tool.preview is None


def test_typing_digits_and_letters_in_editor_keeps_tool(qtbot, word_window) -> None:
    w = word_window
    _click(qtbot, w, EMPTY)
    qtbot.keyClicks(_editor(w), "a1t2h")
    assert w.tool_manager.active_tool is w.text_tool
    assert _editor(w).toPlainText() == "a1t2h"


# -- stamps -----------------------------------------------------------------------------
def test_stamp_centred_in_checkbox(qtbot, window, word_form_pdf) -> None:
    w = window
    assert w.open_file(str(word_form_pdf))
    w.act_stamp_check.trigger()
    assert w.tool_manager.active_tool.name == "stamp_check"
    x0, y0, x1, y1 = fixtures.WORD_SHAPES["checkbox_12"]
    _click(qtbot, w, QPointF(x0 + 3, y0 + 3))
    assert w.undo_stack.count() == 1
    command = w.undo_stack.command(0)
    assert isinstance(command, AddAnnotCommand) and command.text() == "Add stamp"
    (info,) = _annots(w)
    assert info.kind is AnnotKind.STAMP and info.text == "4"
    cx, cy = STAMP_CENTRE["4"]
    centre = QPointF(info.rect.left() + cx * info.font_size, info.rect.top() + cy * info.font_size)
    assert centre.x() == pytest.approx((x0 + x1) / 2, abs=0.5)
    assert centre.y() == pytest.approx((y0 + y1) / 2, abs=0.5)


def test_stamp_alt_click_ignores_snapping(qtbot, window, word_form_pdf, settings) -> None:
    w = window
    assert w.open_file(str(word_form_pdf))
    w.act_stamp_cross.trigger()
    x0, y0, _x1, _y1 = fixtures.WORD_SHAPES["checkbox_12"]
    click = QPointF(x0 + 2.5, y0 + 2.5)
    _click(qtbot, w, click, modifier=ALT)
    (info,) = _annots(w)
    assert info.text == "8"
    cx, cy = STAMP_CENTRE["8"]
    centre = QPointF(info.rect.left() + cx * info.font_size, info.rect.top() + cy * info.font_size)
    tol = 1.0 / w.page_view.view_scale + 0.1
    assert centre.x() == pytest.approx(click.x(), abs=tol)
    assert centre.y() == pytest.approx(click.y(), abs=tol)
    assert info.rect.width() == pytest.approx(settings.stamp_size)


def test_stamp_click_on_annotation_selects(qtbot, window, word_form_pdf) -> None:
    w = window
    assert w.open_file(str(word_form_pdf))
    w.act_stamp_dot.trigger()
    _click(qtbot, w, EMPTY)
    (info,) = _annots(w)
    assert info.text == "l"
    w.document_view.annot_selection.clear()
    _click(qtbot, w, info.rect.center())
    assert w.undo_stack.count() == 1
    assert w.document_view.annot_selection.current.name == info.name


def test_resized_rect_keeps_stamps_square() -> None:
    rect = QRectF(100, 100, 12, 12)
    r = resized_rect(rect, Handle.BOTTOM_RIGHT, QPointF(6, 2), square=True)
    assert (r.left(), r.top(), r.width(), r.height()) == (100, 100, 18, 18)
    r = resized_rect(rect, Handle.LEFT, QPointF(-4, 0), square=True)
    assert r.width() == r.height() == 16 and r.right() == 112 and r.center().y() == 106
    r = resized_rect(rect, Handle.TOP_LEFT, QPointF(50, 50), square=False)
    assert r.width() == r.height() == 4  # never flips


# -- form fields ------------------------------------------------------------------------
def test_click_in_form_field_creates_nothing_unless_alt(qtbot, window, lo_form_pdf) -> None:
    w = window
    assert w.open_file(str(lo_form_pdf))
    assert w.tool_manager.active_tool is w.form_tool
    w.act_text_tool.trigger()
    messages = _messages(w.text_tool)
    doc = w.document_view.document
    field = next(x for x in doc.widgets(0) if x.name == "Zone de texte 8_54")
    _click(qtbot, w, field.rect.center())
    assert not w.document_view.annot_editor.is_open
    assert not w.document_view.field_editor.is_open
    assert messages == [form_field_message()]
    assert w.statusBar().currentMessage() == form_field_message()
    assert w.undo_stack.count() == 0
    _click(qtbot, w, field.rect.center(), modifier=ALT)
    assert w.document_view.annot_editor.is_open
    qtbot.keyClicks(_editor(w), "over")
    qtbot.keyClick(_editor(w), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert w.undo_stack.count() == 1
    assert [a.text for a in _annots(w)] == ["over"]
    assert len(messages) == 1


# -- wiring ---------------------------------------------------------------------------
def test_push_commits_open_annot_editor_first(qtbot, word_window) -> None:
    w = word_window
    _click(qtbot, w, EMPTY)
    qtbot.keyClicks(_editor(w), "abc")
    doc = w.document_view.document
    w.document_view.push(RotatePageCommand(doc, 0, 90))
    assert w.undo_stack.count() == 2
    assert isinstance(w.undo_stack.command(0), AddAnnotCommand)
    assert isinstance(w.undo_stack.command(1), RotatePageCommand)
    assert not w.document_view.annot_editor.is_open


def test_digit_in_page_spinbox_does_not_switch_tool(qtbot, window, many_pages_pdf) -> None:
    w = window
    assert w.open_file(str(many_pages_pdf))
    assert w.tool_manager.active_tool.name == "hand"
    w.page_spin.setFocus()
    w.page_spin.lineEdit().selectAll()
    qtbot.keyClick(w.page_spin.lineEdit(), Qt.Key.Key_1)
    qtbot.keyClick(w.page_spin.lineEdit(), Qt.Key.Key_2)
    qtbot.keyClick(w.page_spin.lineEdit(), Qt.Key.Key_T)
    assert w.tool_manager.active_tool.name == "hand"
    assert w.page_spin.lineEdit().text().startswith("12")
    # Positive control: the same keys switch tools when the page view has the focus.
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_1)
    assert w.tool_manager.active_tool.name == "stamp_check"
    qtbot.keyClick(w.page_view, Qt.Key.Key_3)
    assert w.tool_manager.active_tool.name == "stamp_dot"
    qtbot.keyClick(w.page_view, Qt.Key.Key_T)
    assert w.tool_manager.active_tool is w.text_tool
    qtbot.keyClick(w.page_view, Qt.Key.Key_H)
    assert w.tool_manager.active_tool.name == "hand"


def test_tool_change_commits_editor_and_clears_selection(qtbot, word_window) -> None:
    w = word_window
    _click(qtbot, w, EMPTY)
    qtbot.keyClicks(_editor(w), "abc")
    w.act_stamp_check.trigger()
    assert w.undo_stack.count() == 1
    assert not w.document_view.annot_editor.is_open
    (info,) = _annots(w)
    w.stamp_tools["check"].selection.select(info)
    assert w.act_delete_annot.isEnabled()
    w.act_hand_tool.trigger()
    assert w.document_view.annot_selection.current is None
    assert not w.act_delete_annot.isEnabled()


def test_select_existing_annotation_and_apply_style(qtbot, window, annotated_pdf) -> None:
    w = window
    assert w.open_file(str(annotated_pdf))
    w.act_text_tool.trigger()
    x0, y0, x1, y1 = fixtures.ANNOT_TEXT_RECT
    _click(qtbot, w, QPointF((x0 + x1) / 2, (y0 + y1) / 2))
    current = w.document_view.annot_selection.current
    assert current is not None and current.name == fixtures.ANNOT_TEXT_NAME
    w.text_tool.apply_style(color=(1.0, 0.0, 0.0))
    assert w.undo_stack.count() == 1
    assert w.undo_stack.command(0).text() == "Change text style"
    assert w.document_view.document.annot(0, current.name).color == (1.0, 0.0, 0.0)
    assert w.settings.annot_color == "#ff0000"


def test_actions_disabled_without_document_or_permission(qtbot, window, owner_locked_pdf):
    w = window
    actions = (w.act_text_tool, w.act_stamp_check, w.act_stamp_cross, w.act_stamp_dot)
    assert not any(a.isEnabled() for a in actions)
    assert not w.act_delete_annot.isEnabled()
    assert w.act_text_tool.toolTip() == "Text (T)"
    assert w.act_stamp_check.shortcut().toString() == "1"
    assert w.act_hand_tool.shortcut().toString() == "H"
    assert w.act_form_tool.shortcut().toString() == "F"
    assert not w.act_text_tool.icon().isNull() and not w.act_stamp_dot.icon().isNull()
    assert w.open_file(str(owner_locked_pdf))
    assert not w.document_view.document.can_annotate
    assert not any(a.isEnabled() for a in actions)
    assert w.statusBar().currentMessage() == NOT_PERMITTED
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_T)
    assert w.tool_manager.active_tool.name == "hand"


def test_settings_defaults(settings) -> None:
    assert settings.annot_font_size == 11.0
    assert settings.annot_color == "#000000"
    assert settings.stamp_size == 12.0
    settings.annot_font_size = 14
    settings.stamp_size = 0  # invalid: default
    assert settings.annot_font_size == 14.0 and settings.stamp_size == 12.0


# -- M3-T6: text style toolbar widgets ---------------------------------------------------


def _select_text(qtbot, w: MainWindow, annotated_pdf):
    assert w.open_file(str(annotated_pdf))
    w.act_text_tool.trigger()
    x0, y0, x1, y1 = fixtures.ANNOT_TEXT_RECT
    _click(qtbot, w, QPointF((x0 + x1) / 2, (y0 + y1) / 2))
    current = w.document_view.annot_selection.current
    assert current is not None and current.name == fixtures.ANNOT_TEXT_NAME
    return current


def test_style_widgets_in_toolbar_and_menu(window) -> None:
    w = window
    edit = w.menu_edit.actions()
    tools = [w.act_hand_tool, w.act_form_tool, *w._annot_actions()]
    assert all(a in edit for a in (*tools, w.act_delete_annot))
    assert edit.index(w.act_text_tool) < edit.index(w.act_delete_annot)
    assert all(a in w.toolbar.actions() for a in tools)
    assert w.act_delete_annot.shortcut() == QKeySequence(QKeySequence.StandardKey.Delete)
    assert w.act_text_tool.shortcut().toString() == "T"
    assert w.act_stamp_dot.shortcut().toString() == "3"
    spin, button = w.font_size_spin, w.color_button
    assert w.toolbar.isAncestorOf(spin) and w.toolbar.isAncestorOf(button)
    assert (spin.minimum(), spin.maximum(), spin.suffix()) == (6, 72, " pt")
    assert spin.toolTip() == "Font size" and spin.accessibleName() == "Font size"
    assert button.toolTip() == "Text color" and not button.icon().isNull()
    assert spin.value() == 11 and w.text_color.name() == "#000000"


def test_style_widgets_initialised_from_settings(qtbot, settings) -> None:
    settings.annot_font_size = 16
    settings.annot_color = "#0000ff"
    w = MainWindow(settings)
    qtbot.addWidget(w)
    assert w.font_size_spin.value() == 16
    assert w.text_color.name() == "#0000ff"


def test_font_size_spin_edits_selection_and_persists(
    qtbot, window, annotated_pdf, ini_path
) -> None:
    w = window
    current = _select_text(qtbot, w, annotated_pdf)
    assert w.font_size_spin.value() == 11
    w.font_size_spin.setValue(20)
    assert w.undo_stack.count() == 1
    cmd = w.undo_stack.command(0)
    assert isinstance(cmd, EditAnnotCommand) and cmd.text() == "Change text style"
    edited = w.document_view.document.annot(0, current.name)
    assert edited.font_size == 20
    assert edited.rect.height() > current.rect.height()  # refitted
    w.settings.qsettings.sync()
    assert Settings(QSettings(str(ini_path), QSettings.Format.IniFormat)).annot_font_size == 20


def test_color_button_edits_selection_and_persists(
    qtbot, window, annotated_pdf, ini_path, monkeypatch
) -> None:
    w = window
    current = _select_text(qtbot, w, annotated_pdf)
    asked: list[str] = []

    def fake_get_color(parent, initial):
        asked.append(initial.name())
        return QColor("#ff0000")

    monkeypatch.setattr(dialogs, "get_color", fake_get_color)
    qtbot.mouseClick(w.color_button, LEFT)
    assert asked == ["#000000"]
    assert w.undo_stack.count() == 1
    assert isinstance(w.undo_stack.command(0), EditAnnotCommand)
    assert w.document_view.document.annot(0, current.name).color == (1.0, 0.0, 0.0)
    assert w.text_color.name() == "#ff0000"
    w.settings.qsettings.sync()
    assert Settings(QSettings(str(ini_path), QSettings.Format.IniFormat)).annot_color == "#ff0000"
    monkeypatch.setattr(dialogs, "get_color", lambda parent, initial: None)  # cancelled
    qtbot.mouseClick(w.color_button, LEFT)
    assert w.undo_stack.count() == 1


def test_style_widgets_follow_selection(qtbot, window, annotated_pdf) -> None:
    w = window
    w.settings.annot_font_size = 30
    w.settings.annot_color = "#00ff00"
    _select_text(qtbot, w, annotated_pdf)
    assert w.font_size_spin.value() == 11 and w.text_color.name() == "#000000"
    w.document_view.annot_selection.clear()
    assert w.font_size_spin.value() == 30 and w.text_color.name() == "#00ff00"
    assert w.undo_stack.count() == 0


def test_style_without_annot_tool_only_updates_defaults(
    qtbot, window, annotated_pdf, monkeypatch
) -> None:
    w = window
    assert w.open_file(str(annotated_pdf))
    assert w.tool_manager.active_tool.name == "form"  # annotated_pdf has a widget
    w.font_size_spin.setValue(9)
    monkeypatch.setattr(dialogs, "get_color", lambda parent, initial: QColor("#123456"))
    w.color_button.click()
    assert w.undo_stack.count() == 0
    assert w.settings.annot_font_size == 9 and w.settings.annot_color == "#123456"


def test_style_widgets_disabled_without_document_or_permission(
    window, simple_pdf, owner_locked_pdf
) -> None:
    w = window
    assert not w.font_size_spin.isEnabled() and not w.color_button.isEnabled()
    assert w.open_file(str(simple_pdf))
    assert w.font_size_spin.isEnabled() and w.color_button.isEnabled()
    assert w.open_file(str(owner_locked_pdf))
    assert not w.font_size_spin.isEnabled() and not w.color_button.isEnabled()
