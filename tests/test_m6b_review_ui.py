"""M6b review findings (UI): pending double-clicked words, Copy Text, markup hits."""

from __future__ import annotations

import fixtures
import pymupdf
import pytest
from fixtures import TEXT_LINES
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow

NO_MOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
LINE_1 = TEXT_LINES[0][0]
QUICK = LINE_1.index("quick")


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
