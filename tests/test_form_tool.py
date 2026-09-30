from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QLineEdit, QMessageBox

import pdfeditor.i18n as i18n
from pdfeditor.core.forms import FieldKind
from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.tools.base import ToolEvent

ENCODING_MESSAGE = (
    "Some characters cannot be displayed with this form’s font; they are stored but may not print."
)


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: None)
    # pytest-qt closes the window before this fixture's teardown: never block on a prompt.
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
def form_window(window, lo_form_pdf):
    assert window.open_file(str(lo_form_pdf))
    return window


def _info(window: MainWindow, name: str, page: int = 0, index: int = 0):
    doc = window.document_view.document
    return [w for w in doc.widgets(page) if w.name == name][index]


def _click(qtbot, window: MainWindow, info) -> None:
    pv = window.page_view
    pv.ensureVisible(pv.page_item(info.page).mapRectToScene(info.rect), 40, 40)
    pos = pv.page_rect_to_viewport(info.page, info.rect).center()
    qtbot.mouseClick(pv.viewport(), Qt.MouseButton.LeftButton, pos=pos)


def _press(window: MainWindow, page: int, x: float, y: float) -> bool:
    page_pos = QPointF(x, y)
    event = ToolEvent(
        page, page_pos, page_pos, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, None
    )
    return window.form_tool.mouse_press(event)


def _messages(window: MainWindow) -> list[str]:
    messages: list[str] = []
    window.form_tool.message.connect(messages.append)
    return messages


def test_form_opens_with_form_tool(form_window) -> None:
    w = form_window
    assert w.tool_manager.active_tool is w.form_tool
    assert w.act_form_tool.isEnabled() and w.act_form_tool.isChecked()
    assert w.act_auto_shrink.isChecked()


def test_plain_pdf_uses_hand_tool(window, simple_pdf, lo_form_pdf) -> None:
    assert window.open_file(str(lo_form_pdf))
    assert window.open_file(str(simple_pdf))
    assert window.tool_manager.active_tool.name == "hand"
    assert not window.act_form_tool.isEnabled()


def test_owner_locked_disables_form_tool(window, owner_locked_pdf) -> None:
    assert window.open_file(str(owner_locked_pdf))
    assert not window.act_form_tool.isEnabled()
    assert window.tool_manager.active_tool.name == "hand"


def test_dynamic_xfa_disables_form_tool(window, dynamic_xfa_pdf) -> None:
    assert window.open_file(str(dynamic_xfa_pdf))
    assert not window.act_form_tool.isEnabled()
    assert window.tool_manager.active_tool.name == "hand"


def test_click_checkbox_pushes_one_command(qtbot, form_window) -> None:
    w = form_window
    info = _info(w, "Case à cocher 1_2")
    assert not info.is_on
    _click(qtbot, w, info)
    assert w.undo_stack.count() == 1
    assert w.act_undo.text() == "Undo Edit form field"
    assert _info(w, "Case à cocher 1_2").is_on
    assert w.isWindowModified()
    assert w.form_tool.focused_button is not None
    w.act_undo.trigger()
    assert not _info(w, "Case à cocher 1_2").is_on
    assert not w.isWindowModified()


def test_space_toggles_focused_checkbox(qtbot, form_window) -> None:
    w = form_window
    _click(qtbot, w, _info(w, "Case à cocher 1_3"))
    assert _info(w, "Case à cocher 1_3").is_on
    qtbot.keyClick(w.page_view, Qt.Key.Key_Space)
    assert not _info(w, "Case à cocher 1_3").is_on
    assert w.undo_stack.count() == 2


def test_french_undo_text(qtbot, qapp, settings, lo_form_pdf) -> None:
    i18n.install_translators(qapp, "fr")
    try:
        w = MainWindow(settings)  # the undo prefix is translated at construction
        qtbot.addWidget(w)
        w.resize(900, 700)
        w.show()
        qtbot.waitExposed(w)
        assert w.open_file(str(lo_form_pdf))
        assert w.act_form_tool.text() == "Outil &formulaire"
        assert w.act_auto_shrink.text() == "Réduire automatiquement le texte trop long"
        _click(qtbot, w, _info(w, "Case à cocher 1_2"))
        assert w.act_undo.text() == "Annuler la modification du champ"
        w.undo_stack.setClean()
        w.close()
    finally:
        i18n.remove_translators(qapp)


def test_radio_selects_then_turns_off(qtbot, form_window) -> None:
    w = form_window
    first, second = _info(w, "Sexe"), _info(w, "Sexe", index=1)
    _click(qtbot, w, first)
    assert _info(w, "Sexe").is_on and not _info(w, "Sexe", index=1).is_on
    _click(qtbot, w, second)
    assert not _info(w, "Sexe").is_on and _info(w, "Sexe", index=1).is_on
    _click(qtbot, w, second)  # NoToggleToOff unset: clicking the selected button clears it
    assert not _info(w, "Sexe", index=1).is_on
    assert w.undo_stack.count() == 3


def test_radio_no_toggle_to_off(form_window) -> None:
    w = form_window
    info = dataclasses.replace(_info(w, "Sexe"), value="M", flags=_info(w, "Sexe").flags | 1 << 14)
    w.form_tool.toggle(info)
    assert w.undo_stack.count() == 0


def test_click_text_opens_editor(qtbot, form_window) -> None:
    w = form_window
    info = _info(w, "Zone de texte 8_54")
    _click(qtbot, w, info)
    editor = w.document_view.field_editor
    assert editor.is_open
    assert editor.current_info.name == "Zone de texte 8_54"
    assert isinstance(editor.editor, QLineEdit)
    assert w.undo_stack.count() == 0


def test_hover_cursor(form_window) -> None:
    w = form_window
    info = _info(w, "Case à cocher 1_2")
    center = info.rect.center()
    event = ToolEvent(
        0, center, center, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, None
    )
    assert w.form_tool.mouse_move(event) is False
    assert w.page_view.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor
    away = QPointF(20, 700)
    event = ToolEvent(0, away, away, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, None)
    assert w.form_tool.mouse_move(event) is False
    assert w.page_view.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor


def test_click_empty_area_falls_through(form_window) -> None:
    w = form_window
    w.form_tool.focus(_info(w, "Zone de texte 8_54"))
    w.document_view.field_editor.editor.setText("abc")
    assert _press(w, 0, 20, 700) is False
    assert not w.document_view.field_editor.is_open
    assert w.undo_stack.count() == 1  # the pending edit was committed
    assert _press(w, None, 0, 0) is False


def test_read_only_and_hidden_never_open(form_window) -> None:
    w = form_window
    doc = w.document_view.document
    for name in ("Zone de texte lecture", "Zone masquée"):
        info = next(x for x in doc.widgets(0) if x.name == name)
        center = info.rect.center()
        assert _press(w, 0, center.x(), center.y()) is False
        assert not w.document_view.field_editor.is_open
    assert w.undo_stack.count() == 0


def test_tab_moves_to_geometric_next(qtbot, form_window) -> None:
    w = form_window
    editor = w.document_view.field_editor
    w.form_tool.focus(_info(w, "Zone de texte 8_54"))
    qtbot.waitUntil(editor.editor.hasFocus)
    qtbot.keyClick(editor.editor, Qt.Key.Key_Tab)
    assert editor.current_info.name == "Zone de texte 8_55"
    qtbot.keyClick(editor.editor, Qt.Key.Key_Backtab)
    assert editor.current_info.name == "Zone de texte 8_54"


def test_tab_crosses_to_next_page(qtbot, form_window) -> None:
    w = form_window
    editor = w.document_view.field_editor
    w.form_tool.focus(_info(w, "Couleur"))  # last field of page 1
    qtbot.waitUntil(editor.editor.hasFocus)
    assert w.page_view.current_page == 0
    with qtbot.waitSignal(
        w.page_view.current_page_changed, check_params_cb=lambda i: i == 1, timeout=1000
    ):
        qtbot.keyClick(editor.editor, Qt.Key.Key_Tab)
    info = editor.current_info
    assert (info.page, info.name) == (1, "Nom")
    # Next: the page 2 checkbox gets the keyboard focus, then Tab wraps to page 1.
    qtbot.keyClick(editor.editor, Qt.Key.Key_Tab)
    assert not editor.is_open
    assert w.form_tool.focused_button.name == "Case à cocher 2_1"
    qtbot.keyClick(w.page_view, Qt.Key.Key_Tab)
    assert editor.current_info.page == 0 and editor.current_info.name == "Nom"


def test_tab_without_editor_focuses_first_field(qtbot, form_window) -> None:
    w = form_window
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_Tab)
    editor = w.document_view.field_editor
    assert editor.is_open and editor.current_info.name == "Nom"
    editor.cancel()
    qtbot.keyClick(w.page_view, Qt.Key.Key_Backtab)
    # Last field of page 1 in reading order: the list box.
    assert editor.is_open and editor.current_info.kind is FieldKind.LIST


def test_maybe_save_commits_pending_edit_first(form_window, monkeypatch) -> None:
    w = form_window
    seen: list[bool] = []

    def confirm(_parent, _name):
        seen.append(w.document_view.is_dirty)
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(dialogs, "confirm_save_changes", confirm)
    w.form_tool.focus(_info(w, "Zone de texte 8_54"))
    w.document_view.field_editor.editor.setText("pending")
    assert w.close_document()
    assert seen == [True]
    assert w.document_view.document is None


def test_save_commits_pending_edit(form_window, lo_form_pdf) -> None:
    w = form_window
    w.form_tool.focus(_info(w, "Zone de texte 8_54"))
    w.document_view.field_editor.editor.setText("saved value")
    assert w.save()
    assert not w.isWindowModified()
    assert _info(w, "Zone de texte 8_54").value == "saved value"
    assert w.undo_stack.count() == 1


def test_undo_after_save_redirties(qtbot, form_window) -> None:
    w = form_window
    _click(qtbot, w, _info(w, "Case à cocher 1_2"))
    assert w.save()
    assert not w.isWindowModified()
    w.act_undo.trigger()
    assert w.isWindowModified()
    assert not _info(w, "Case à cocher 1_2").is_on


def test_switching_documents_with_open_editor(form_window, simple_pdf, monkeypatch) -> None:
    w = form_window
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Discard
    )
    w.form_tool.focus(_info(w, "Zone de texte 8_54"))
    w.document_view.field_editor.editor.setText("lost")
    assert w.open_file(str(simple_pdf))
    assert not w.isWindowModified()
    assert w.undo_stack.count() == 0
    assert not w.document_view.field_editor.is_open


def _commit(w: MainWindow, name: str, text: str) -> None:
    w.form_tool.focus(_info(w, name))
    w.document_view.field_editor.editor.setText(text)
    assert w.document_view.field_editor.commit()


def test_auto_shrink_sets_font_size_zero(form_window) -> None:
    w = form_window
    info = _info(w, "Zone de texte 8_55")
    assert info.font_size == 8 and info.max_len == 0
    _commit(w, "Zone de texte 8_55", "A very long value " * 6)
    command = w.undo_stack.command(0)
    assert command.font_size == 0
    assert _info(w, "Zone de texte 8_55").font_size == 0
    w.act_undo.trigger()
    assert _info(w, "Zone de texte 8_55").font_size == 8


def test_short_text_keeps_font_size(form_window) -> None:
    w = form_window
    _commit(w, "Zone de texte 8_55", "short")
    assert w.undo_stack.command(0).font_size is None


def test_auto_shrink_setting_off(form_window) -> None:
    w = form_window
    w.act_auto_shrink.trigger()
    assert w.settings.auto_shrink_text is False
    _commit(w, "Zone de texte 8_55", "A very long value " * 6)
    assert w.undo_stack.command(0).font_size is None
    assert _info(w, "Zone de texte 8_55").font_size == 8


def test_non_cp1252_characters_message(form_window) -> None:
    w = form_window
    messages = _messages(w)
    _commit(w, "Zone de texte 8_54", "déjà vu €")
    assert messages == []
    _commit(w, "Zone de texte 8_54", "Ω")
    assert messages == [ENCODING_MESSAGE]
    assert _info(w, "Zone de texte 8_54").value == "Ω"
    assert w.statusBar().currentMessage() == ENCODING_MESSAGE


def test_vanished_field_reports_and_pushes_nothing(form_window) -> None:
    w = form_window
    messages = _messages(w)
    ghost = dataclasses.replace(_info(w, "Zone de texte 8_54"), xref=99999, name="ghost")
    w.document_view.field_editor.committed.emit(ghost, "x")
    assert w.undo_stack.count() == 0
    assert messages == ["The form field could not be updated."]


def test_switching_to_hand_tool_commits(form_window) -> None:
    w = form_window
    w.form_tool.focus(_info(w, "Zone de texte 8_54"))
    w.document_view.field_editor.editor.setText("abc")
    w.act_hand_tool.trigger()
    assert w.tool_manager.active_tool.name == "hand"
    assert not w.document_view.field_editor.is_open
    assert w.undo_stack.count() == 1
