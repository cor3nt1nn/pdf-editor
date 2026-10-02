"""M6b review findings (UI): pending double-clicked words, Copy Text, markup hits."""

from __future__ import annotations

from pathlib import Path

import fixtures
import pymupdf
import pytest
from fixtures import TEXT_LINES
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.tools.markup_tools import expand_ligatures

NO_MOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
LINE_1 = TEXT_LINES[0][0]
QUICK = LINE_1.index("quick")
ARIAL = Path("C:/Windows/Fonts/arial.ttf")


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


def _dclick(qtbot, w: MainWindow, p: QPointF) -> None:
    vp = w.page_view.viewport()
    pos = _vp(w, p)
    qtbot.mouseClick(vp, LEFT, NO_MOD, pos)
    qtbot.mouseDClick(vp, LEFT, NO_MOD, pos)


def _markups(w: MainWindow):
    return [a for a in w.document_view.document.annots(0) if a.is_markup]


# -- 4: a pending double-clicked word is flushed before saves, closes and exports ----------
def test_pending_word_is_marked_before_save(qtbot, text_window) -> None:
    w = text_window
    w.act_highlight.trigger()
    _dclick(qtbot, w, _char(w, QUICK + 1))
    assert w.undo_stack.count() == 0  # waiting for a possible third click
    assert w.save()
    assert w.undo_stack.count() == 1
    assert not w.isWindowModified() and w.undo_stack.isClean()
    with pymupdf.open(w.document_view.document.path) as fd:
        assert [a.type[1] for a in fd[0].annots()].count("Highlight") == 1
    qtbot.wait(QApplication.doubleClickInterval() + 100)
    assert w.undo_stack.count() == 1  # not marked a second time
    assert not w.isWindowModified()


def test_pending_word_counts_before_close(qtbot, text_window, monkeypatch) -> None:
    w = text_window
    asked = []

    def confirm(parent, name):
        asked.append(name)
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(dialogs, "confirm_save_changes", confirm)
    w.act_underline.trigger()
    _dclick(qtbot, w, _char(w, QUICK + 1))
    assert not w.close_document()
    assert asked and len(_markups(w)) == 1


def test_tool_flush_pending_default_is_a_no_op(text_window) -> None:
    w = text_window
    for tool in w.tool_manager.tools.values():
        tool.flush_pending()
    assert w.undo_stack.count() == 0


# -- 5: Copy Text explains why it cannot copy -------------------------------------------
def test_copy_text_on_a_copy_protected_document_explains(qtbot, window, owner_locked_pdf):
    w = window
    assert w.open_file(str(owner_locked_pdf))
    assert not w.document_view.document.can_extract
    w.act_select_text.trigger()
    assert not w.act_copy_text.isEnabled()  # nothing selected
    w.document_view.text_selection.set(0, 0, 3)
    assert w.act_copy_text.isEnabled()
    clipboard = QApplication.clipboard()
    clipboard.setText("before")
    messages = []
    w.statusBar().messageChanged.connect(messages.append)
    w.page_view.setFocus()
    qtbot.keyClick(w.page_view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert messages[-1] == "Copying text is not permitted by this document’s security settings."
    assert clipboard.text() == "before"


# -- 7: ligatures are spelt out in copied text ----------------------------------------
def test_expand_ligatures() -> None:
    assert expand_ligatures("\ufb00 \ufb01 \ufb02 \ufb03 \ufb04 \ufb05 \ufb06 x") == (
        "ff fi fl ffi ffl st st x"
    )


@pytest.mark.skipif(not ARIAL.is_file(), reason="Arial is not installed")
def test_copy_text_spells_out_ligatures(qtbot, window, tmp_path) -> None:
    w = window
    path = tmp_path / "lig.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="FA", fontfile=str(ARIAL))
    page.insert_text((72, 100), "o\ufb03ce \ufb01le", fontname="FA", fontsize=14)
    doc.save(path)
    doc.close()
    assert w.open_file(str(path))
    pt = w.document_view.document.page_text(0)
    assert "\ufb03" in pt.text  # the model keeps one char per glyph
    w.act_select_text.trigger()
    w.document_view.text_selection.set(0, 0, len(pt.chars) - 1)
    assert w.copy_text()
    assert QApplication.clipboard().text().replace("\xa0", " ") == "office file"
    # The text under a markup too.
    w.act_highlight.trigger()
    w.document_view.text_selection.set(0, 0, len(pt.chars) - 1)
    info = w.tool_manager.active_tool.commit_selection()
    assert w.document_view.annot_selection.current.name == info.name
    assert w.copy_text()
    assert QApplication.clipboard().text().replace("\xa0", " ") == "office file"
