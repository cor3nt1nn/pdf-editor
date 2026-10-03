"""M7-T7: the Edit Page Text tool and its MainWindow wiring."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QKeySequence, QMouseEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox
from textedit_fixtures import (
    ARIAL_PATH,
    CALIBRI_PATH,
    IMAGE_RECT,
    LINE1,
    TIMES_PATH,
    make_ocr_pdf,
    make_text_edit_pdf,
    needs_text_fonts,
)

from pdfeditor.core.commands import ReplaceTextCommand
from pdfeditor.core.fontmatch import SystemFonts
from pdfeditor.core.textedit import EditReason, Run
from pdfeditor.i18n import install_translators, remove_translators
from pdfeditor.ui import dialogs
from pdfeditor.ui import main_window as mw_module
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.tools import textedit_tool
from pdfeditor.ui.tools.textedit_tool import TextEditTool

pytestmark = needs_text_fonts

NO_MOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
JEAN = LINE1.index("Jean")


@pytest.fixture(scope="module")
def fonts() -> SystemFonts:
    return SystemFonts.from_paths([CALIBRI_PATH, ARIAL_PATH, TIMES_PATH])


@pytest.fixture
def window(qtbot, settings, monkeypatch, fonts):
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: None)
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Discard
    )
    w = MainWindow(settings)
    w.textedit_tool.fonts = fonts
    qtbot.addWidget(w)
    w.resize(1000, 700)
    w.show()
    qtbot.waitExposed(w)
    w.activateWindow()
    yield w
    w.undo_stack.setClean()
    w.close()


@pytest.fixture
def edit_window(window, tmp_path):
    assert window.open_file(str(make_text_edit_pdf(tmp_path / "edit.pdf")))
    window.act_textedit_tool.trigger()
    assert window.tool_manager.active_tool is window.textedit_tool
    return window


def _norm(s: str) -> str:
    return s.replace("\xa0", " ")


def _page_text(w: MainWindow) -> str:
    return _norm(w.document_view.document.page_text(0).text)


def _edited(w: MainWindow, old: str, new: str) -> bool:
    """The page shows ``new`` instead of ``old`` (the new chars come last in content
    order, on their own line)."""
    text = _page_text(w)
    return old not in text and new in text


def _vp(w: MainWindow, point: QPointF, page: int = 0) -> QPoint:
    pv = w.page_view
    scene = pv.page_item(page).mapToScene(point)
    pv.ensureVisible(QRectF(scene.x() - 1, scene.y() - 1, 2, 2), 60, 60)
    return pv.mapFromScene(pv.page_item(page).mapToScene(point))


def _char(w: MainWindow, index: int) -> QPointF:
    return w.document_view.document.page_text(0).chars[index].bbox.center()


def _click(qtbot, w: MainWindow, point: QPointF, mod=NO_MOD) -> None:
    qtbot.mouseClick(w.page_view.viewport(), LEFT, mod, _vp(w, point))


def _hover(w: MainWindow, pos: QPoint) -> None:
    """A mouse move without buttons over the page view's viewport."""
    vp = w.page_view.viewport()
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(pos),
        QPointF(vp.mapToGlobal(pos)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        NO_MOD,
    )
    QApplication.sendEvent(vp, event)


def _selected(w: MainWindow) -> str:
    return _norm(w.document_view.text_selection.text())


def _messages(w: MainWindow) -> list[str]:
    messages: list[str] = []
    w.statusBar().messageChanged.connect(messages.append)
    return messages


def _open_editor(qtbot, w: MainWindow) -> QLineEdit:
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_Return)
    editor = w.document_view.textedit_editor
    assert editor.is_open
    widget = editor.editor
    assert isinstance(widget, QLineEdit)
    return widget


# -- actions -------------------------------------------------------------------------------
def test_action_menu_toolbar_icon_and_shortcut(window) -> None:
    w = window
    act = w.act_textedit_tool
    assert mw_module.strip_mnemonic(act.text()) == "Edit Page Text"
    assert act.toolTip() == "Edit page text (E)"
    assert act.shortcut() == QKeySequence("E")
    assert not act.icon().isNull()
    assert act.isCheckable()
    assert not act.isEnabled()  # no document
    edit = mw_module.menu_actions(w.menu_edit)
    assert edit.index(act) > edit.index(w.act_signature_tool)
    toolbar = w.toolbar.actions()
    assert act in toolbar
    button_action = next(a for a in toolbar if w.toolbar.widgetForAction(a) is w.signature_button)
    assert toolbar.index(act) == toolbar.index(button_action) + 1
    assert isinstance(w.textedit_tool, TextEditTool)
    assert w.tool_manager.actions["textedit"] is act


def test_e_activates_the_tool(qtbot, window, tmp_path) -> None:
    w = window
    assert w.open_file(str(make_text_edit_pdf(tmp_path / "edit.pdf")))
    assert w.act_textedit_tool.isEnabled()
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_E)
    assert w.tool_manager.active_tool.name == "textedit"
    assert w.act_textedit_tool.isChecked()


def test_disabled_without_modify_permission(window, owner_locked_pdf, monkeypatch, tmp_path):
    w = window
    assert w.open_file(str(owner_locked_pdf))
    assert not w.document_view.document.can_modify
    assert not w.act_textedit_tool.isEnabled()
    assert w.open_file(str(make_text_edit_pdf(tmp_path / "edit.pdf")))
    assert w.act_textedit_tool.isEnabled()
    # Refused at commit time too (the permission notice).
    monkeypatch.setattr(type(w.document_view.document), "can_modify", property(lambda s: False))
    messages = _messages(w)
    assert w.textedit_tool.apply_edit(0, Run(JEAN, JEAN + 3), "Paul") is None
    assert messages[-1] == (
        "Editing page text is not permitted by this document’s security settings."
    )
    assert w.undo_stack.count() == 0


# -- selection -----------------------------------------------------------------------------
def test_click_word_edit_undo_redo(qtbot, edit_window) -> None:
    w = edit_window
    before = _page_text(w)
    _click(qtbot, w, _char(w, JEAN + 1))
    assert _selected(w) == "Jean"
    widget = _open_editor(qtbot, w)
    assert widget.text() == "Jean"
    qtbot.keyClicks(widget, "Pierre")
    qtbot.keyClick(widget, Qt.Key.Key_Return)
    assert not w.document_view.textedit_editor.is_open
    assert w.undo_stack.count() == 1
    assert isinstance(w.undo_stack.command(0), ReplaceTextCommand)
    assert _edited(w, "Jean", "Pierre")
    assert w.isWindowModified()
    assert w.document_view.text_selection.is_empty
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert _page_text(w) == before
    qtbot.keyClick(w.page_view, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert _edited(w, "Jean", "Pierre")


def test_full_save_notice_once_per_document(qtbot, edit_window) -> None:
    w = edit_window
    messages = _messages(w)
    tool = w.textedit_tool
    assert tool.apply_edit(0, Run(JEAN, JEAN + 3), "Paul") is not None
    assert messages[-1] == "After editing page text, the next save rewrites the whole file."
    text = w.document_view.document.page_text(0)
    start = _norm("".join(c.c for c in text.chars)).index("Lyon")
    assert tool.apply_edit(0, Run(start, start + 3), "Nice") is not None
    assert w.undo_stack.count() == 2
    assert all("next save" not in m for m in messages[1:])


def test_second_click_opens_editor_double_click_selects_span(qtbot, edit_window) -> None:
    w = edit_window
    _click(qtbot, w, _char(w, JEAN + 1))
    qtbot.mouseDClick(w.page_view.viewport(), LEFT, NO_MOD, _vp(w, _char(w, JEAN + 1)))
    assert _selected(w) == _norm(LINE1)  # one span: the whole line
    assert not w.document_view.textedit_editor.is_open
    # A later single click on the selection opens the editor.
    qtbot.wait(QApplication.doubleClickInterval() + 50)
    _click(qtbot, w, _char(w, JEAN + 1))
    qtbot.waitUntil(lambda: w.document_view.textedit_editor.is_open)
    assert _norm(w.document_view.textedit_editor.editor.text()) == _norm(LINE1)
    w.document_view.textedit_editor.close()


def test_drag_stays_within_the_line(qtbot, edit_window) -> None:
    w = edit_window
    vp = w.page_view.viewport()
    start = _vp(w, _char(w, JEAN))
    end = _vp(w, _char(w, JEAN) + QPointF(0, 40))  # well below, on a later line
    qtbot.mousePress(vp, LEFT, NO_MOD, start)
    for k in (1, 2, 3):
        qtbot.mouseMove(vp, start + (end - start) * k / 3)
    qtbot.mouseRelease(vp, LEFT, NO_MOD, end)
    selected = _selected(w)
    assert selected and "\n" not in selected
    assert _norm(LINE1).find(selected) >= 0


def test_shift_click_extends(qtbot, edit_window) -> None:
    w = edit_window
    _click(qtbot, w, _char(w, JEAN + 1))
    dupont_end = LINE1.index("Dupont") + 5
    _click(qtbot, w, _char(w, dupont_end), Qt.KeyboardModifier.ShiftModifier)
    assert _selected(w) == "Jean Dupont"
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_Escape)
    assert w.document_view.text_selection.is_empty


def test_f2_opens_and_escape_cancels(qtbot, edit_window) -> None:
    w = edit_window
    _click(qtbot, w, _char(w, JEAN + 1))
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_F2)
    editor = w.document_view.textedit_editor
    assert editor.is_open
    qtbot.keyClick(editor.editor, Qt.Key.Key_Escape)
    assert not editor.is_open
    assert w.undo_stack.count() == 0


def test_hover_frames_and_cursor(qtbot, edit_window) -> None:
    w = edit_window
    _hover(w, _vp(w, _char(w, JEAN + 1)))
    hover = w.document_view.text_hover
    assert hover.target is not None and hover.item is not None
    assert w.page_view.viewport().cursor().shape() == Qt.CursorShape.IBeamCursor
    _hover(w, _vp(w, QPointF(300, 600)))
    assert hover.target is None
    assert w.page_view.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor
    _hover(w, _vp(w, _char(w, JEAN + 1)))
    w.act_hand_tool.trigger()
    assert hover.item is None


def test_tool_switch_clears_selection(qtbot, edit_window) -> None:
    w = edit_window
    _click(qtbot, w, _char(w, JEAN + 1))
    assert not w.document_view.text_selection.is_empty
    w.act_hand_tool.trigger()
    assert w.document_view.text_selection.is_empty


# -- notices -------------------------------------------------------------------------------
def test_click_on_empty_area_or_image(qtbot, edit_window) -> None:
    w = edit_window
    messages = _messages(w)
    _click(qtbot, w, QPointF(300, 600))
    assert messages[-1] == "No editable text here (scanned image or outlined text)."
    w.statusBar().clearMessage()
    x0, y0, x1, y1 = IMAGE_RECT
    _click(qtbot, w, QPointF((x0 + x1) / 2, (y0 + y1) / 2))
    assert messages[-1] == "No editable text here (scanned image or outlined text)."
    assert w.document_view.text_selection.is_empty


def test_ocr_layer_notice(qtbot, window, tmp_path) -> None:
    w = window
    assert w.open_file(str(make_ocr_pdf(tmp_path / "ocr.pdf")))
    w.act_textedit_tool.trigger()
    messages = _messages(w)
    _click(qtbot, w, _char(w, 1))
    assert messages[-1] == (
        "This text is an invisible OCR layer over an image; it cannot be edited here."
    )
    assert w.document_view.text_selection.is_empty


def test_substitution_warning(qtbot, edit_window) -> None:
    w = edit_window
    messages = _messages(w)
    start = LINE1.index("Dupont")
    w.document_view.text_selection.set(0, start, start + 5)
    widget = _open_editor(qtbot, w)
    widget.setText("Zidane")
    qtbot.keyClick(widget, Qt.Key.Key_Return)
    assert w.undo_stack.count() == 1
    assert (
        "Replaced with Calibri: the document’s font lacks some of these characters."
        in (messages[-1])
    )


def test_refusal_reason_notices(qtbot, edit_window, monkeypatch) -> None:
    w = edit_window
    assert textedit_tool.reason_notice(EditReason.TOO_COMPLEX) == (
        "This page is too complex to edit (content larger than 20 MB)."
    )
    assert textedit_tool.reason_notice(EditReason.DIRECTION) == (
        "Right-to-left and vertical text cannot be edited."
    )
    assert textedit_tool.reason_notice(EditReason.FAILED) == "The text could not be changed."
    assert textedit_tool.reason_notice(EditReason.INVISIBLE).startswith("This text is an")

    def refuse(*_a, **_k):
        from pdfeditor.core.textedit import TextEditError

        raise TextEditError("too big", EditReason.TOO_COMPLEX)

    monkeypatch.setattr(type(w.document_view.document), "replace_text_run", refuse)
    messages = _messages(w)
    assert w.textedit_tool.apply_edit(0, Run(JEAN, JEAN + 3), "Paul") is None
    assert messages[-1] == "This page is too complex to edit (content larger than 20 MB)."
    assert w.undo_stack.count() == 0


def test_commit_pending_edits_commits_editor_before_save(qtbot, edit_window, tmp_path) -> None:
    w = edit_window
    _click(qtbot, w, _char(w, JEAN + 1))
    widget = _open_editor(qtbot, w)
    widget.setText("Paul")
    out = tmp_path / "saved.pdf"
    w.document_view.save_as(str(out))
    assert w.undo_stack.count() == 1
    assert _edited(w, "Jean", "Paul")
    assert not w.document_view.textedit_editor.is_open


# -- shortcuts dialog and translations -------------------------------------------------------
def test_shortcuts_dialog_lists_edit_page_text(window) -> None:
    sections = dict(window.shortcut_sections())
    native = QKeySequence.SequenceFormat.NativeText
    assert dict(sections["Edit"])["Edit Page Text"] == QKeySequence("E").toString(native)
    interaction = dict(sections["Pointer and editing"])
    assert interaction["Select a word / a line of page text"] == "Click / Double-click"


def test_french_strings(qtbot, qapp, settings) -> None:
    install_translators(qapp, "fr")
    try:
        w = MainWindow(settings)
        qtbot.addWidget(w)
        assert w.act_textedit_tool.text() == "Modifier le &texte de la page"
        assert w.act_textedit_tool.toolTip() == "Modifier le texte de la page (E)"
        assert textedit_tool.no_text_notice() == (
            "Aucun texte modifiable ici (image numérisée ou texte vectorisé)."
        )
        assert textedit_tool.too_complex_notice() == (
            "Cette page est trop complexe pour être modifiée (contenu supérieur à 20 Mo)."
        )
        assert textedit_tool.full_save_notice() == (
            "Après une modification du texte, le prochain enregistrement réécrit tout le fichier."
        )
        interaction = dict(dict(w.shortcut_sections())["Souris et saisie"])
        assert interaction["Sélectionner un mot / une ligne du texte de la page"] == (
            "Clic / Double-clic"
        )
        w.close()
    finally:
        remove_translators(qapp)
