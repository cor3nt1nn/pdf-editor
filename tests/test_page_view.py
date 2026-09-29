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


def test_fit_page(qtbot, view: PageView) -> None:
    vw, vh = view.viewport().width(), view.viewport().height()
    for page in (0, 1):  # A4 portrait (height-bound), Letter landscape (width-bound here)
        view.set_zoom_percent(100)
        view.scroll_to_page(page)
        view.set_zoom_mode(ZoomMode.FIT_PAGE)
        assert view.zoom_mode == ZoomMode.FIT_PAGE
        assert view.current_page == page
        w, h = SIMPLE_SIZES[page]
        zoom_w = vw / ((w + 2 * MARGIN_PT) * BASE_SCALE) * 100
        zoom_h = vh / ((h + 2 * PAGE_GAP_PT) * BASE_SCALE) * 100
        assert view.zoom_percent == pytest.approx(min(zoom_w, zoom_h))
        # The whole page is visible in the viewport.
        rect = view.page_rect_to_viewport(page, QRectF(0, 0, w, h))
        assert view.viewport().rect().adjusted(-1, -1, 1, 1).contains(rect), (page, rect)
        # ...and it fills the limiting dimension.
        if zoom_h < zoom_w:
            assert rect.height() == pytest.approx(h * view.view_scale, abs=2)
            assert rect.height() > 0.9 * vh
        else:
            assert rect.width() > 0.9 * vw


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


def _wait_rendered(qtbot, view: PageView, page: int) -> float:
    view.viewport().repaint()
    qtbot.waitUntil(lambda: view.page_item(page).last_scale is not None, timeout=3000)
    scale = view.page_item(page).last_scale
    qtbot.waitUntil(lambda: view.service.pixmap(page, scale) is not None, timeout=5000)
    return scale


def test_content_change_keeps_scroll_and_stale_pixmap(qtbot, view: PageView, doc) -> None:
    view.set_zoom_percent(100)
    view.scroll_to_page(1)
    bar = view.verticalScrollBar()
    bar.setValue(bar.value() + 40)
    scale = _wait_rendered(qtbot, view, 1)
    old = view.service.pixmap(1, scale)
    before = bar.value()
    # Blue box drawn by the fixture on every page at (60, 90)-(200, 160).
    probe = view.page_rect_to_viewport(1, QRectF(100, 110, 20, 20)).center()
    with doc.lock:  # blocks the render worker: the re-render cannot arrive yet
        doc.fitz[1].draw_rect((300, 300, 400, 400), color=(0, 0, 0), fill=(0, 0, 0))
        doc.page_changed.emit(1)  # content only: same size
        assert bar.value() == before
        assert view.service.pixmap(1, scale) is None
        assert view.service.is_stale(1)
        assert view.service.best(1) is old
        color = view.viewport().grab().toImage().pixelColor(probe)
        assert color.blue() > 150 and color.red() < 150  # old pixmap painted, no white flash
    qtbot.waitUntil(lambda: view.service.pixmap(1, scale) is not None, timeout=5000)
    assert not view.service.is_stale(1)
    assert bar.value() == before


def test_rotating_page_above_keeps_view_anchor(qtbot, view: PageView, doc) -> None:
    view.set_zoom_percent(100)
    view.scroll_to_page(2)
    bar = view.verticalScrollBar()
    bar.setValue(bar.value() - 30)
    assert view.current_page == 1 or view.current_page == 2
    current = view.current_page
    top = view.mapToScene(QPoint(0, 0)).y() - view.doc_scene.page_offset(current).y()
    doc.set_page_rotation(0, 90)  # page 0 gets shorter: everything below moves up
    assert view.current_page == current
    new_top = view.mapToScene(QPoint(0, 0)).y() - view.doc_scene.page_offset(current).y()
    assert new_top == pytest.approx(top, abs=1)


def test_rotating_current_page_keeps_it_current(qtbot, view: PageView, doc) -> None:
    view.set_zoom_percent(100)
    view.scroll_to_page(1)
    doc.set_page_rotation(1, 90)
    assert view.page_item(1).size == QSizeF(612, 792)
    assert view.current_page == 1
    top = view.mapToScene(QPoint(0, 0)).y()
    assert top == pytest.approx(view.doc_scene.page_offset(1).y() - PAGE_GAP_PT / 2, abs=2)


@pytest.fixture
def rotated_view(qtbot, tmp_path):
    from fixtures import make_cropped_form_pdf

    d = PdfDocument.open(make_cropped_form_pdf(tmp_path / "crop90.pdf", 90))
    v = PageView()
    qtbot.addWidget(v)
    v.resize(900, 700)
    v.show()
    qtbot.waitExposed(v)
    v.set_document(d)
    v.set_zoom_percent(100)
    yield v, d
    v.service.stop()
    d.close()


def test_mapping_on_rotated_cropped_page_matches_pixels(qtbot, rotated_view) -> None:
    from test_document import assert_rect_matches_red_pixels

    view, doc = rotated_view
    [(_name, widget_rect)] = doc.widget_rects(0)
    scale = _wait_rendered(qtbot, view, 0)
    assert scale == pytest.approx(BASE_SCALE, abs=1 / 64)
    view.viewport().repaint()
    image = view.viewport().grab().toImage()
    rect = view.page_rect_to_viewport(0, widget_rect)
    assert rect.width() == pytest.approx(widget_rect.width() * BASE_SCALE, abs=2)
    assert_rect_matches_red_pixels(image, QRectF(rect))


def test_viewport_to_page_on_rotated_page(qtbot, rotated_view) -> None:
    view, doc = rotated_view
    [(_name, widget_rect)] = doc.widget_rects(0)
    center = view.page_rect_to_viewport(0, widget_rect).center()
    hit = view.viewport_to_page(center)
    assert hit is not None
    page, pos = hit
    assert page == 0
    assert pos.x() == pytest.approx(widget_rect.center().x(), abs=1.5)
    assert pos.y() == pytest.approx(widget_rect.center().y(), abs=1.5)
    # Rotated page space: x runs along the 702 pt (rotated) width.
    corner = view.page_rect_to_viewport(0, QRectF(690, 500, 1, 1)).center()
    hit = view.viewport_to_page(corner)
    assert hit is not None and hit[0] == 0
    assert hit[1].x() == pytest.approx(690.5, abs=1.5)
    outside = view.page_rect_to_viewport(0, QRectF(705, 100, 1, 1)).center()
    assert view.viewport_to_page(outside) is None
