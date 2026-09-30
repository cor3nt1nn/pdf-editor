from __future__ import annotations

import pytest
import shiboken6
from fixtures import LO_EDITABLE
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QGraphicsItem

from pdfeditor.core.commands import RotatePageCommand
from pdfeditor.core.document import PdfDocument
from pdfeditor.core.settings import Settings
from pdfeditor.ui.document_view import DocumentView
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.overlays.field_items import FieldHitItem


@pytest.fixture
def dv(qtbot):
    view = DocumentView()
    qtbot.addWidget(view)
    view.resize(800, 600)
    yield view
    view.undo_stack.setClean()
    view.shutdown()


def _editable(doc: PdfDocument) -> list:
    return [w for w in doc.all_widgets() if w.editable]


def _assert_geometry(dv: DocumentView) -> None:
    layer = dv.field_layer
    scene = dv.page_view.doc_scene
    for item in layer.items():
        info = item.info
        page_item = dv.page_view.page_item(info.page)
        assert item.parentItem() is page_item
        expected = info.rect.translated(scene.page_offset(info.page))
        got = item.sceneBoundingRect()
        # The cosmetic pen adds no width in page space; allow float noise only.
        assert got.x() == pytest.approx(expected.x(), abs=1e-6)
        assert got.y() == pytest.approx(expected.y(), abs=1e-6)
        assert got.width() == pytest.approx(expected.width(), abs=1e-6)
        assert got.height() == pytest.approx(expected.height(), abs=1e-6)


def test_one_item_per_editable_widget(qtbot, dv, lo_form_pdf) -> None:
    doc = dv.open(str(lo_form_pdf))
    items = dv.field_layer.items()
    assert len(items) == len(_editable(doc)) == len(LO_EDITABLE) == 13
    assert all(isinstance(i, FieldHitItem) and i.info.editable for i in items)
    item = items[0]
    assert item.zValue() == 1
    assert item.acceptedMouseButtons() == Qt.MouseButton.NoButton
    assert not item.acceptHoverEvents()
    assert item.pen().isCosmetic()
    assert item.brush().color().alpha() == 40
    assert not item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
    _assert_geometry(dv)


def test_geometry_on_rotated_page(qtbot, dv, lo_form_rotated_pdf) -> None:
    doc = dv.open(str(lo_form_rotated_pdf))
    assert doc.page_rotation(0) == 90
    assert len(dv.field_layer.items()) == 13
    _assert_geometry(dv)


def test_rotation_rebuilds_only_that_page(qtbot, dv, lo_form_pdf) -> None:
    doc = dv.open(str(lo_form_pdf))
    before = {p: dv.field_layer.items(p) for p in range(doc.page_count)}
    assert len(before) == 2 and all(before.values())
    dv.undo_stack.push(RotatePageCommand(doc, 0, 90))
    after0 = dv.field_layer.items(0)
    assert len(after0) == len(before[0])
    assert not set(map(id, after0)) & set(map(id, before[0]))
    assert dv.field_layer.items(1) == before[1]  # untouched page keeps its items
    assert all(item.scene() is None for item in before[0])
    _assert_geometry(dv)
    dv.undo_stack.undo()
    _assert_geometry(dv)


def test_structure_change_rebuilds_on_new_page_items(qtbot, dv, lo_form_pdf) -> None:
    doc = dv.open(str(lo_form_pdf))
    old_items = dv.field_layer.items()
    old_page = dv.page_view.page_item(0)
    doc.structure_changed.emit()
    assert dv.page_view.page_item(0) is not old_page
    assert len(dv.field_layer.items()) == 13
    assert not any(shiboken6.isValid(i) and i.scene() is not None for i in old_items)
    _assert_geometry(dv)


def test_rebuild_after_save_reload(qtbot, dv, lo_form_pdf, tmp_path) -> None:
    doc = dv.open(str(lo_form_pdf))
    old_items = dv.field_layer.items()
    with qtbot.waitSignal(doc.reloaded):
        dv.save_as(str(tmp_path / "copy.pdf"))
    items = dv.field_layer.items()
    assert len(items) == 13
    assert not set(map(id, items)) & set(map(id, old_items))
    _assert_geometry(dv)


def test_set_visible_hides_and_survives_rebuild(qtbot, dv, lo_form_pdf) -> None:
    doc = dv.open(str(lo_form_pdf))
    dv.field_layer.set_visible(False)
    assert not any(i.isVisible() for i in dv.field_layer.items())
    doc.structure_changed.emit()
    assert dv.field_layer.items() and not any(i.isVisible() for i in dv.field_layer.items())
    dv.field_layer.set_visible(True)
    assert all(i.isVisible() for i in dv.field_layer.items())


def test_no_highlights_without_fill_permission(qtbot, dv, owner_locked_pdf) -> None:
    doc = dv.open(str(owner_locked_pdf))
    assert doc.is_form and not doc.can_fill_forms
    assert dv.field_layer.items() == []


def test_no_highlights_for_plain_document_and_after_close(
    qtbot, dv, simple_pdf, lo_form_pdf
) -> None:
    dv.open(str(simple_pdf))
    assert dv.field_layer.items() == []
    dv.open(str(lo_form_pdf))
    assert len(dv.field_layer.items()) == 13
    dv.close_document()
    assert dv.field_layer.items() == []


def test_menu_toggle_hides_and_persists(qtbot, settings, ini_path, lo_form_pdf) -> None:
    assert settings.highlight_fields
    w = MainWindow(settings)
    qtbot.addWidget(w)
    act = w.act_highlight_fields
    assert act.isCheckable() and act.isChecked()
    assert act in w.menu_view.actions()
    assert w.open_file(str(lo_form_pdf))
    layer = w.document_view.field_layer
    assert len(layer.items()) == 13 and all(i.isVisible() for i in layer.items())
    act.trigger()
    assert not act.isChecked()
    assert not any(i.isVisible() for i in layer.items())
    assert not settings.highlight_fields
    w.close()

    w2 = MainWindow(Settings(QSettings(str(ini_path), QSettings.Format.IniFormat)))
    qtbot.addWidget(w2)
    assert not w2.act_highlight_fields.isChecked()
    assert w2.open_file(str(lo_form_pdf))
    assert not any(i.isVisible() for i in w2.document_view.field_layer.items())
    w2.close()
