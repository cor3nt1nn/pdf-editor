"""M6b-T14: text selection and markup hardening (long drags, many markups, odd markups,
foreign markups, clean documents stay untouched)."""

from __future__ import annotations

import fixtures
import pymupdf
import pytest
from fixtures import (
    LONG_TEXT_LINE,
    LONG_TEXT_LINES,
    MANY_MARKUPS,
    MARKED_FOREIGN,
    MARKED_FOREIGN_AUTHOR,
    MARKED_FOREIGN_CONTENTS,
    MARKED_FOREIGN_OPACITY,
    MARKED_QUADS,
    ODD_MARKUPS,
    TEXT_LINES,
    many_markups_name,
    many_markups_rect,
)
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMessageBox
from timing import best_time

from pdfeditor.core.annotations import AnnotKind
from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.tools.markup_tools import markup_text

NO_MOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
#: 500 drag moves over a 2 400-char page (docs/M6_PLAN.md T14).
DRAG_BUDGET_S = 1.0
DRAG_MOVES = 500
#: 100 hit tests among 200 markups (pointer press/hover cost).
MANY_HITS_BUDGET_S = 0.5
LINE_1 = TEXT_LINES[0][0]


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: None)
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Discard
    )
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.resize(1000, 800)
    w.show()
    qtbot.waitExposed(w)
    w.activateWindow()
    yield w
    w.undo_stack.setClean()
    w.close()


def _vp(w: MainWindow, point: QPointF, page: int = 0) -> QPoint:
    pv = w.page_view
    scene = pv.page_item(page).mapToScene(point)
    pv.ensureVisible(QRectF(scene.x() - 1, scene.y() - 1, 2, 2), 60, 60)
    return pv.mapFromScene(pv.page_item(page).mapToScene(point))


def _char(w: MainWindow, index: int) -> QPointF:
    return w.document_view.document.page_text(0).chars[index].bbox.center()


def _drag(qtbot, w: MainWindow, a: QPointF, b: QPointF, steps: int = 3) -> None:
    vp = w.page_view.viewport()
    start, end = _vp(w, a), _vp(w, b)
    qtbot.mousePress(vp, LEFT, NO_MOD, start)
    for k in range(1, steps + 1):
        qtbot.mouseMove(vp, start + (end - start) * k / steps)
    qtbot.mouseRelease(vp, LEFT, NO_MOD, end)


# -- performance -------------------------------------------------------------------------------
def test_long_selection_drag_budget(qtbot, window, tmp_path) -> None:
    w = window
    assert w.open_file(str(fixtures.make_long_text_pdf(tmp_path / "long.pdf")))
    w.page_view.set_zoom_percent(50)
    w.act_select_text.trigger()
    pt = w.document_view.document.page_text(0)
    assert len(pt.chars) == LONG_TEXT_LINES * len(LONG_TEXT_LINE)
    vp = w.page_view.viewport()
    first, last = _vp(w, _char(w, 0)), _vp(w, _char(w, len(pt.chars) - 1))
    # Every move lands somewhere new along the diagonal (and back), across all lines.
    path = [
        first + (last - first) * (k / (DRAG_MOVES // 2))
        for k in list(range(1, DRAG_MOVES // 2 + 1)) + list(range(DRAG_MOVES // 2, 0, -1))
    ]

    def drag() -> None:
        qtbot.mousePress(vp, LEFT, NO_MOD, first)
        for k, p in enumerate(path):
            qtbot.mouseMove(vp, p)
            if k % 10 == 0:
                QApplication.processEvents()  # repaint the selection now and then
        qtbot.mouseMove(vp, last)
        qtbot.mouseRelease(vp, LEFT, NO_MOD, last)

    best, times = best_time(drag, DRAG_BUDGET_S, setup=w.document_view.text_selection.clear)
    assert best < DRAG_BUDGET_S, times
    sel = w.document_view.text_selection
    assert len(sel.refs()) == len(pt.chars)
    assert sel.text().count("\n") == LONG_TEXT_LINES - 1
    QApplication.processEvents()  # paints the 40 quads
    assert w.copy_text()
    assert len(QApplication.clipboard().text()) >= 2000


def test_two_hundred_highlights_on_a_page(qtbot, window, tmp_path) -> None:
    w = window
    assert w.open_file(str(fixtures.make_many_markups_pdf(tmp_path / "many.pdf")))
    doc = w.document_view.document
    assert len(doc.annots(0)) == MANY_MARKUPS
    w.act_highlight.trigger()
    tool = w.markup_tools[AnnotKind.HIGHLIGHT]
    points = [
        QRectF(QPointF(*r[:2]), QPointF(*r[2:])).center()
        for r in map(many_markups_rect, range(0, MANY_MARKUPS, 2))
    ]
    found: list = []

    def hits() -> None:
        found[:] = [tool.annot_at(0, p) for p in points]

    best, times = best_time(hits, MANY_HITS_BUDGET_S)
    assert best < MANY_HITS_BUDGET_S, times
    assert [i.name for i in found] == [many_markups_name(k) for k in range(0, MANY_MARKUPS, 2)]
    # Click the last one: selected with its quad outline, no handles.
    x0, y0, x1, y1 = many_markups_rect(MANY_MARKUPS - 1)
    qtbot.mouseClick(w.page_view.viewport(), LEFT, NO_MOD, _vp(w, QPointF(x0 + 25, y0 + 15)))
    current = w.document_view.annot_selection.current
    assert current.name == many_markups_name(MANY_MARKUPS - 1)
    assert not w.document_view.annot_selection.item.handles
    # Drag from a highlighted word: one more highlight (the old one is not moved).
    pt = doc.page_text(0)
    word = pt.word_at(QPointF(x0 + 10, y1 - 13))
    assert word is not None
    _drag(qtbot, w, pt.chars[word[0].index].bbox.center(), pt.chars[word[1].index].bbox.center())
    assert len(doc.annots(0)) == MANY_MARKUPS + 1
    assert w.undo_stack.count() == 1
    w.undo()
    assert len(doc.annots(0)) == MANY_MARKUPS


# -- odd and foreign markups ---------------------------------------------------------------------
def test_odd_markups_are_listed_but_not_selectable(qtbot, window, tmp_path) -> None:
    w = window
    assert w.open_file(str(fixtures.make_odd_markups_pdf(tmp_path / "odd.pdf")))
    doc = w.document_view.document
    infos = {a.name: a for a in doc.annots(0)}
    assert set(infos) == {"ok", *ODD_MARKUPS}
    for name in ODD_MARKUPS:
        assert infos[name].is_markup
        assert infos[name].rect.isEmpty(), name
        assert not infos[name].editable, name
    assert infos["ok"].editable
    w.act_highlight.trigger()
    tool = w.markup_tools[AnnotKind.HIGHLIGHT]
    for p in (QPointF(100, 92), QPointF(0, 792), QPointF(300, 400), QPointF(130, 96)):
        assert tool.annot_at(0, p) is None or tool.annot_at(0, p).name == "ok"
    assert tool.annot_at(0, QPointF(100, 96)).name == "ok"
    # The text under them is still selectable and markable; copying ignores them.
    second = LINE_1.index("brown")
    _drag(qtbot, w, _char(w, second), _char(w, second + 4))
    current = w.document_view.annot_selection.current
    assert current is not None and markup_text(doc, current) == "brown"
    assert w.copy_text()
    assert QApplication.clipboard().text() == "brown"
    # Saving keeps them all.
    assert w.save()
    assert {a.name for a in doc.annots(0)} == {"ok", *ODD_MARKUPS, current.name}


def test_foreign_highlight_recolor_keeps_author_and_contents(qtbot, window, tmp_path) -> None:
    w = window
    path = fixtures.make_marked_pdf(tmp_path / "marked.pdf")
    assert w.open_file(str(path))
    doc = w.document_view.document
    w.act_highlight.trigger()
    (quad,) = MARKED_QUADS[MARKED_FOREIGN]
    centre = QPointF((quad[0] + quad[2]) / 2, (quad[1] + quad[5]) / 2)
    qtbot.mouseClick(w.page_view.viewport(), LEFT, NO_MOD, _vp(w, centre))
    current = w.document_view.annot_selection.current
    assert current is not None and current.kind is AnnotKind.HIGHLIGHT
    assert current.text == MARKED_FOREIGN_CONTENTS
    assert w.text_color == QColor.fromRgbF(*current.color)
    w._show_style(11, QColor("#00ff00"))
    w._apply_style(color=QColor("#00ff00"))
    assert w.undo_stack.command(0).text() == "Change markup color"
    info = doc.annot(0, w.document_view.annot_selection.current.name)
    assert info.color == pytest.approx((0.0, 1.0, 0.0))
    assert info.opacity == pytest.approx(MARKED_FOREIGN_OPACITY)
    assert info.text == MARKED_FOREIGN_CONTENTS
    assert w.save()
    with pymupdf.open(path) as raw:
        page = raw[0]
        (annot,) = [a for a in page.annots() if a.info.get("title") == MARKED_FOREIGN_AUTHOR]
        assert annot.info["content"] == MARKED_FOREIGN_CONTENTS
        assert annot.colors["stroke"] == pytest.approx([0.0, 1.0, 0.0])
    w.undo()
    assert doc.annot(0, info.name).color == pytest.approx((1.0, 0.5, 0.0))


def test_browsing_a_marked_document_changes_nothing(qtbot, window, tmp_path) -> None:
    w = window
    path = fixtures.make_marked_pdf(tmp_path / "marked.pdf")
    original = path.read_bytes()
    assert w.open_file(str(path))
    vp = w.page_view.viewport()
    w.act_select_text.trigger()
    _drag(qtbot, w, _char(w, 4), _char(w, 30))
    assert w.copy_text()
    qtbot.mouseClick(vp, LEFT, NO_MOD, _vp(w, _char(w, 6)))
    qtbot.mouseDClick(vp, LEFT, NO_MOD, _vp(w, _char(w, 6)))
    qtbot.keyClick(w.page_view, Qt.Key.Key_Escape)
    for act in (w.act_highlight, w.act_underline, w.act_strikeout):
        act.trigger()
        for name, quads in MARKED_QUADS.items():
            if name == fixtures.MARKED_ROTATED_NAME:
                continue
            q = quads[0]
            p = QPointF((q[0] + q[2]) / 2, (q[1] + q[5]) / 2)
            qtbot.mouseMove(vp, _vp(w, p))
            qtbot.mouseClick(vp, LEFT, NO_MOD, _vp(w, p))
            if w.act_copy_text.isEnabled():
                w.copy_text()
        qtbot.keyClick(w.page_view, Qt.Key.Key_Escape)
    w.act_hand_tool.trigger()
    assert w.undo_stack.count() == 0
    assert not w.isWindowModified()
    assert w.save()
    assert path.read_bytes() == original
