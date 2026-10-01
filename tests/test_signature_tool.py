"""M4-T5: signature placement (snapping.signature_placement), aspect-locked resize and the
signature tool."""

from __future__ import annotations

import fixtures
import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox

from pdfeditor.core import snapping
from pdfeditor.core.annotations import AnnotKind
from pdfeditor.core.commands import AddAnnotCommand, EditAnnotCommand
from pdfeditor.core.signature import ImageData
from pdfeditor.core.snapping import Snap, SnapKind
from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.overlays.annot_items import Handle
from pdfeditor.ui.tools.annot_tools import SignatureTool, form_field_message, resized_rect

ALT = Qt.KeyboardModifier.AltModifier
NO_MOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton
A4 = QSizeF(595, 842)
#: Empty spot of the word_form page (no shape nearby).
EMPTY = QPointF(400, 600)
#: Middle cell of the first table row of the word_form page.
CELL_CLICK = QPointF(222, 165)


# -- signature_placement -----------------------------------------------------------------
def test_placement_cell_fits_bottom_left_with_padding() -> None:
    cell = QRectF(172, 150, 100, 30)
    r = snapping.signature_placement(Snap(SnapKind.CELL, cell), QPointF(200, 160), 150, 2.5, A4)
    assert r.width() / r.height() == pytest.approx(2.5)
    assert r.height() == pytest.approx(30 - 4)  # height-limited, largest that fits
    assert r.left() == pytest.approx(cell.left() + 2)
    assert r.bottom() == pytest.approx(cell.bottom() - 2)
    assert cell.adjusted(2, 2, -2, -2).contains(r.adjusted(0.001, 0.001, -0.001, -0.001))
    # Width-limited cell.
    narrow = QRectF(10, 10, 40, 100)
    r = snapping.signature_placement(Snap(SnapKind.CELL, narrow), QPointF(20, 20), 150, 2.5, A4)
    assert r.width() == pytest.approx(36) and r.height() == pytest.approx(36 / 2.5)
    assert r.left() == pytest.approx(12) and r.bottom() == pytest.approx(108)
    # A big cell: no wider than the default width.
    big = QRectF(50, 300, 400, 200)
    r = snapping.signature_placement(Snap(SnapKind.CELL, big), QPointF(60, 310), 150, 2.5, A4)
    assert r.width() == pytest.approx(150) and r.height() == pytest.approx(60)
    assert r.left() == pytest.approx(52) and r.bottom() == pytest.approx(498)


def test_placement_underline() -> None:
    rule = QRectF(110, 100, 190, 0)
    r = snapping.signature_placement(Snap(SnapKind.UNDERLINE, rule), QPointF(200, 90), 150, 2.5, A4)
    assert r.left() == pytest.approx(112) and r.bottom() == pytest.approx(98)
    assert r.width() == pytest.approx(150) and r.height() == pytest.approx(60)
    short = QRectF(110, 100, 60, 0)
    r = snapping.signature_placement(
        Snap(SnapKind.UNDERLINE, short), QPointF(120, 90), 150, 2.5, A4
    )
    assert r.width() == pytest.approx(56) and r.height() == pytest.approx(56 / 2.5)
    assert r.left() == pytest.approx(112) and r.bottom() == pytest.approx(98)


@pytest.mark.parametrize("kind", [SnapKind.NONE, SnapKind.BOX])
def test_placement_default_centred_and_clamped(kind) -> None:
    box = QRectF(70, 70, 10, 10) if kind is SnapKind.BOX else None
    click = QPointF(300, 400)
    r = snapping.signature_placement(Snap(kind, box), click, 150, 2.5, A4)
    assert r.center().x() == pytest.approx(300) and r.center().y() == pytest.approx(400)
    assert r.width() == pytest.approx(150) and r.height() == pytest.approx(60)
    # Near the corner: shifted onto the page, size kept.
    r = snapping.signature_placement(Snap(kind, box), QPointF(590, 5), 150, 2.5, A4)
    assert r.right() == pytest.approx(595) and r.top() == pytest.approx(0)
    assert r.width() == pytest.approx(150)
    # Wider than the page: shrunk (aspect kept) to fit.
    r = snapping.signature_placement(Snap(kind, box), QPointF(10, 10), 1000, 2.5, A4)
    assert r.width() == pytest.approx(595) and r.height() == pytest.approx(595 / 2.5)
    assert QRectF(0, 0, 595, 842).contains(r.adjusted(0.001, 0.001, -0.001, -0.001))


# -- resized_rect(aspect=...) --------------------------------------------------------------
_ANCHORS = {
    Handle.TOP_LEFT: lambda r: (r.right(), r.bottom()),
    Handle.TOP_RIGHT: lambda r: (r.left(), r.bottom()),
    Handle.BOTTOM_LEFT: lambda r: (r.right(), r.top()),
    Handle.BOTTOM_RIGHT: lambda r: (r.left(), r.top()),
    Handle.LEFT: lambda r: (r.right(), r.center().y()),
    Handle.RIGHT: lambda r: (r.left(), r.center().y()),
    Handle.TOP: lambda r: (r.center().x(), r.bottom()),
    Handle.BOTTOM: lambda r: (r.center().x(), r.top()),
}


@pytest.mark.parametrize("handle", list(Handle))
@pytest.mark.parametrize("delta", [QPointF(17, 5), QPointF(-9, 13), QPointF(-60, -40)])
def test_resized_rect_keeps_aspect_and_anchor(handle, delta) -> None:
    rect = QRectF(100, 200, 100, 40)
    r = resized_rect(rect, handle, delta, aspect=2.5)
    assert abs(r.width() / r.height() - 2.5) < 1e-6
    assert min(r.width(), r.height()) >= 4.0 - 1e-9
    ax, ay = _ANCHORS[handle](rect)
    bx, by = _ANCHORS[handle](r)
    assert abs(ax - bx) < 1e-6 and abs(ay - by) < 1e-6


def test_resized_rect_aspect_follows_dominant_side() -> None:
    rect = QRectF(0, 0, 100, 40)
    r = resized_rect(rect, Handle.BOTTOM_RIGHT, QPointF(50, 0), aspect=2.5)
    assert (r.width(), r.height()) == pytest.approx((150, 60))
    r = resized_rect(rect, Handle.BOTTOM_RIGHT, QPointF(0, 20), aspect=2.5)
    assert (r.width(), r.height()) == pytest.approx((150, 60))
    r = resized_rect(rect, Handle.TOP, QPointF(0, -20), aspect=2.5)
    assert (r.width(), r.height()) == pytest.approx((150, 60))
    assert r.center().x() == pytest.approx(50) and r.bottom() == pytest.approx(40)


def test_resized_rect_square_unchanged() -> None:
    rect = QRectF(100, 100, 12, 12)
    for handle in Handle:
        for delta in (QPointF(6, 2), QPointF(-4, 0), QPointF(50, 50)):
            a = resized_rect(rect, handle, delta, square=True)
            b = resized_rect(rect, handle, delta, aspect=1.0)
            assert a == b
    r = resized_rect(rect, Handle.BOTTOM_RIGHT, QPointF(6, 2), square=True)
    assert (r.left(), r.top(), r.width(), r.height()) == (100, 100, 18, 18)
    r = resized_rect(QRectF(0, 0, 30, 10), Handle.RIGHT, QPointF(5, 0), square=False)
    assert (r.width(), r.height()) == (35, 10)


# -- the tool --------------------------------------------------------------------------------
@pytest.fixture
def window(qtbot, qapp, settings, monkeypatch):
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


def _install(w: MainWindow, store) -> SignatureTool:
    tool = SignatureTool(w.document_view, w.settings, store, w)
    tool.message.connect(w.statusBar().showMessage)
    w.tool_manager.register(tool)
    w.tool_manager.set_active(tool.name)
    assert w.tool_manager.active_tool is tool
    return tool


@pytest.fixture
def sig(window, word_form_pdf, store_with_one):
    assert window.open_file(str(word_form_pdf))
    return window, _install(window, store_with_one)


def _vp(w: MainWindow, page: int, point: QPointF) -> QPoint:
    pv = w.page_view
    scene = pv.page_item(page).mapToScene(point)
    pv.ensureVisible(QRectF(scene.x() - 1, scene.y() - 1, 2, 2), 60, 60)
    return pv.mapFromScene(pv.page_item(page).mapToScene(point))


def _click(qtbot, w: MainWindow, point: QPointF, page: int = 0, modifier=NO_MOD) -> None:
    qtbot.mouseClick(w.page_view.viewport(), LEFT, modifier, _vp(w, page, point))


def _drag_px(qtbot, w: MainWindow, start: QPoint, dx: int, dy: int = 0) -> None:
    vp = w.page_view.viewport()
    qtbot.mousePress(vp, LEFT, NO_MOD, start)
    qtbot.mouseMove(vp, start + QPoint(dx // 2, dy // 2))
    qtbot.mouseMove(vp, start + QPoint(dx, dy))
    qtbot.mouseRelease(vp, LEFT, NO_MOD, start + QPoint(dx, dy))


def _annots(w: MainWindow, page: int = 0):
    return w.document_view.document.annots(page)


def _aspect(store) -> float:
    record = store.get(store.default_id)
    return record.width / record.height


def _place_alt(qtbot, w, at: QPointF = EMPTY):
    _click(qtbot, w, at, modifier=ALT)
    (info,) = [a for a in _annots(w) if a.kind is AnnotKind.SIGNATURE]
    return info


def test_click_in_cell_places_snapped(qtbot, sig, store_with_one) -> None:
    w, tool = sig
    assert tool.snap_at(0, CELL_CLICK, False, tolerance=0.0).kind is SnapKind.CELL
    expected = tool.placement(0, CELL_CLICK, False, _aspect(store_with_one))
    _click(qtbot, w, CELL_CLICK)
    assert w.undo_stack.count() == 1
    command = w.undo_stack.command(0)
    assert isinstance(command, AddAnnotCommand) and command.text() == "Add signature"
    (info,) = _annots(w)
    assert info.kind is AnnotKind.SIGNATURE
    for a, b in zip(info.rect.getCoords(), expected.getCoords(), strict=True):
        assert a == pytest.approx(b, abs=1e-3)
    x0, x1 = fixtures.WORD_TABLE_X[1:3]
    y0, y1 = fixtures.WORD_TABLE_Y[0:2]
    assert x0 < info.rect.left() < x0 + 3 and y1 - 3 < info.rect.bottom() < y1
    assert info.rect.top() > y0
    assert info.rect.width() / info.rect.height() == pytest.approx(
        _aspect(store_with_one), rel=1e-3
    )
    assert w.document_view.annot_selection.current.name == info.name


def test_hover_preview_is_placement(qtbot, sig, store_with_one) -> None:
    w, tool = sig
    qtbot.mouseMove(w.page_view.viewport(), _vp(w, 0, CELL_CLICK))
    expected = tool.placement(0, CELL_CLICK, False, _aspect(store_with_one))
    assert tool.preview is not None and tool.preview[0] == 0
    assert tool.preview[1] == expected


def test_drag_on_empty_space_places_dragged_width(qtbot, sig, store_with_one) -> None:
    w, tool = sig
    start = _vp(w, 0, EMPTY)
    _drag_px(qtbot, w, start, 120, 30)
    assert w.undo_stack.count() == 1
    assert isinstance(w.undo_stack.command(0), AddAnnotCommand)
    (info,) = _annots(w)
    scale = w.page_view.view_scale
    tol = 1.5 / scale + 0.1
    assert info.rect.left() == pytest.approx(EMPTY.x(), abs=tol)
    assert info.rect.top() == pytest.approx(EMPTY.y(), abs=tol)
    assert info.rect.width() == pytest.approx(120 / scale, abs=tol)
    assert info.rect.width() / info.rect.height() == pytest.approx(
        _aspect(store_with_one), rel=1e-3
    )
    assert tool.preview is None


def test_second_click_selects(qtbot, sig) -> None:
    w, _tool = sig
    info = _place_alt(qtbot, w)
    selection = w.document_view.annot_selection
    selection.clear()
    _click(qtbot, w, info.rect.center())
    assert selection.current is not None and selection.current.name == info.name
    _click(qtbot, w, info.rect.center())  # click_selected: nothing happens
    assert w.undo_stack.count() == 1 and len(_annots(w)) == 1
    assert not w.document_view.annot_editor.is_open


def test_corner_handle_drag_keeps_aspect(qtbot, sig) -> None:
    w, _tool = sig
    info = _place_alt(qtbot, w)
    assert w.document_view.annot_selection.current.name == info.name
    start = _vp(w, 0, info.rect.bottomRight())
    _drag_px(qtbot, w, start, 40, 4)
    assert w.undo_stack.count() == 2
    assert isinstance(w.undo_stack.command(1), EditAnnotCommand)
    (after,) = _annots(w)
    assert after.name == info.name
    assert after.rect.width() > info.rect.width() + 10
    old_aspect = info.rect.width() / info.rect.height()
    assert after.rect.width() / after.rect.height() == pytest.approx(old_aspect, rel=1e-4)
    assert after.rect.topLeft().x() == pytest.approx(info.rect.left(), abs=1e-3)
    assert after.rect.topLeft().y() == pytest.approx(info.rect.top(), abs=1e-3)


def test_delete_undo_redo(qtbot, sig) -> None:
    w, _tool = sig
    info = _place_alt(qtbot, w)
    w.page_view.viewport().setFocus()
    assert w.act_delete_annot.isEnabled()
    w.act_delete_annot.trigger()
    assert _annots(w) == [] and w.undo_stack.count() == 2
    w.undo()
    (back,) = _annots(w)
    assert back.name == info.name and back.kind is AnnotKind.SIGNATURE
    w.redo()
    assert _annots(w) == []


def test_alt_click_ignores_snapping(qtbot, sig, settings, store_with_one) -> None:
    w, _tool = sig
    info = _place_alt(qtbot, w, CELL_CLICK)
    tol = 1.5 / w.page_view.view_scale + 0.1
    assert info.rect.center().x() == pytest.approx(CELL_CLICK.x(), abs=tol)
    assert info.rect.center().y() == pytest.approx(CELL_CLICK.y(), abs=tol)
    assert info.rect.width() == pytest.approx(settings.signature_width, abs=1e-3)
    assert info.rect.height() == pytest.approx(
        settings.signature_width / _aspect(store_with_one), abs=1e-3
    )


def test_form_field_notice(qtbot, window, lo_form_pdf, store_with_one) -> None:
    w = window
    assert w.open_file(str(lo_form_pdf))
    tool = _install(w, store_with_one)
    messages: list[str] = []
    tool.message.connect(messages.append)
    field = next(x for x in w.document_view.document.widgets(0) if x.name == "Zone de texte 8_54")
    _click(qtbot, w, field.rect.center())
    assert messages == [form_field_message()]
    assert w.undo_stack.count() == 0
    _click(qtbot, w, field.rect.center(), modifier=ALT)
    assert w.undo_stack.count() == 1
    assert [a.kind for a in _annots(w)] == [AnnotKind.SIGNATURE]


def test_empty_store_emits_signature_needed(qtbot, window, word_form_pdf, signature_store) -> None:
    w = window
    assert w.open_file(str(word_form_pdf))
    tool = _install(w, signature_store)
    needed: list[bool] = []
    tool.signature_needed.connect(lambda: needed.append(True))
    qtbot.mouseMove(w.page_view.viewport(), _vp(w, 0, EMPTY))
    assert tool.preview is None
    _click(qtbot, w, EMPTY)
    assert needed == [True]
    _drag_px(qtbot, w, _vp(w, 0, EMPTY), 80)
    assert needed == [True, True]
    assert w.undo_stack.count() == 0 and _annots(w) == []


def test_rotated_page_placement_upright(qtbot, window, word_form_rotated_pdf, store_with_one):
    w = window
    assert w.open_file(str(word_form_rotated_pdf))
    doc = w.document_view.document
    assert doc.page_rotation(0) == 90
    _install(w, store_with_one)
    info = _place_alt(qtbot, w, QPointF(500, 300))
    record = store_with_one.get(store_with_one.default_id)
    expected = ImageData.from_qimage(store_with_one.load(record.id), 90)
    upright = ImageData.from_qimage(store_with_one.load(record.id))
    assert info.image_size == (upright.height, upright.width)  # turned for the page
    assert doc.annot_image(0, info.name) == expected
    # Upright on screen: the page-space rect has the image's own aspect.
    assert info.rect.width() / info.rect.height() == pytest.approx(
        record.width / record.height, rel=1e-3
    )


def test_deactivation_clears_selection(qtbot, sig) -> None:
    w, tool = sig
    _place_alt(qtbot, w)
    assert w.document_view.annot_selection.current is not None
    w.tool_manager.set_active("hand")
    assert w.document_view.annot_selection.current is None
    assert tool.preview is None


def test_unreadable_image_reports(qtbot, sig, store_with_one) -> None:
    w, tool = sig
    messages: list[str] = []
    tool.message.connect(messages.append)
    store_with_one.path(store_with_one.default_id).write_bytes(b"not a png")
    _click(qtbot, w, EMPTY, modifier=ALT)
    assert messages == ["The image could not be read."]
    assert w.undo_stack.count() == 0


def test_cached_image_dropped_on_store_change(qtbot, sig, store_with_one, signature_png) -> None:
    w, tool = sig
    _place_alt(qtbot, w)
    assert tool._images
    store_with_one.add("Second", QImage(str(signature_png)))
    assert not tool._images
