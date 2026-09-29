from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence

from pdfeditor.core.settings import Settings
from pdfeditor.render.renderer import RenderKind
from pdfeditor.ui.main_window import MainWindow


@pytest.fixture
def window(qtbot, settings, simple_pdf):
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.resize(1000, 700)
    w.show()
    qtbot.waitExposed(w)
    assert w.open_file(str(simple_pdf))
    w.page_view.set_zoom_percent(100)
    yield w
    w.undo_stack.setClean()
    w.close()


def test_row_count_matches_pages(window: MainWindow) -> None:
    assert window.thumbnail_model.rowCount() == 3
    assert window.thumbnail_model.data(window.thumbnail_model.index(1)) == "2"
    window.close_document()
    assert window.thumbnail_model.rowCount() == 0


def test_thumbs_render_async(qtbot, window: MainWindow) -> None:
    model = window.thumbnail_model
    for row in range(3):
        model.data(model.index(row), Qt.ItemDataRole.DecorationRole)
    qtbot.waitUntil(lambda: all(model.is_rendered(r) for r in range(3)), timeout=5000)
    pm = model.data(model.index(1), Qt.ItemDataRole.DecorationRole)
    assert pm.width() == pytest.approx(140, abs=5)  # scale quantized to 1/64
    assert pm.height() / pm.width() == pytest.approx(612 / 792, abs=0.02)


def test_click_row_scrolls(qtbot, window: MainWindow) -> None:
    sidebar = window.thumbnails
    rect = sidebar.visualRect(window.thumbnail_model.index(2))
    assert rect.isValid()
    qtbot.mouseClick(sidebar.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert window.page_view.current_page == 2


def test_scrolling_updates_selection(window: MainWindow) -> None:
    window.page_view.scroll_to_page(1)
    assert window.thumbnails.currentIndex().row() == 1
    window.page_view.scroll_to_page(2)
    assert window.thumbnails.currentIndex().row() == 2
    assert window.page_view.current_page == 2


def test_rotated_page_thumb_rerenders(qtbot, window: MainWindow) -> None:
    model = window.thumbnail_model
    idx = model.index(0)
    model.data(idx, Qt.ItemDataRole.DecorationRole)
    qtbot.waitUntil(lambda: model.is_rendered(0), timeout=5000)
    before = model.data(idx, Qt.ItemDataRole.DecorationRole)
    assert before.height() > before.width()
    with qtbot.waitSignal(model.dataChanged):
        window.document_view.document.set_page_rotation(0, 90)
    assert not model.is_rendered(0)
    model.data(idx, Qt.ItemDataRole.DecorationRole)
    qtbot.waitUntil(lambda: model.is_rendered(0), timeout=5000)
    after = model.data(idx, Qt.ItemDataRole.DecorationRole)
    assert after.width() > after.height()
    service = window.page_view.service
    assert service.pixmap(0, model.thumb_scale(0), RenderKind.THUMB) is not None


def test_f4_toggles_and_persists(qtbot, window: MainWindow, ini_path) -> None:
    act = window.act_thumbnails
    assert act.shortcut() == QKeySequence("F4")
    assert window.thumbnails_dock.isVisible()
    act.trigger()
    assert not window.thumbnails_dock.isVisible()
    assert window.settings.thumbnails_visible is False
    window.close()

    w2 = MainWindow(Settings(QSettings(str(ini_path), QSettings.Format.IniFormat)))
    qtbot.addWidget(w2)
    w2.show()
    assert not w2.thumbnails_dock.isVisible()
    w2.act_thumbnails.trigger()
    assert w2.thumbnails_dock.isVisible()
    assert w2.settings.thumbnails_visible is True
    w2.close()


def test_save_as_and_full_save_keep_caches(qtbot, window: MainWindow, tmp_path) -> None:
    model = window.thumbnail_model
    for row in range(3):
        model.data(model.index(row), Qt.ItemDataRole.DecorationRole)
    qtbot.waitUntil(lambda: all(model.is_rendered(r) for r in range(3)), timeout=5000)
    cache = window.page_view.service.cache
    entries = len(cache)
    doc = window.document_view.document
    with qtbot.assertNotEmitted(model.modelReset):
        doc.save_as(tmp_path / "copy.pdf")
        doc.save(force_full=True)
    assert len(cache) == entries
    assert all(model.is_rendered(r) for r in range(3))


def test_content_change_keeps_old_thumbnail(qtbot, window: MainWindow) -> None:
    model = window.thumbnail_model
    idx = model.index(0)
    model.data(idx, Qt.ItemDataRole.DecorationRole)
    qtbot.waitUntil(lambda: model.is_rendered(0), timeout=5000)
    doc = window.document_view.document
    with doc.lock:
        doc.fitz[0].draw_rect((300, 300, 400, 400), color=(0, 0, 0), fill=(0, 0, 0))
        doc.page_changed.emit(0)
        assert not model.is_rendered(0)
        pm = model.data(idx, Qt.ItemDataRole.DecorationRole)
        img = pm.toImage()
        # the fixture's blue box, not a blank placeholder
        c = img.pixelColor(int(img.width() * 100 / 595), int(img.height() * 120 / 842))
        assert c.blue() > 150 and c.red() < 150
    qtbot.waitUntil(lambda: model.is_rendered(0), timeout=5000)
