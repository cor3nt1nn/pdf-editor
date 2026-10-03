"""M7 review findings (UI): the run editor's stale-run guard, reopening after a refusal,
empty rects, rotated pages and the refusal notices."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QLineEdit, QMessageBox
from textedit_fixtures import (
    ARIAL_PATH,
    CALIBRI_PATH,
    LINE1,
    TIMES_PATH,
    make_text_edit_pdf,
    needs_text_fonts,
)

from pdfeditor.core.fontmatch import SystemFonts
from pdfeditor.core.textedit import EditReason, Run
from pdfeditor.i18n import install_translators, remove_translators
from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.overlays import textedit_editor
from pdfeditor.ui.tools import textedit_tool

pytestmark = needs_text_fonts

JEAN = LINE1.index("Jean")
JEAN_RUN = Run(JEAN, JEAN + 3)


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


def _open_window(window, tmp_path, *, rotate: int = 0):
    assert window.open_file(str(make_text_edit_pdf(tmp_path / "edit.pdf", rotate=rotate)))
    window.act_textedit_tool.trigger()
    assert window.tool_manager.active_tool is window.textedit_tool
    return window


def _norm(s: str) -> str:
    return s.replace("\xa0", " ")


def _page_text(w: MainWindow) -> str:
    return _norm(w.document_view.document.page_text(0).text)


def _messages(w: MainWindow) -> list[str]:
    messages: list[str] = []
    w.statusBar().messageChanged.connect(messages.append)
    return messages


def _editor_widget(w: MainWindow) -> QLineEdit:
    widget = w.document_view.textedit_editor.editor
    assert isinstance(widget, QLineEdit)
    return widget


# -- 6. the run is checked again when the edit is committed ----------------------------------
def test_stale_run_is_not_edited(qtbot, window, tmp_path, fonts) -> None:
    w = _open_window(window, tmp_path)
    messages = _messages(w)
    doc = w.document_view.document
    editor = w.document_view.textedit_editor
    editor.open(0, JEAN_RUN)
    widget = _editor_widget(w)
    widget.setText("Paul")
    # Something else rewrites the page meanwhile (the editor stays open: same page size).
    flat = _norm("".join(c.c for c in doc.page_text(0).chars))
    start = flat.index("Monsieur")
    doc.replace_text_run(0, Run(start, start + 7), "Madame", fonts=fonts)
    assert editor.is_open
    assert JEAN_RUN.text(doc.page_text(0)) != "Jean"
    before = _page_text(w)
    qtbot.keyClick(widget, Qt.Key.Key_Return)
    assert w.undo_stack.count() == 0
    assert _page_text(w) == before and "Paul" not in before
    assert messages[-1] == textedit_tool.stale_notice()
    assert not editor.is_open  # a stale run is not reopened


def test_replace_text_command_expect_text_guard(window, tmp_path, fonts) -> None:
    from pdfeditor.core.commands import ReplaceTextCommand
    from pdfeditor.core.textedit import TextEditError

    w = _open_window(window, tmp_path)
    doc = w.document_view.document
    cmd = ReplaceTextCommand(doc, 0, JEAN_RUN, "Paul", fonts=fonts, expect_text="Jear")
    with pytest.raises(TextEditError) as info:
        cmd.apply_now()
    assert info.value.reason is EditReason.STALE
    assert "Jean" in _page_text(w)
    ok = ReplaceTextCommand(doc, 0, JEAN_RUN, "Paul", fonts=fonts, expect_text="Jean")
    ok.apply_now()
    assert "Paul" in _page_text(w)


# -- 8. a refused edit reopens the editor with what was typed --------------------------------
def test_refused_edit_reopens_with_the_typed_text(qtbot, window, tmp_path) -> None:
    w = _open_window(window, tmp_path)
    messages = _messages(w)
    editor = w.document_view.textedit_editor
    editor.open(0, JEAN_RUN)
    widget = _editor_widget(w)
    typed = "Pa​ul"
    widget.setText(typed)
    qtbot.keyClick(widget, Qt.Key.Key_Return)
    assert w.undo_stack.count() == 0
    assert messages[-1] == textedit_tool.invalid_text_notice()
    assert editor.is_open
    assert editor.anchor is not None and editor.anchor.run == JEAN_RUN
    assert _editor_widget(w).text() == typed
    # typing a valid text now commits it
    _editor_widget(w).setText("Paul")
    qtbot.keyClick(_editor_widget(w), Qt.Key.Key_Return)
    assert w.undo_stack.count() == 1 and "Paul" in _page_text(w)
    assert not editor.is_open


def test_refused_edit_not_reopened_when_leaving_the_tool(qtbot, window, tmp_path) -> None:
    w = _open_window(window, tmp_path)
    editor = w.document_view.textedit_editor
    editor.open(0, JEAN_RUN)
    _editor_widget(w).setText("Pa\x00ul")
    w.act_hand_tool.trigger()
    assert not editor.is_open
    assert w.undo_stack.count() == 0


# -- 9. an empty run rect is refused ------------------------------------------------------
def test_empty_run_rect_is_refused(qtbot, window, tmp_path, monkeypatch) -> None:
    w = _open_window(window, tmp_path)
    messages = _messages(w)
    monkeypatch.setattr(textedit_editor, "run_rect", lambda text, run: QRectF())
    editor = w.document_view.textedit_editor
    with pytest.raises(ValueError):
        editor.open(0, JEAN_RUN)
    assert not editor.is_open
    w.document_view.text_selection.set(0, JEAN_RUN.first, JEAN_RUN.last)
    assert not w.textedit_tool.open_editor()
    assert not editor.is_open
    assert messages[-1] == textedit_tool.failed_notice()


# -- 7. rotated pages: a horizontal editor along the run --------------------------------------
@pytest.mark.parametrize("rotate", [90, 270])
def test_editor_on_a_rotated_page_is_horizontal(qtbot, window, tmp_path, rotate) -> None:
    w = _open_window(window, tmp_path, rotate=rotate)
    view = w.page_view
    editor = w.document_view.textedit_editor
    editor.open(0, JEAN_RUN)
    anchor = editor.anchor
    assert anchor is not None and anchor.vertical == (1 if rotate == 90 else -1)
    run_px = view.page_rect_to_viewport(0, anchor.rect)
    assert run_px.height() > run_px.width()  # the run is vertical on screen
    widget = _editor_widget(w)
    geo = widget.geometry()
    assert geo.width() > geo.height()  # the editor is not
    assert geo.height() == run_px.width()
    assert geo.width() >= run_px.height()
    assert geo.left() == run_px.left()
    if rotate == 90:  # text runs down: the editor starts at the run's top
        assert geo.top() == run_px.top()
    else:  # text runs up: it starts at the bottom
        assert geo.bottom() == run_px.bottom()
    widget.setText("Paul")
    qtbot.keyClick(widget, Qt.Key.Key_Return)
    assert w.undo_stack.count() == 1 and "Paul" in _page_text(w)


# -- refusal notices -----------------------------------------------------------------------
def test_specific_refusal_notices(qapp) -> None:
    notices = {r: textedit_tool.reason_notice(r) for r in EditReason}
    generic = textedit_tool.failed_notice()
    for reason in (
        EditReason.XOBJECT,
        EditReason.DIRECTION,
        EditReason.DUPLICATE,
        EditReason.NO_FONT,
        EditReason.STALE,
        EditReason.INVALID_TEXT,
    ):
        assert notices[reason] != generic, reason
    assert notices[EditReason.FAILED] == generic
    install_translators(qapp, "fr")
    try:
        assert textedit_tool.reason_notice(EditReason.NO_FONT) == (
            "Aucune police installée ne peut afficher ces caractères."
        )
        assert textedit_tool.reason_notice(EditReason.DIRECTION).startswith("Le texte de droite")
    finally:
        remove_translators(qapp)
