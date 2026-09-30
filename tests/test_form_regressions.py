"""M2 review regressions (UI): stale snapshots, renumbered xrefs, auto-shrink memory,
rotation with an open editor, multi-page radios and prefilled choices."""

from __future__ import annotations

import pytest
from fixtures import (
    LO_PREFILLED,
    make_lo_form_pdf,
    make_multipage_radio_pdf,
    make_square_form_pdf,
)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QListWidget, QMessageBox

import pdfeditor.core.document as document_module
from pdfeditor.core.document import PdfDocument, SaveError
from pdfeditor.core.forms import WidgetInfo
from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow

LONG_TEXT = "un texte beaucoup trop long pour tenir dans cette zone de saisie, vraiment"


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


def _doc(w: MainWindow) -> PdfDocument:
    doc = w.document_view.document
    assert doc is not None
    return doc


def _info(w: MainWindow, name: str, page: int = 0, index: int = 0) -> WidgetInfo:
    return [x for x in _doc(w).widgets(page) if x.name == name][index]


def _state(w: MainWindow, state: str) -> WidgetInfo:
    return next(x for x in _doc(w).all_widgets() if x.on_state == state)


def _click(qtbot, w: MainWindow, info: WidgetInfo) -> None:
    pv = w.page_view
    if pv.current_page != info.page:
        pv.scroll_to_page(info.page)
    pv.ensureVisible(pv.page_item(info.page).mapRectToScene(info.rect), 40, 40)
    pos = pv.page_rect_to_viewport(info.page, info.rect).center()
    qtbot.mouseClick(pv.viewport(), Qt.MouseButton.LeftButton, pos=pos)


# -- finding 1: another widget of the field being edited ------------------------------
@pytest.mark.parametrize("how", ["click", "focus"])
def test_other_widget_of_edited_field_sees_pending_value(qtbot, window, lo_form_pdf, how):
    w = window
    assert w.open_file(str(lo_form_pdf))
    editor = w.document_view.field_editor
    w.form_tool.focus(_info(w, "Nom"))
    editor.editor.setText("Dupont")  # typed, not committed yet
    kid = _info(w, "Nom", page=1)
    assert kid.value == ""  # the snapshot predates the pending value
    if how == "click":
        _click(qtbot, w, kid)
    else:
        w.form_tool.focus(kid)
    assert editor.is_open and editor.current_info.page == 1
    assert editor.current_info.value == "Dupont"
    assert editor.editor.text() == "Dupont"
    editor.editor.setText("Durand")
    assert editor.commit()
    assert _info(w, "Nom").value == "Durand"
    w.act_undo.trigger()
    assert _info(w, "Nom").value == "Dupont"
    assert _info(w, "Nom", page=1).value == "Dupont"
    w.act_undo.trigger()
    assert _info(w, "Nom").value == ""


# -- finding 3: renumbered xrefs ---------------------------------------------------------
def _focus_radio_f(qtbot, w: MainWindow) -> WidgetInfo:
    female = next(x for x in _doc(w).widgets(0) if x.on_state == "F")
    _click(qtbot, w, female)
    assert w.form_tool.focused_button is not None
    assert next(x for x in _doc(w).widgets(0) if x.on_state == "F").is_on
    return female


def _space_turns_radio_f_off(qtbot, w: MainWindow) -> None:
    others = {(x.name, x.value) for x in _doc(w).widgets(0) if x.name != "Sexe"}
    count = w.undo_stack.count()
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_Space)
    assert w.undo_stack.count() == count + 1
    assert not any(x.is_on for x in _doc(w).widgets(0) if x.name == "Sexe")
    assert {(x.name, x.value) for x in _doc(w).widgets(0) if x.name != "Sexe"} == others


def test_space_after_save_as_acts_on_the_focused_radio(qtbot, window, lo_form_pdf, tmp_path):
    w = window
    assert w.open_file(str(lo_form_pdf))
    female = _focus_radio_f(qtbot, w)
    w.document_view.save_as(str(tmp_path / "copy.pdf"))
    fresh = next(x for x in _doc(w).widgets(0) if x.on_state == "F")
    assert fresh.xref != female.xref  # garbage=3 renumbered the objects
    focused = w.form_tool.focused_button
    assert focused is not None and focused.xref == fresh.xref  # re-resolved on reload
    _space_turns_radio_f_off(qtbot, w)


def test_space_after_failed_full_save_acts_on_the_focused_radio(
    qtbot, window, lo_form_pdf, monkeypatch
):
    w = window
    assert w.open_file(str(lo_form_pdf))
    female = _focus_radio_f(qtbot, w)

    def fail(path, data):
        raise PermissionError(13, "Access is denied", path)

    monkeypatch.setattr(document_module, "_write_atomically", fail)
    with pytest.raises(SaveError):
        _doc(w).save(force_full=True)  # renumbers in memory, no reload
    fresh = next(x for x in _doc(w).widgets(0) if x.on_state == "F")
    assert fresh.xref != female.xref  # the widget cache was dropped
    _space_turns_radio_f_off(qtbot, w)


# -- finding 5: auto-shrink is not permanent ---------------------------------------------
def _commit(w: MainWindow, info: WidgetInfo, text: str) -> None:
    w.form_tool.focus(info)
    w.document_view.field_editor.editor.setText(text)
    assert w.document_view.field_editor.commit()


def test_auto_shrink_restores_original_size_when_text_fits(window, lo_form_pdf) -> None:
    w = window
    assert w.open_file(str(lo_form_pdf))
    name = "Zone de texte 8_55"  # 120 pt wide, prefilled
    assert _info(w, name).font_size == 8
    _commit(w, _info(w, name), LONG_TEXT)
    assert _info(w, name).font_size == 0
    _commit(w, _info(w, name), "court")
    assert w.undo_stack.command(1).font_size == 8
    assert _info(w, name).font_size == 8
    _commit(w, _info(w, name), "encore court")
    assert w.undo_stack.command(2).font_size is None  # nothing to restore
    w.act_undo.trigger()
    w.act_undo.trigger()
    assert (_info(w, name).value, _info(w, name).font_size) == (LONG_TEXT, 0)
    w.act_undo.trigger()
    assert (_info(w, name).value, _info(w, name).font_size) == ("déjà", 8)


def test_authored_auto_size_is_left_alone(window, lo_form_pdf) -> None:
    w = window
    assert w.open_file(str(lo_form_pdf))
    name = "Zone de texte 8_54"
    _commit(w, _info(w, name), LONG_TEXT)
    w.undo_stack.setClean()
    assert w.open_file(str(lo_form_pdf))  # forgets what it shrank (new document)
    doc = _doc(w)
    doc.set_field_value(0, _info(w, name).xref, "x", font_size=0)  # authored auto size
    _commit(w, _info(w, name), "court")
    assert w.undo_stack.command(0).font_size is None
    assert _info(w, name).font_size == 0


# -- finding 6: rotation with an open editor ----------------------------------------------
def test_rotate_with_open_editor_commits_first(qtbot, window, tmp_path) -> None:
    w = window
    assert w.open_file(str(make_square_form_pdf(tmp_path / "square.pdf")))
    editor = w.document_view.field_editor
    w.form_tool.focus(_info(w, "carre"))
    editor.editor.setText("abc")
    w.rotate_current_page(90)
    assert not editor.is_open
    assert w.undo_stack.count() == 2
    assert w.undo_stack.text(0) == "Edit form field"
    assert w.undo_stack.text(1) == "Rotate page"
    assert _info(w, "carre").value == "abc"
    w.act_undo.trigger()
    assert _doc(w).page_rotation(0) == 0 and _info(w, "carre").value == "abc"
    w.act_undo.trigger()
    assert _info(w, "carre").value == ""


def test_square_page_rotation_closes_editor(qtbot, window, tmp_path) -> None:
    """A rotation that bypasses DocumentView.push (square page: same size) still
    commits the editor instead of leaving it over a stale rect."""
    w = window
    assert w.open_file(str(make_square_form_pdf(tmp_path / "square.pdf")))
    editor = w.document_view.field_editor
    w.form_tool.focus(_info(w, "carre"))
    editor.editor.setText("abc")
    size = _doc(w).page_size(0)
    _doc(w).set_page_rotation(0, 90)
    assert _doc(w).page_size(0) == size
    assert not editor.is_open
    assert _info(w, "carre").value == "abc"


def test_document_view_push_commits_pending_edit(window, lo_form_pdf) -> None:
    from pdfeditor.core.commands import RotatePageCommand

    w = window
    assert w.open_file(str(lo_form_pdf))
    w.form_tool.focus(_info(w, "Zone de texte 8_54"))
    w.document_view.field_editor.editor.setText("avant")
    w.document_view.push(RotatePageCommand(_doc(w), 1, 90))
    assert [w.undo_stack.text(i) for i in range(2)] == ["Edit form field", "Rotate page"]


# -- multi-page radio, NoToggleToOff, prefilled choices -----------------------------------
def test_multi_page_radio_click_and_undo(qtbot, window, tmp_path) -> None:
    w = window
    assert w.open_file(str(make_multipage_radio_pdf(tmp_path / "radio.pdf")))
    assert _state(w, "C").is_on
    _click(qtbot, w, _state(w, "A"))
    assert _state(w, "A").is_on and not _state(w, "C").is_on
    w.act_undo.trigger()
    assert _state(w, "C").is_on and not _state(w, "A").is_on


def test_no_toggle_to_off_from_file(qtbot, window, tmp_path) -> None:
    w = window
    path = make_multipage_radio_pdf(tmp_path / "radio.pdf", no_toggle_off=True)
    assert w.open_file(str(path))
    _click(qtbot, w, _state(w, "C"))
    assert _state(w, "C").is_on
    assert w.undo_stack.count() == 0
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_Space)
    assert _state(w, "C").is_on and w.undo_stack.count() == 0


def test_prefilled_choices_preselected_in_editor(window, tmp_path) -> None:
    w = window
    assert w.open_file(str(make_lo_form_pdf(tmp_path / "pre.pdf", prefill_choices=True)))
    editor = w.document_view.field_editor
    w.form_tool.focus(_info(w, "Civilité"))
    combo = editor.editor
    assert isinstance(combo, QComboBox)
    assert combo.currentData() == LO_PREFILLED["Civilité"] and combo.currentText() == "Madame"
    w.form_tool.focus(_info(w, "Couleur"))
    lst = editor.editor
    assert isinstance(lst, QListWidget)
    assert [i.text() for i in lst.selectedItems()] == [LO_PREFILLED["Couleur"]]
    editor.cancel()
    assert w.undo_stack.count() == 0
