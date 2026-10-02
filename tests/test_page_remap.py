"""M6a-T3: render cache remapping and PageView position across structural changes."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QSizeF, Qt

from pdfeditor.core.document import PdfDocument
from pdfeditor.render.renderer import RenderKind
from pdfeditor.render.service import RenderService
from pdfeditor.ui.page_view import PageView, remapped_current_page
from pdfeditor.ui.thumbnails import ThumbnailModel


@pytest.fixture
def doc(many_pages_pdf):
    d = PdfDocument.open(many_pages_pdf)
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
    v.set_zoom_percent(50)
    yield v
    v.service.stop()


def _wait_rendered(qtbot, view: PageView, page: int) -> float:
    view.scroll_to_page(page)
    view.viewport().repaint()
    qtbot.waitUntil(lambda: view.page_item(page).last_scale is not None, timeout=3000)
    scale = view.page_item(page).last_scale
    qtbot.waitUntil(lambda: view.service.pixmap(page, scale) is not None, timeout=5000)
    return scale


def _offset_in_page(view: PageView, page: int) -> float:
    return view.mapToScene(QPoint(0, 0)).y() - view.doc_scene.page_offset(page).y()


# -- remapped_current_page ---------------------------------------------------------
def test_target_follows_current_page() -> None:
    t = remapped_current_page([0, None, 1, 2], 3, 3)
    assert (t.page, t.same_spot, t.inserted) == (2, True, False)
    assert remapped_current_page([2, 0, 1], 0, 3).page == 2


def test_target_nearest_survivor_next_then_previous() -> None:
    assert remapped_current_page([0, None, None, 1], 1, 2).page == 1
    t = remapped_current_page([0, 1, None, None], 3, 2)
    assert (t.page, t.same_spot) == (1, False)


def test_target_first_inserted_page() -> None:
    t = remapped_current_page([0, 3, 4], 0, 5)
    assert (t.page, t.inserted) == (1, True)


# -- RenderService ------------------------------------------------------------------
def test_service_remap_moves_cache_and_stale(qtbot, doc) -> None:
    service = RenderService()
    try:
        service.set_document(doc)
        from PySide6.QtGui import QPixmap

        a, b = QPixmap(10, 10), QPixmap(10, 10)
        service.cache.put(0, 1.0, RenderKind.PAGE, a)
        service.cache.put(2, 1.0, RenderKind.THUMB, b)
        service.invalidate_page(2, keep_stale=True)  # b becomes stale
        assert service.is_stale(2, RenderKind.THUMB)
        gen = service.worker.generation(RenderKind.THUMB)
        service.remap_pages([1, None, 0])
        assert service.pixmap(1, 1.0) is a
        assert service.pixmap(0, 1.0) is None
        assert service.is_stale(0, RenderKind.THUMB)
        assert not service.is_stale(2, RenderKind.THUMB)
        assert service.best(0, RenderKind.THUMB) is b
        assert service.worker.generation(RenderKind.THUMB) == gen + 1
    finally:
        service.stop()


# -- PageView ---------------------------------------------------------------------
def test_delete_earlier_page_keeps_current_page_and_spot(qtbot, view, doc) -> None:
    scale = _wait_rendered(qtbot, view, 5)
    bar = view.verticalScrollBar()
    bar.setValue(bar.value() + 20)
    assert view.current_page == 5
    offset = _offset_in_page(view, 5)
    pixmap = view.service.pixmap(5, scale)
    pid = doc.page_id(5)
    with qtbot.waitSignal(view.current_page_changed) as blocker:
        doc.delete_pages([1])
    assert blocker.args == [4]
    assert view.page_count == doc.page_count
    assert view.current_page == 4
    assert doc.page_id(4) == pid
    assert _offset_in_page(view, 4) == pytest.approx(offset, abs=1)
    assert view.service.pixmap(4, scale) is pixmap  # moved, not re-rendered


def test_moved_current_page_is_followed(qtbot, view, doc) -> None:
    view.scroll_to_page(3)
    pid = doc.page_id(3)
    order = doc.page_ids()
    order.remove(pid)
    order.insert(10, pid)
    doc.reorder_pages(order)
    assert doc.page_index(pid) == 10
    assert view.current_page == 10


def test_deleted_current_page_goes_to_nearest_survivor(qtbot, view, doc) -> None:
    view.scroll_to_page(3)
    nxt = doc.page_id(5)
    doc.delete_pages([3, 4])
    assert view.current_page == doc.page_index(nxt) == 3
    last = doc.page_count - 1
    view.scroll_to_page(last)
    prev = doc.page_id(last - 1)
    doc.delete_pages([last])
    assert view.current_page == doc.page_index(prev)


def test_insert_makes_first_inserted_page_current(qtbot, view, doc) -> None:
    view.scroll_to_page(2)
    pid = doc.insert_blank_page(6, QSizeF(300, 400))
    assert view.current_page == doc.page_index(pid) == 6
    assert view.page_item(6).size == QSizeF(300, 400)


def test_undo_delete_snapshot_restores_and_shows_page(qtbot, view, doc) -> None:
    ids = doc.page_ids()
    data = doc.snapshot()
    view.scroll_to_page(4)
    doc.delete_pages([4])
    assert view.current_page == 4  # the next page took its place
    doc.restore_snapshot(data, ids)
    assert doc.page_count == len(ids)
    assert view.current_page == 4  # the restored page is shown again


def test_untouched_thumbnails_stay_rendered_after_delete(qtbot, doc) -> None:
    service = RenderService()
    try:
        model = ThumbnailModel(service)
        service.set_document(doc)
        model.set_document(doc)
        view = PageView(service)
        qtbot.addWidget(view)
        view.set_document(doc)
        for row in range(4):
            model.data(model.index(row), Qt.ItemDataRole.DecorationRole)
        qtbot.waitUntil(lambda: all(model.is_rendered(r) for r in range(4)), timeout=5000)
        doc.delete_pages([1])
        assert model.rowCount() == doc.page_count
        assert model.is_rendered(0)
        assert model.is_rendered(1)  # former page 2
        assert model.is_rendered(2)  # former page 3
    finally:
        service.stop()
