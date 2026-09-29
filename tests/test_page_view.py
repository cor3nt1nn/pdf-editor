from __future__ import annotations

import pytest
from fixtures import SIMPLE_SIZES
from PySide6.QtCore import QPoint, QPointF, QRectF, QSizeF

from pdfeditor.constants import BASE_SCALE, MARGIN_PT, PAGE_GAP_PT, ZoomMode
from pdfeditor.core.document import PdfDocument
from pdfeditor.ui.page_view import PageView


@pytest.fixture
def doc(simple_pdf):
    d = PdfDocument.open(simple_pdf)
    yield d
    d.close()


@pytest.fixture
def view(qtbot, doc):
    v = PageView()
    qtbot.addWidget(v)
    v.resize(800, 600)
    v.show()
    qtbot.waitExposed(v)
    v.set_document(doc)
    yield v
    v.service.stop()


def test_offsets_stack_and_center(view: PageView) -> None:
    scene = view.doc_scene
    max_w = max(w for w, _ in SIMPLE_SIZES)
    y = MARGIN_PT
    for i, (w, h) in enumerate(SIMPLE_SIZES):
        pos = scene.page_offset(i)
        assert pos.y() == pytest.approx(y)
        assert pos.x() == pytest.approx((max_w - w) / 2 + MARGIN_PT)
        y += h + PAGE_GAP_PT
    assert scene.sceneRect().width() == pytest.approx(max_w + 2 * MARGIN_PT)
    assert scene.sceneRect().height() == pytest.approx(y - PAGE_GAP_PT + MARGIN_PT)
    # Narrower A4 page is centered on the widest (Letter landscape) page
    a4 = scene.page_scene_rect(0)
    letter = scene.page_scene_rect(1)
    assert a4.center().x() == pytest.approx(letter.center().x())


def test_fit_width_changes_on_resize(qtbot, view: PageView) -> None:
    assert view.zoom_mode == ZoomMode.FIT_WIDTH
    z1 = view.zoom_percent
    expected = view.viewport().width() / ((792 + 2 * MARGIN_PT) * BASE_SCALE) * 100
    assert z1 == pytest.approx(expected)
    with qtbot.waitSignal(view.zoom_changed) as blocker:
        view.resize(1100, 600)
    assert view.zoom_percent > z1
    assert blocker.args[1] == ZoomMode.FIT_WIDTH
    assert view.transform().m11() == pytest.approx(view.zoom_percent / 100 * BASE_SCALE)


def test_fit_page(view: PageView) -> None:
    view.set_zoom_mode(ZoomMode.FIT_PAGE)
    page_px_h = SIMPLE_SIZES[0][1] * view.view_scale
    assert page_px_h <= view.viewport().height()
    assert view.zoom_mode == ZoomMode.FIT_PAGE


def test_scroll_to_page_emits(qtbot, view: PageView) -> None:
    view.set_zoom_percent(100)
    with qtbot.waitSignal(view.current_page_changed) as blocker:
        view.scroll_to_page(2)
    assert blocker.args == [2]
    assert view.current_page == 2
    with qtbot.waitSignal(view.current_page_changed) as blocker:
        view.scroll_to_page(1)
    assert blocker.args == [1]
    top_left = view.mapToScene(QPoint(0, 0))
    assert top_left.y() == pytest.approx(view.doc_scene.page_offset(1).y() - PAGE_GAP_PT / 2, abs=2)


def test_scrolling_updates_current_page(qtbot, view: PageView) -> None:
    view.set_zoom_percent(100)
    view.scroll_to_page(0)
    bar = view.verticalScrollBar()
    with qtbot.waitSignal(view.current_page_changed):
        bar.setValue(bar.maximum())
    assert view.current_page == 2
    view.scroll_to_page(0)
    assert view.current_page == 0


def test_zoom_clamped_and_steps(qtbot, view: PageView) -> None:
    view.set_zoom_percent(1000)
    assert view.zoom_percent == 400
    assert view.zoom_mode == ZoomMode.CUSTOM
    view.set_zoom_percent(1)
    assert view.zoom_percent == 25
    view.zoom_out()
    assert view.zoom_percent == 25
    view.set_zoom_percent(100)
    view.zoom_in()
    assert view.zoom_percent == 125
    view.zoom_out()
    view.zoom_out()
    assert view.zoom_percent == 75
    view.set_zoom_percent(110)
    view.zoom_in()
    assert view.zoom_percent == 125
    view.set_zoom_percent(400)
    view.zoom_in()
    assert view.zoom_percent == 400


def test_rotation_changes_bounding_rect_and_shifts(qtbot, view: PageView, doc) -> None:
    item0 = view.page_item(0)
    assert item0.boundingRect() == QRectF(0, 0, 595, 842)
    y1_before = view.doc_scene.page_offset(1).y()
    doc.set_page_rotation(0, 90)
    assert item0.boundingRect() == QRectF(0, 0, 842, 595)
    assert item0.size == QSizeF(842, 595)
    y1_after = view.doc_scene.page_offset(1).y()
    assert y1_after == pytest.approx(y1_before - (842 - 595))


def test_pages_render_into_cache(qtbot, view: PageView) -> None:
    view.set_zoom_percent(50)
    view.scroll_to_page(0)
    view.viewport().repaint()
    qtbot.waitUntil(lambda: view.page_item(0).last_scale is not None, timeout=3000)
    scale = view.page_item(0).last_scale
    qtbot.waitUntil(lambda: view.service.pixmap(0, scale) is not None, timeout=5000)
    pm = view.service.pixmap(0, scale)
    assert pm.width() == pytest.approx(595 * scale, abs=1)


def test_page_rect_mapping_round_trip(view: PageView) -> None:
    view.set_zoom_percent(100)
    view.scroll_to_page(1)
    rect = view.page_rect_to_viewport(1, QRectF(100, 100, 50, 20))
    assert rect.width() == pytest.approx(50 * BASE_SCALE, abs=2)
    hit = view.viewport_to_page(rect.center())
    assert hit is not None
    page, pos = hit
    assert page == 1
    assert pos.x() == pytest.approx(125, abs=1.5)
    assert pos.y() == pytest.approx(110, abs=1.5)


def test_set_document_none(view: PageView) -> None:
    view.set_document(None)
    assert view.page_count == 0
    assert view.viewport_to_page(QPoint(10, 10)) is None
    assert isinstance(view.doc_scene.sceneRect().topLeft(), QPointF)
