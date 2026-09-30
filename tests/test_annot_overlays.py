"""M3-T4: annotation text editor and selection handles."""

from __future__ import annotations

import pytest
from fixtures import ANNOT_TEXT, ANNOT_TEXT_NAME, ROTATED_ANNOT_NAME
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QGraphicsScene, QPlainTextEdit

from pdfeditor.constants import ZoomMode
from pdfeditor.core.document import PdfDocument
from pdfeditor.ui.document_view import DocumentView
from pdfeditor.ui.overlays.annot_editor import AnnotTextEditor, EditorAnchor
from pdfeditor.ui.overlays.annot_items import (
    ANNOT_HANDLE_Z,
    BOUNDS_MARGIN,
    AnnotHandleItem,
    AnnotSelection,
    Handle,
    handle_points,
)

RECT = QRectF(100, 300, 200, 60)
BLUE = (0.0, 0.0, 1.0)
RED = (1.0, 0.0, 0.0)


@pytest.fixture
def dv(qtbot):
    view = DocumentView()
    qtbot.addWidget(view)
    view.resize(800, 600)
    view.show()
    qtbot.waitExposed(view)
    view.activateWindow()
    yield view
    view.undo_stack.setClean()
    view.shutdown()


class Recorder:
    def __init__(self, editor: AnnotTextEditor) -> None:
        self.committed: list[tuple[EditorAnchor, str]] = []
        self.cancelled = 0
        editor.committed.connect(lambda anchor, text: self.committed.append((anchor, text)))
        editor.cancelled.connect(self._on_cancelled)

    def _on_cancelled(self) -> None:
        self.cancelled += 1


@pytest.fixture
def setup(qtbot, dv, annotated_pdf):
    doc = dv.open(str(annotated_pdf))
    dv.page_view.set_zoom_mode(ZoomMode.CUSTOM)
    dv.page_view.set_zoom_percent(100)
    editor = AnnotTextEditor(dv.page_view, doc)
    return dv, doc, editor, Recorder(editor)


def _assert_geom(editor: AnnotTextEditor, view, page: int, rect: QRectF, grow: bool = False):
    expected = view.page_rect_to_viewport(page, rect)
    got = editor.editor.geometry()
    assert abs(got.x() - expected.x()) <= 2
    assert abs(got.y() - expected.y()) <= 2
    assert abs(got.width() - expected.width()) <= 2
    if grow:
        assert got.height() >= expected.height() - 2
    else:
        assert abs(got.height() - expected.height()) <= 2


# -- AnnotTextEditor ------------------------------------------------------------
def test_open_new_geometry_and_font(qtbot, setup) -> None:
    dv, doc, editor, rec = setup
    view = dv.page_view
    editor.open_new(0, RECT, 11, BLUE)
    widget = editor.editor
    assert isinstance(widget, QPlainTextEdit)
    assert widget.parent() is view.viewport()
    assert widget.isVisible() and editor.is_open
    assert widget.toPlainText() == ""
    _assert_geom(editor, view, 0, RECT)
    assert widget.font().pixelSize() == round(11 * view.view_scale)
    assert "rgb(0, 0, 255)" in widget.styleSheet()
    anchor = editor.anchor
    assert anchor is not None and anchor.is_new and anchor.name == ""
    assert (anchor.page, anchor.rect, anchor.font_size, anchor.color) == (0, RECT, 11.0, BLUE)
    qtbot.waitUntil(widget.hasFocus)


def test_enter_newline_ctrl_enter_commits_once(qtbot, setup) -> None:
    dv, doc, editor, rec = setup
    editor.open_new(0, RECT, 11, BLUE)
    widget = editor.editor
    qtbot.waitUntil(widget.hasFocus)
    qtbot.keyClicks(widget, "a")
    qtbot.keyClick(widget, Qt.Key.Key_Return)
    qtbot.keyClicks(widget, "b")
    assert editor.is_open and rec.committed == []
    assert widget.toPlainText() == "a\nb"
    qtbot.keyClick(widget, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert len(rec.committed) == 1
    anchor, text = rec.committed[0]
    assert text == "a\nb"
    assert anchor.is_new and anchor.page == 0 and anchor.rect == RECT
    assert anchor.font_size == 11 and anchor.color == BLUE
    assert not editor.is_open and widget.isHidden()
    qtbot.wait(10)  # deleteLater, focus changes: still exactly one commit
    assert len(rec.committed) == 1 and rec.cancelled == 0
    assert dv.page_view.hasFocus()


def test_escape_cancels(qtbot, setup) -> None:
    dv, doc, editor, rec = setup
    editor.open_new(0, RECT, 11, BLUE)
    qtbot.keyClicks(editor.editor, "abc")
    qtbot.keyClick(editor.editor, Qt.Key.Key_Escape)
    assert rec.cancelled == 1 and rec.committed == [] and not editor.is_open
    qtbot.wait(10)
    assert rec.committed == []


def test_empty_new_commit_emits_nothing(qtbot, setup) -> None:
    dv, doc, editor, rec = setup
    editor.open_new(0, RECT, 11, BLUE)
    assert editor.commit() is False
    editor.open_new(0, RECT, 11, BLUE)
    editor.editor.setPlainText("  \n ")
    assert editor.commit() is False
    assert rec.committed == [] and rec.cancelled == 0 and not editor.is_open


def test_tab_commits(qtbot, setup) -> None:
    dv, doc, editor, rec = setup
    editor.open_new(0, RECT, 11, BLUE)
    qtbot.keyClicks(editor.editor, "t")
    qtbot.keyClick(editor.editor, Qt.Key.Key_Tab)
    assert [t for _a, t in rec.committed] == ["t"] and not editor.is_open


def test_focus_out_commits(qtbot, setup) -> None:
    dv, doc, editor, rec = setup
    editor.open_new(0, RECT, 11, BLUE)
    widget = editor.editor
    qtbot.waitUntil(widget.hasFocus)
    qtbot.keyClicks(widget, "focus")
    dv.page_view.setFocus(Qt.FocusReason.MouseFocusReason)
    qtbot.waitUntil(lambda: not editor.is_open)
    assert [t for _a, t in rec.committed] == ["focus"]
    qtbot.wait(10)
    assert len(rec.committed) == 1


def test_open_existing_prefilled_and_changed_only(qtbot, setup) -> None:
    dv, doc, editor, rec = setup
    info = doc.annot(0, ANNOT_TEXT_NAME)
    editor.open_existing(info)
    widget = editor.editor
    assert widget.toPlainText() == ANNOT_TEXT
    assert editor.anchor.info is info and not editor.anchor.is_new
    assert editor.anchor.name == ANNOT_TEXT_NAME
    _assert_geom(editor, dv.page_view, 0, info.rect, grow=True)
    assert editor.commit() is False and rec.committed == []

    editor.open_existing(info)
    editor.editor.insertPlainText("!")
    assert editor.commit() is True
    anchor, text = rec.committed[-1]
    assert anchor.info is info and text == ANNOT_TEXT + "!"

    stamp = next(a for a in doc.annots(0) if a.kind != "text")
    with pytest.raises(ValueError):
        editor.open_existing(stamp)


def test_set_style_live(qtbot, setup) -> None:
    dv, doc, editor, rec = setup
    view = dv.page_view
    editor.set_style(20, RED)  # closed: no-op
    info = doc.annot(0, ANNOT_TEXT_NAME)
    editor.open_existing(info)
    widget = editor.editor
    editor.set_style(20, RED)
    assert widget.font().pixelSize() == round(20 * view.view_scale)
    assert "rgb(255, 0, 0)" in widget.styleSheet()
    assert editor.anchor.font_size == 20 and editor.anchor.color == RED
    # Unchanged text, changed style: committed with the new style.
    assert editor.commit() is True
    anchor, text = rec.committed[-1]
    assert text == ANNOT_TEXT and anchor.font_size == 20 and anchor.color == RED


def test_auto_growing_height(qtbot, setup) -> None:
    dv, doc, editor, rec = setup
    small = QRectF(100, 300, 200, 16)
    editor.open_new(0, small, 11, BLUE)
    widget = editor.editor
    h0 = widget.height()
    widget.setPlainText("1\n2\n3\n4\n5")
    assert widget.height() > h0 + 3 * widget.fontMetrics().lineSpacing()
    _assert_geom(editor, dv.page_view, 0, small, grow=True)


def test_follows_scroll_and_zoom(qtbot, setup) -> None:
    dv, doc, editor, rec = setup
    view = dv.page_view
    editor.open_new(0, RECT, 11, BLUE)
    widget = editor.editor
    before = widget.geometry()
    bar = view.verticalScrollBar()
    start = bar.value()
    bar.setValue(start + 40)
    delta = bar.value() - start
    assert delta == 40
    assert widget.geometry().y() == pytest.approx(before.y() - delta, abs=2)
    _assert_geom(editor, view, 0, RECT)

    bar.setValue(start)
    width = widget.width()
    view.set_zoom_percent(200)
    back = bar.value()
    _assert_geom(editor, view, 0, RECT)
    assert widget.width() == pytest.approx(width * 2, abs=3)
    assert widget.font().pixelSize() == round(11 * view.view_scale)

    bar.setValue(bar.maximum())
    assert widget.isHidden() and editor.is_open and rec.committed == []
    bar.setValue(back)
    assert widget.isVisible()
    _assert_geom(editor, view, 0, RECT)


def test_rotated_page_editor_is_horizontal(qtbot, setup) -> None:
    dv, doc, editor, rec = setup
    info = doc.annot(1, ROTATED_ANNOT_NAME)
    assert info.rect.width() > info.rect.height()
    editor.open_existing(info)
    widget = editor.editor
    _assert_geom(editor, dv.page_view, 1, info.rect, grow=True)
    assert widget.width() > widget.height()
    editor.close()


def test_closes_silently_on_document_change(qtbot, setup, simple_pdf) -> None:
    dv, doc, editor, rec = setup
    editor.open_new(0, RECT, 11, BLUE)
    qtbot.keyClicks(editor.editor, "bye")
    other = PdfDocument.open(str(simple_pdf))
    try:
        editor.set_document(other)
        assert not editor.is_open and editor.document is other
        assert rec.committed == [] and rec.cancelled == 0
    finally:
        editor.set_document(None)
        other.close()


# -- AnnotHandleItem ----------------------------------------------------------------
def test_handle_item_bounds(qtbot, setup) -> None:
    dv, doc, _editor, _rec = setup
    view = dv.page_view
    page_item = view.page_item(0)
    item = AnnotHandleItem(RECT, page_item)
    assert item.zValue() == ANNOT_HANDLE_Z
    assert item.acceptedMouseButtons() == Qt.MouseButton.NoButton
    m = BOUNDS_MARGIN
    offset = page_item.scenePos()
    assert item.sceneBoundingRect() == RECT.translated(offset).adjusted(-m, -m, m, m)
    ghost = RECT.translated(50, 40)
    item.set_ghost(ghost)
    assert item.sceneBoundingRect().contains(ghost.translated(offset).adjusted(-m, -m, m, m))
    item.set_ghost(None)
    moved = QRectF(10, 10, 30, 20)
    item.set_rect(moved)
    assert item.sceneBoundingRect() == moved.translated(offset).adjusted(-m, -m, m, m)


@pytest.mark.parametrize("zoom", [100, 25])
def test_handle_at_tolerance(qtbot, setup, zoom) -> None:
    dv, doc, _editor, _rec = setup
    view = dv.page_view
    view.set_zoom_percent(zoom)
    item = AnnotHandleItem(RECT, view.page_item(0))
    tol = 6 / view.view_scale
    near = 4 / view.view_scale
    points = handle_points(RECT)
    assert len(points) == len(Handle) == 8
    for handle, centre in points.items():
        for dx, dy in ((near, near), (-near, near), (0, -near), (near, 0)):
            assert item.handle_at(centre + QPointF(dx, dy), tol) is handle
        assert item.handle_at(centre + QPointF(8 / view.view_scale, 0), tol) is not handle
    assert item.handle_at(RECT.center(), tol) is None


def _render(scene: QGraphicsScene, source: QRectF, scale: float) -> QImage:
    image = QImage(
        round(source.width() * scale), round(source.height() * scale), QImage.Format.Format_ARGB32
    )
    image.fill(QColor("white"))
    painter = QPainter(image)
    scene.render(painter, QRectF(image.rect()), source)
    painter.end()
    return image


def test_ghost_and_handles_paint(qtbot) -> None:
    scene = QGraphicsScene()
    item = AnnotHandleItem(QRectF(20, 20, 60, 30))
    scene.addItem(item)
    source = QRectF(0, 0, 200, 150)
    scene.setSceneRect(source)
    ghost_inside = (120 * 2, 90 * 2)  # a point inside the ghost, outside the rect
    plain = _render(scene, source, 2.0)
    assert plain.pixelColor(*ghost_inside) == QColor("white")
    item.set_ghost(QRectF(100, 70, 60, 40))
    image = _render(scene, source, 2.0)
    assert image.pixelColor(*ghost_inside) != QColor("white")
    # Ghost dashed border: some non-white pixels along its top edge.
    top = [image.pixelColor(x, 70 * 2) for x in range(100 * 2 + 4, 160 * 2 - 4)]
    assert any(c != QColor("white") for c in top)
    # Handles are 7 device px at any scale: blue border 3 px from the centre, not 5.
    for scale in (1.0, 3.0):
        img = _render(scene, source, scale)
        cx, cy = round(20 * scale), round(20 * scale)
        for dx, dy in ((-3, 1), (3, -1), (1, -3), (-1, 3)):  # outline
            colour = img.pixelColor(cx + dx, cy + dy)
            assert colour.blue() > 150 and colour.red() < 100, (scale, dx, dy)
        for dx, dy in ((-2, 1), (2, -1), (1, -2), (-1, 2)):  # white interior
            assert img.pixelColor(cx + dx, cy + dy) == QColor("white"), (scale, dx, dy)
        for dx, dy in ((-4, 1), (1, -4)):  # outside the handle (and the frame)
            assert img.pixelColor(cx + dx, cy + dy) == QColor("white"), (scale, dx, dy)


# -- AnnotSelection -------------------------------------------------------------------
@pytest.fixture
def selection(qtbot, setup):
    dv, doc, _editor, _rec = setup
    sel = AnnotSelection(dv.page_view, dv)
    sel.set_document(doc)
    changes: list[int] = []
    sel.changed.connect(lambda: changes.append(1))
    return dv, doc, sel, changes


def test_selection_select_and_clear(qtbot, selection) -> None:
    dv, doc, sel, changes = selection
    info = doc.annot(0, ANNOT_TEXT_NAME)
    sel.select(info)
    assert sel.current is info and len(changes) == 1
    item = sel.item
    assert item is not None and item.parentItem() is dv.page_view.page_item(0)
    assert item.rect == info.rect
    sel.select(info)
    assert len(changes) == 1
    sel.set_ghost(info.rect.translated(10, 0))
    assert item.ghost == info.rect.translated(10, 0)
    sel.set_ghost(None)
    assert item.ghost is None
    rotated = doc.annot(1, ROTATED_ANNOT_NAME)
    sel.select(rotated)
    assert sel.item.parentItem() is dv.page_view.page_item(1) and len(changes) == 2
    sel.clear()
    assert sel.current is None and sel.item is None and len(changes) == 3
    sel.clear()
    assert len(changes) == 3


def test_selection_follows_page_changes(qtbot, selection) -> None:
    dv, doc, sel, changes = selection
    sel.select(doc.annot(0, ANNOT_TEXT_NAME))
    moved = QRectF(150, 400, 200, 16)
    doc.update_annot(0, ANNOT_TEXT_NAME, rect=moved)
    got = sel.current.rect
    assert [got.x(), got.y(), got.width()] == pytest.approx([150, 400, 200], abs=0.5)
    assert sel.item.rect == sel.current.rect and len(changes) == 2
    # Another page's change: ignored.
    doc.page_changed.emit(1)
    assert len(changes) == 2
    doc.delete_annot(0, ANNOT_TEXT_NAME)
    assert sel.current is None and sel.item is None and len(changes) == 3


@pytest.mark.parametrize("signal", ["structure_changed", "reloaded"])
def test_selection_survives_structure_and_reload(qtbot, selection, signal) -> None:
    dv, doc, sel, changes = selection
    info = doc.annot(0, ANNOT_TEXT_NAME)
    sel.select(info)
    getattr(doc, signal).emit()
    assert sel.current == info and len(changes) == 1
    item = sel.item
    assert item is not None and item.parentItem() is dv.page_view.page_item(0)
    assert item.scene() is dv.page_view.scene()


def test_selection_cleared_on_document_change(qtbot, selection) -> None:
    dv, doc, sel, changes = selection
    sel.select(doc.annot(0, ANNOT_TEXT_NAME))
    sel.set_document(None)
    assert sel.current is None and sel.item is None and len(changes) == 2
