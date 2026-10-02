"""M6b-T11: selection overlay and hit-testing of text markups (quad outlines, no handles,
never moved nor resized, hit by quads)."""

from __future__ import annotations

import fixtures
import pytest
from fixtures import (
    MARKED_HIGHLIGHT_NAME,
    MARKED_QUADS,
    MARKED_ROTATED_NAME,
    MARKED_UNDERLINE_NAME,
)
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene, QMessageBox

from pdfeditor.core.annotations import AnnotKind, AnnotSpec
from pdfeditor.core.commands import DeleteAnnotCommand
from pdfeditor.core.pagetext import Quad
from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.overlays.annot_items import (
    SELECTION_COLOR,
    AnnotHandleItem,
    Handle,
    annot_hit,
    handle_points,
)

NO_MOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
#: The fixture's two-quad highlight: line 1 (94, 89)-(160, 103), line 2 (72, 107)-(140, 121).
QUAD_1 = QRectF(QPointF(94, 89), QPointF(160, 103))
QUAD_2 = QRectF(QPointF(72, 107), QPointF(140, 121))
UNION = QUAD_1.united(QUAD_2)
#: Inside the union rect of the highlight, in neither of its quads.
GAP = QPointF(152, 115)
QUADS = (Quad.from_rect(QUAD_1), Quad.from_rect(QUAD_2))


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
def marked_window(window, tmp_path):
    path = fixtures.make_marked_pdf(tmp_path / "marked.pdf")
    assert window.open_file(str(path))
    window.act_text_tool.trigger()
    assert window.tool_manager.active_tool is window.text_tool
    return window


def _vp(w: MainWindow, page: int, point: QPointF) -> QPoint:
    pv = w.page_view
    scene = pv.page_item(page).mapToScene(point)
    pv.ensureVisible(QRectF(scene.x() - 1, scene.y() - 1, 2, 2), 60, 60)
    return pv.mapFromScene(pv.page_item(page).mapToScene(point))


def _click(qtbot, w: MainWindow, point: QPointF, page: int = 0) -> None:
    qtbot.mouseClick(w.page_view.viewport(), LEFT, NO_MOD, _vp(w, page, point))


def _drag(qtbot, w: MainWindow, point: QPointF, dx: int, dy: int) -> None:
    vp = w.page_view.viewport()
    start = _vp(w, 0, point)
    qtbot.mousePress(vp, LEFT, NO_MOD, start)
    for k in (1, 2, 3):
        qtbot.mouseMove(vp, start + QPoint(dx * k // 3, dy * k // 3))
    qtbot.mouseRelease(vp, LEFT, NO_MOD, start + QPoint(dx, dy))


def _text_spec() -> AnnotSpec:
    return AnnotSpec(
        page=0,
        kind=AnnotKind.TEXT,
        text="x",
        font_size=11.0,
        color=(0.0, 0.0, 0.0),
        rect=QRectF(300, 500, 100, 40),
    )


def _render(item: AnnotHandleItem, size: int = 300) -> QImage:
    scene = QGraphicsScene()
    scene.addItem(item)
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    scene.render(painter, QRectF(0, 0, size, size), QRectF(0, 0, size, size))
    painter.end()
    scene.removeItem(item)
    return image


def _is_blue(image: QImage, p: QPointF) -> bool:
    """A selection-coloured pixel within 1 px of ``p``."""
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            c = QColor(image.pixel(round(p.x()) + dx, round(p.y()) + dy))
            if c.alpha() and abs(c.blue() - SELECTION_COLOR.blue()) < 40 and c.red() < 80:
                return True
    return False


def _is_white(image: QImage, p: QPointF) -> bool:
    c = QColor(image.pixel(round(p.x()), round(p.y())))
    return c.alpha() > 0 and min(c.red(), c.green(), c.blue()) > 200


# -- AnnotHandleItem -------------------------------------------------------------------
def test_item_without_handles_never_hits_a_handle() -> None:
    item = AnnotHandleItem(UNION, handles=False, quads=QUADS)
    assert not item.handles and item.quads == QUADS
    for centre in handle_points(UNION).values():
        assert item.handle_at(centre, 6.0) is None
    with_handles = AnnotHandleItem(UNION)
    assert with_handles.handles and with_handles.quads == ()
    assert with_handles.handle_at(UNION.topLeft(), 6.0) is Handle.TOP_LEFT


def test_item_setters() -> None:
    item = AnnotHandleItem(UNION)
    item.set_quads(QUADS)
    item.set_handles(False)
    assert item.quads == QUADS and not item.handles
    assert item.handle_at(UNION.topLeft(), 6.0) is None
    item.set_quads(())
    item.set_handles(True)
    assert item.quads == () and item.handle_at(UNION.topLeft(), 6.0) is Handle.TOP_LEFT


def test_quad_outlines_painted_without_frame_or_handles(qapp) -> None:
    image = _render(AnnotHandleItem(UNION, handles=False, quads=QUADS))
    # Every quad's corners and edges are outlined.
    for rect in (QUAD_1, QUAD_2):
        for p in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
            assert _is_blue(image, p), p
        assert _is_blue(image, QPointF(rect.center().x(), rect.top()))
    # No union frame (its top-left corner is in neither quad) and no white handle.
    assert not _is_blue(image, UNION.topLeft())
    assert not _is_blue(image, QPointF(UNION.left(), UNION.top() + 5))
    for centre in handle_points(UNION).values():
        assert not _is_white(image, centre)
    # The M3 item draws the frame and white handles.
    framed = _render(AnnotHandleItem(UNION))
    assert _is_blue(framed, QPointF(UNION.left(), UNION.top() + 5))
    assert _is_white(framed, handle_points(UNION)[Handle.TOP_LEFT])


def test_slanted_quad_outline(qapp) -> None:
    quad = Quad(QPointF(50, 50), QPointF(150, 100), QPointF(40, 70), QPointF(140, 120))
    image = _render(AnnotHandleItem(quad.bounding_rect(), handles=False, quads=(quad,)))
    assert _is_blue(image, QPointF(100, 75))  # middle of the top edge
    assert not _is_blue(image, quad.bounding_rect().topRight())


def test_item_is_not_mouse_target(qapp) -> None:
    scene = QGraphicsScene()
    parent = QGraphicsRectItem(0, 0, 300, 300)
    scene.addItem(parent)
    item = AnnotHandleItem(UNION, parent, handles=False, quads=QUADS)
    assert item.acceptedMouseButtons() == Qt.MouseButton.NoButton
    assert item.boundingRect().contains(UNION)


# -- annot_hit ---------------------------------------------------------------------------
def test_annot_hit_by_quads(marked_window) -> None:
    doc = marked_window.document_view.document
    info = doc.annot(0, MARKED_HIGHLIGHT_NAME)
    assert info.rect.contains(GAP)
    assert annot_hit(info, QUAD_1.center(), 0.0)
    assert annot_hit(info, QUAD_2.center(), 0.0)
    assert not annot_hit(info, GAP, 3.0)
    assert annot_hit(info, QPointF(QUAD_2.right() + 2, QUAD_2.center().y()), 3.0)
    assert not annot_hit(info, QPointF(QUAD_2.right() + 4, QUAD_2.center().y()), 3.0)
    # Any other annotation is hit by its rect.
    text = doc.add_annot(_text_spec())
    assert annot_hit(text, QPointF(302, 502), 0.0)
    assert annot_hit(text, QPointF(298, 502), 3.0)


# -- tools ----------------------------------------------------------------------------------
def test_click_on_quad_selects_with_outline_item(qtbot, marked_window) -> None:
    w = marked_window
    selection = w.document_view.annot_selection
    _click(qtbot, w, QUAD_2.center())
    current = selection.current
    assert current is not None and current.name == MARKED_HIGHLIGHT_NAME
    item = selection.item
    assert item is not None and not item.handles
    assert item.quads == current.quads and len(item.quads) == 2
    assert item.parentItem() is w.page_view.page_item(0)
    assert w.act_delete_annot.isEnabled()
    assert w.undo_stack.count() == 0


def test_click_between_quads_misses(qtbot, marked_window) -> None:
    w = marked_window
    tool = w.text_tool
    assert tool.annot_at(0, GAP) is None
    assert tool.annot_at(0, QUAD_1.center()).name == MARKED_HIGHLIGHT_NAME
    _click(qtbot, w, GAP)
    current = w.document_view.annot_selection.current
    assert current is None or current.name != MARKED_HIGHLIGHT_NAME
    w.document_view.annot_editor.cancel()


def test_markup_never_moved(qtbot, marked_window) -> None:
    w = marked_window
    doc = w.document_view.document
    before = doc.annot(0, MARKED_HIGHLIGHT_NAME)
    _drag(qtbot, w, QUAD_1.center(), 60, 40)
    selection = w.document_view.annot_selection
    assert selection.current.name == MARKED_HIGHLIGHT_NAME
    assert selection.item.ghost is None
    assert w.undo_stack.count() == 0
    assert doc.annot(0, MARKED_HIGHLIGHT_NAME).quads == before.quads


@pytest.mark.parametrize("corner", ["topRight", "bottomRight", "topLeft"])
def test_markup_never_resized_from_corners(qtbot, marked_window, corner) -> None:
    """Pressing where a handle of the union rect would be never resizes."""
    w = marked_window
    doc = w.document_view.document
    _click(qtbot, w, QUAD_1.center())  # select (and arm)
    before = doc.annot(0, MARKED_HIGHLIGHT_NAME)
    point = getattr(QUAD_1, corner)()
    assert w.text_tool._handle_at(0, point) is None
    _drag(qtbot, w, point, 30, 30)
    assert w.undo_stack.count() == 0
    assert doc.annot(0, MARKED_HIGHLIGHT_NAME).quads == before.quads
    assert w.document_view.annot_selection.item.ghost is None


def test_hover_cursor_on_markup(qtbot, marked_window) -> None:
    w = marked_window
    vp = w.page_view.viewport()
    qtbot.mouseMove(vp, _vp(w, 0, QUAD_1.center()))
    assert vp.cursor().shape() == Qt.CursorShape.PointingHandCursor
    _click(qtbot, w, QUAD_1.center())
    qtbot.mouseMove(vp, _vp(w, 0, QUAD_1.topRight()))
    assert vp.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_delete_selected_markup(qtbot, marked_window) -> None:
    w = marked_window
    doc = w.document_view.document
    _click(qtbot, w, QUAD_1.center())
    qtbot.keyClick(w.page_view.viewport(), Qt.Key.Key_Delete)
    assert isinstance(w.undo_stack.command(0), DeleteAnnotCommand)
    assert doc.annot(0, MARKED_HIGHLIGHT_NAME) is None
    assert w.document_view.annot_selection.current is None
    w.undo_stack.undo()
    assert doc.annot(0, MARKED_HIGHLIGHT_NAME) is not None


def test_recolor_keeps_outline_item(qtbot, marked_window) -> None:
    w = marked_window
    doc = w.document_view.document
    selection = w.document_view.annot_selection
    _click(qtbot, w, QUAD_1.center())
    w.text_tool.apply_style(color=(0.0, 1.0, 0.0))
    assert w.undo_stack.count() == 1
    fresh = doc.annot(0, MARKED_HIGHLIGHT_NAME)
    assert fresh.color == pytest.approx((0.0, 1.0, 0.0))
    assert selection.current.color == pytest.approx((0.0, 1.0, 0.0))
    assert not selection.item.handles and selection.item.quads == fresh.quads


def test_selection_switch_between_markup_and_freetext(qtbot, marked_window) -> None:
    w = marked_window
    doc = w.document_view.document
    selection = w.document_view.annot_selection
    text = doc.add_annot(_text_spec())
    selection.select(doc.annot(0, MARKED_UNDERLINE_NAME))
    item = selection.item
    assert not item.handles and len(item.quads) == 1
    selection.select(text)
    assert selection.item is item  # reused
    assert item.handles and item.quads == () and item.rect == text.rect
    selection.select(doc.annot(0, MARKED_HIGHLIGHT_NAME))
    assert not item.handles and len(item.quads) == 2


def test_rotated_page_markup_selected_by_quad(qtbot, marked_window) -> None:
    w = marked_window
    doc = w.document_view.document
    info = doc.annot(1, MARKED_ROTATED_NAME)
    (expected,) = MARKED_QUADS[MARKED_ROTATED_NAME]
    _click(qtbot, w, info.quads[0].bounding_rect().center(), page=1)
    selection = w.document_view.annot_selection
    assert selection.current.name == MARKED_ROTATED_NAME
    box = selection.item.quads[0].bounding_rect()
    assert box.left() == pytest.approx(expected[0], abs=0.01)
    assert box.top() == pytest.approx(expected[1], abs=0.01)
    assert not selection.item.handles


def test_selection_follows_markup_across_page_move(qtbot, marked_window) -> None:
    w = marked_window
    doc = w.document_view.document
    selection = w.document_view.annot_selection
    _click(qtbot, w, QUAD_1.center())
    w.move_pages([0], 2)  # page 1 moves to the end
    current = selection.current
    assert current is not None and current.page == 1
    assert current.name == MARKED_HIGHLIGHT_NAME
    assert selection.item.parentItem() is w.page_view.page_item(1)
    assert not selection.item.handles and len(selection.item.quads) == 2
    assert doc.annot(1, MARKED_HIGHLIGHT_NAME) is not None
