"""M6b-T13: MainWindow wiring of the Select Text and markup tools, Copy Text, the colour
button for markups, settings and translations."""

from __future__ import annotations

import fixtures
import pytest
from fixtures import MARKED_HIGHLIGHT_NAME, TEXT_LINES
from PySide6.QtCore import QPoint, QPointF, QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import QApplication, QMessageBox

from pdfeditor.core.annotations import AnnotKind
from pdfeditor.core.settings import MARKUP_COLOR_DEFAULTS, Settings
from pdfeditor.i18n import install_translators, remove_translators
from pdfeditor.ui import dialogs
from pdfeditor.ui import main_window as mw_module
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.tools.markup_tools import MarkupTool, TextSelectTool

NO_MOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
LINE_1 = TEXT_LINES[0][0]
QUICK = LINE_1.index("quick")
BROWN_END = LINE_1.index("brown") + len("brown") - 1


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: None)
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Discard
    )
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.resize(1000, 700)
    w.show()
    qtbot.waitExposed(w)
    w.activateWindow()
    yield w
    w.undo_stack.setClean()
    w.close()


@pytest.fixture
def text_window(window, tmp_path):
    assert window.open_file(str(fixtures.make_text_pdf(tmp_path / "text.pdf")))
    return window


def _vp(w: MainWindow, point: QPointF, page: int = 0) -> QPoint:
    pv = w.page_view
    scene = pv.page_item(page).mapToScene(point)
    pv.ensureVisible(QRectF(scene.x() - 1, scene.y() - 1, 2, 2), 60, 60)
    return pv.mapFromScene(pv.page_item(page).mapToScene(point))


def _char(w: MainWindow, index: int) -> QPointF:
    return w.document_view.document.page_text(0).chars[index].bbox.center()


def _drag(qtbot, w: MainWindow, a: QPointF, b: QPointF) -> None:
    vp = w.page_view.viewport()
    start, end = _vp(w, a), _vp(w, b)
    qtbot.mousePress(vp, LEFT, NO_MOD, start)
    for k in (1, 2, 3):
        qtbot.mouseMove(vp, start + (end - start) * k / 3)
    qtbot.mouseRelease(vp, LEFT, NO_MOD, end)


def _markup_actions(w: MainWindow):
    return (w.act_highlight, w.act_underline, w.act_strikeout)


# -- actions -------------------------------------------------------------------------------
def test_actions_shortcuts_icons_menus_and_toolbar(window) -> None:
    w = window
    expected = {
        w.act_select_text: ("Select Text", "Shift+T", "select_text"),
        w.act_highlight: ("Highlight", "Shift+H", "highlight"),
        w.act_underline: ("Underline", "Shift+U", "underline"),
        w.act_strikeout: ("Strike Through", "Shift+S", "strikeout"),
    }
    edit = mw_module.menu_actions(w.menu_edit)
    toolbar = w.toolbar.actions()
    for act, (text, keys, tool) in expected.items():
        assert mw_module.strip_mnemonic(act.text()) == text
        assert act.shortcut() == QKeySequence(keys)
        assert not act.icon().isNull()
        assert act.isCheckable()
        assert act in edit and act in toolbar
        assert w.tool_manager.actions[tool] is act
    assert w.act_copy_text.shortcuts() == [QKeySequence("Ctrl+C")]
    assert w.act_copy_text in edit
    assert isinstance(w.select_text_tool, TextSelectTool)
    assert set(w.markup_tools) == {AnnotKind.HIGHLIGHT, AnnotKind.UNDERLINE, AnnotKind.STRIKEOUT}
    assert all(isinstance(t, MarkupTool) for t in w.markup_tools.values())
    # E belongs to M7's Edit Page Text tool only.
    owners = [
        a for a in w.findChildren(type(w.act_copy_text)) if QKeySequence("E") in a.shortcuts()
    ]
    assert owners == [w.act_textedit_tool]


def test_enablement_without_document_and_with_permissions(window, tmp_path, owner_locked_pdf):
    w = window
    for act in (w.act_select_text, *_markup_actions(w), w.act_copy_text):
        assert not act.isEnabled()
    assert w.open_file(str(fixtures.make_text_pdf(tmp_path / "text.pdf")))
    assert w.act_select_text.isEnabled()
    assert all(a.isEnabled() for a in _markup_actions(w))
    assert not w.act_copy_text.isEnabled()  # nothing selected
    assert w.open_file(str(owner_locked_pdf))  # no annotations, no copying
    assert w.act_select_text.isEnabled()
    assert not any(a.isEnabled() for a in _markup_actions(w))
    doc = w.document_view.document
    assert not doc.can_annotate and not doc.can_extract
    w.act_select_text.trigger()
    w.document_view.text_selection.set(0, 0, 3)
    assert w.act_copy_text.isEnabled()  # enabled, so that Ctrl+C explains (M6b review)
    messages = []
    w.statusBar().messageChanged.connect(messages.append)
    assert not w.copy_text()
    assert messages[-1] == ("Copying text is not permitted by this document’s security settings.")


def test_shortcut_keys_switch_tools(qtbot, text_window) -> None:
    w = text_window
    w.page_view.setFocus()
    for key, tool in (
        (Qt.Key.Key_T, "select_text"),
        (Qt.Key.Key_H, "highlight"),
        (Qt.Key.Key_U, "underline"),
        (Qt.Key.Key_S, "strikeout"),
    ):
        qtbot.keyClick(w.page_view, key, Qt.KeyboardModifier.ShiftModifier)
        assert w.tool_manager.active_tool.name == tool
    qtbot.keyClick(w.page_view, Qt.Key.Key_H)
    assert w.tool_manager.active_tool.name == "hand"


# -- copy ----------------------------------------------------------------------------------
def test_copy_text_of_selection_and_markup(qtbot, text_window) -> None:
    w = text_window
    clipboard = QApplication.clipboard()
    clipboard.setText("before")
    w.act_select_text.trigger()
    _drag(qtbot, w, _char(w, QUICK), _char(w, BROWN_END))
    assert w.act_copy_text.isEnabled()
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert clipboard.text() == "quick brown"
    assert w.statusBar().currentMessage() == "Text copied"
    w.act_highlight.trigger()
    assert not w.act_copy_text.isEnabled()  # the selection went with the tool
    _drag(qtbot, w, _char(w, LINE_1.index("lazy")), _char(w, LINE_1.index("dog") + 2))
    assert w.document_view.annot_selection.current.kind is AnnotKind.HIGHLIGHT
    assert w.act_copy_text.isEnabled()
    w.act_copy_text.trigger()
    assert clipboard.text() == "lazy dog"
    w.document_view.annot_selection.clear()
    assert not w.act_copy_text.isEnabled()
    assert not w.copy_text()


def test_delete_action_deletes_selected_markup(qtbot, window, tmp_path) -> None:
    w = window
    assert w.open_file(str(fixtures.make_marked_pdf(tmp_path / "marked.pdf")))
    w.act_underline.trigger()
    qtbot.mouseClick(w.page_view.viewport(), LEFT, NO_MOD, _vp(w, QPointF(120, 96)))
    assert w.document_view.annot_selection.current.name == MARKED_HIGHLIGHT_NAME
    assert w.act_delete_annot.isEnabled()
    w.act_delete_annot.trigger()
    doc = w.document_view.document
    assert all(a.name != MARKED_HIGHLIGHT_NAME for a in doc.annots(0))
    assert w.isWindowModified()
    w.undo()
    assert doc.annot(0, MARKED_HIGHLIGHT_NAME) is not None


# -- colour button -------------------------------------------------------------------------
def test_color_button_follows_markup_tool_and_selection(qtbot, text_window, monkeypatch) -> None:
    w = text_window
    s = w.settings
    s.annot_color = "#000080"
    w.act_text_tool.trigger()
    assert w.color_button.toolTip() == "Text color"
    assert w.text_color == QColor("#000080")
    assert w.font_size_spin.isEnabled()
    w.act_highlight.trigger()
    assert w.color_button.toolTip() == "Highlight color"
    assert w.text_color == QColor("#ffff00")
    assert not w.font_size_spin.isEnabled()  # inert for markups
    assert w.color_button.isEnabled()
    w.act_underline.trigger()
    assert w.color_button.toolTip() == "Underline color"
    assert w.text_color == QColor("#ff0000")
    monkeypatch.setattr(dialogs, "get_color", lambda parent, initial: QColor("#00aa00"))
    w.choose_text_color()
    assert s.markup_underline_color == "#00aa00"
    assert s.annot_color == "#000080"
    _drag(qtbot, w, _char(w, QUICK), _char(w, BROWN_END))
    info = w.document_view.annot_selection.current
    assert info.kind is AnnotKind.UNDERLINE
    assert QColor.fromRgbF(*info.color).name() == "#00aa00"
    # Recolour the selection from the button: one undo step.
    monkeypatch.setattr(dialogs, "get_color", lambda parent, initial: QColor("#0000ff"))
    w.choose_text_color()
    assert w.undo_stack.count() == 2
    assert w.undo_stack.command(1).text() == "Change markup color"
    assert w.text_color == QColor("#0000ff")
    # Another tool clears the selection and shows its own default.
    w.act_text_tool.trigger()
    assert w.color_button.toolTip() == "Text color"
    assert w.text_color == QColor("#000080")
    # A markup selected with the text tool shows the markup's colour; no font size.
    qtbot.mouseClick(w.page_view.viewport(), LEFT, NO_MOD, _vp(w, _char(w, QUICK + 2)))
    assert w.document_view.annot_selection.current.kind is AnnotKind.UNDERLINE
    assert w.color_button.toolTip() == "Underline color"
    assert w.text_color == QColor("#0000ff")
    assert not w.font_size_spin.isEnabled()
    w.act_strikeout.trigger()
    assert w.text_color == QColor("#ff0000")
    w.act_select_text.trigger()
    assert w.color_button.toolTip() == "Text color"


# -- settings ------------------------------------------------------------------------------
def test_markup_color_settings_round_trip(ini_path) -> None:
    s = Settings(QSettings(str(ini_path), QSettings.Format.IniFormat))
    assert s.markup_highlight_color == "#ffff00"
    assert s.markup_underline_color == "#ff0000"
    assert s.markup_strikeout_color == "#ff0000"
    assert {k: s.markup_color(k) for k in MARKUP_COLOR_DEFAULTS} == MARKUP_COLOR_DEFAULTS
    s.markup_highlight_color = "#00ff00"
    s.set_markup_color("underline", "#123456")
    s.markup_strikeout_color = "#abcdef"
    s.sync()
    s2 = Settings(QSettings(str(ini_path), QSettings.Format.IniFormat))
    assert s2.markup_highlight_color == "#00ff00"
    assert s2.markup_color("underline") == "#123456"
    assert s2.markup_strikeout_color == "#abcdef"
    s2.markup_highlight_color = "not a colour"
    assert s2.markup_highlight_color == "#ffff00"
    with pytest.raises(KeyError):
        s2.markup_color("squiggly")
    with pytest.raises(KeyError):
        s2.set_markup_color("text", "#000000")


# -- shortcuts dialog and translations -------------------------------------------------------
def test_shortcuts_dialog_lists_text_tools(window) -> None:
    sections = dict(window.shortcut_sections())
    edit = dict(sections["Edit"])
    native = QKeySequence.SequenceFormat.NativeText
    assert edit["Select Text"] == QKeySequence("Shift+T").toString(native)
    assert edit["Highlight"] == QKeySequence("Shift+H").toString(native)
    assert edit["Underline"] == QKeySequence("Shift+U").toString(native)
    assert edit["Strike Through"] == QKeySequence("Shift+S").toString(native)
    assert edit["Copy Text"] == QKeySequence("Ctrl+C").toString(native)
    interaction = dict(sections["Pointer and editing"])
    assert interaction["Select a word / a line"] == "Double-click / Triple-click"
    assert interaction["Extend the text selection"] == "Shift+Click"


def test_french_strings(qtbot, qapp, settings) -> None:
    install_translators(qapp, "fr")
    try:
        w = MainWindow(settings)
        qtbot.addWidget(w)
        assert w.act_select_text.text() == "Sélectionner le te&xte"
        assert w.act_highlight.text() == "Su&rligner"
        assert w.act_underline.text() == "Sou&ligner"
        assert w.act_strikeout.text() == "&Barrer"
        assert w.act_copy_text.text() == "&Copier le texte"
        assert w.act_highlight.toolTip() == "Surligner (Maj+H)"
        w.tool_manager.set_active("highlight")
        assert w.color_button.toolTip() == "Couleur de surlignage"
        interaction = dict(w.shortcut_sections()[-1][1])
        assert interaction["Sélectionner un mot / une ligne"] == "Double-clic / Triple-clic"
        w.close()
    finally:
        remove_translators(qapp)
