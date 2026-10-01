"""M4-T6: signature import and manager dialogs (offscreen, modal helpers patched)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fixtures import SIG_STROKE_POINTS
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDropEvent, QImage
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

from pdfeditor.ui import dialogs, signature_dialogs
from pdfeditor.ui.signature_dialogs import (
    CHECKER_DARK,
    CHECKER_LIGHT,
    SignatureImportDialog,
    SignatureManagerDialog,
)

PRIVACY = (
    "The signature is stored only on this computer. "
    "Anyone who receives a signed PDF can copy the image from it."
)


@pytest.fixture
def warnings(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        dialogs, "warn", lambda parent, title, text, details=None: calls.append(text)
    )
    return calls


@pytest.fixture
def blank_png(tmp_path):
    path = tmp_path / "blank.png"
    image = QImage(300, 120, QImage.Format.Format_RGB32)
    image.fill(QColor(255, 255, 255))
    assert image.save(str(path))
    return path


def _import_dialog(qtbot, store, path=None) -> SignatureImportDialog:
    dialog = SignatureImportDialog(store, None, str(path) if path else None)
    qtbot.addWidget(dialog)
    return dialog


def _preview_pixel(dialog: SignatureImportDialog, fx: float, fy: float) -> QColor:
    target = dialog.preview_target
    point = QPointF(target.x() + fx * target.width(), target.y() + fy * target.height())
    return dialog.preview_image.pixelColor(int(point.x()), int(point.y()))


def _alpha_sum(dialog: SignatureImportDialog) -> int:
    return sum(dialog.processed.alpha)


# -- import dialog ---------------------------------------------------------------
def test_preset_path_shows_preview_on_checkerboard(qtbot, signature_store, signature_photo):
    dialog = _import_dialog(qtbot, signature_store, signature_photo)
    dialog.crop_check.setChecked(False)
    dialog.refresh_preview()
    assert dialog.processed is not None
    assert not dialog.preview_target.isEmpty()
    assert not dialog.preview_label.pixmap().isNull()
    checker = {CHECKER_LIGHT.rgb(), CHECKER_DARK.rgb()}
    for fx, fy in SIG_STROKE_POINTS["paper"]:
        assert _preview_pixel(dialog, fx, fy).rgb() in checker, (fx, fy)
    for fx, fy in SIG_STROKE_POINTS["ink"]:
        assert _preview_pixel(dialog, fx, fy).lightness() < 140, (fx, fy)
    # Both checker colours appear (transparency is visible).
    assert dialog.preview_target.top() > 2
    assert {dialog.preview_image.pixelColor(x, 2).rgb() for x in range(16)} == checker


def test_threshold_defaults_to_otsu_and_slider_changes_alpha(
    qtbot, signature_store, signature_photo
):
    dialog = _import_dialog(qtbot, signature_store, signature_photo)
    otsu = dialog._prepared.otsu_threshold
    assert dialog.threshold_slider.value() == otsu
    before = _alpha_sum(dialog)
    dialog.threshold_slider.setValue(max(0, otsu - 60))
    assert dialog.preview_timer.isActive()  # debounced
    qtbot.waitUntil(lambda: not dialog.preview_timer.isActive())
    assert _alpha_sum(dialog) < before
    assert dialog.threshold_value.text() == str(max(0, otsu - 60))


def test_ok_adds_record_named_from_file_stem(qtbot, signature_store, signature_photo, warnings):
    dialog = _import_dialog(qtbot, signature_store, signature_photo)
    assert dialog.name_edit.text() == "signature"
    assert dialog.ok_button().isEnabled()
    qtbot.mouseClick(dialog.ok_button(), Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert warnings == []
    records = signature_store.records()
    assert [r.name for r in records] == ["signature"]
    assert dialog.result_record == records[0]
    image = signature_store.load(records[0].id)
    assert image is not None and image.hasAlphaChannel()
    # Cropped to the ink, with transparent paper.
    assert image.width() < 1000
    assert image.pixelColor(0, 0).alpha() == 0


def test_ok_uses_edited_name_and_options(qtbot, signature_store, signature_photo):
    dialog = _import_dialog(qtbot, signature_store, signature_photo)
    dialog.name_edit.setText("  Corentin  ")
    dialog.color_combo.setCurrentIndex(dialog.color_combo.findData("black"))
    dialog.accept()
    record = dialog.result_record
    assert record is not None and record.name == "Corentin"
    assert set(dialog.processed.rgb) == {0}


def test_empty_name_disables_ok(qtbot, signature_store, signature_photo):
    dialog = _import_dialog(qtbot, signature_store, signature_photo)
    dialog.name_edit.setText("   ")
    assert not dialog.ok_button().isEnabled()
    dialog.accept()
    assert signature_store.records() == []


def test_cancel_adds_nothing(qtbot, signature_store, signature_photo):
    dialog = _import_dialog(qtbot, signature_store, signature_photo)
    dialog.buttons.button(dialog.buttons.StandardButton.Cancel).click()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.result_record is None
    assert signature_store.records() == []


def test_unreadable_file_warns_and_keeps_dialog_open(
    qtbot, tmp_path, signature_store, signature_photo, warnings
):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not an image")
    dialog = _import_dialog(qtbot, signature_store, bad)
    dialog.show()
    assert warnings == ["The image could not be read."]
    assert dialog.processed is None
    assert not dialog.ok_button().isEnabled()
    assert dialog.isVisible()
    # A good image, then a bad one: the good one stays.
    assert dialog.load(str(signature_photo))
    assert not dialog.load(str(bad))
    assert dialog.image_path == str(signature_photo)
    assert dialog.processed is not None
    assert dialog.isVisible()


def test_no_ink_warns_instead_of_adding(qtbot, signature_store, blank_png, warnings):
    dialog = _import_dialog(qtbot, signature_store, blank_png)
    dialog.show()
    dialog.threshold_slider.setValue(0)
    dialog.accept()
    assert warnings == ["No ink was found in the image; raise the threshold."]
    assert signature_store.records() == []
    assert dialog.isVisible()
    assert dialog.result_record is None


def test_browse_uses_get_image_path(qtbot, monkeypatch, signature_store, signature_png):
    asked = []
    monkeypatch.setattr(
        dialogs, "get_image_path", lambda parent, d: asked.append(d) or str(signature_png)
    )
    dialog = _import_dialog(qtbot, signature_store)
    assert not dialog.ok_button().isEnabled()
    dialog.browse_button.click()
    assert asked == [""]
    assert dialog.image_path == str(signature_png)
    assert dialog.processed is not None and dialog.processed.ink_bbox is not None
    # Cancelled chooser: nothing changes; the directory of the current file is offered.
    monkeypatch.setattr(dialogs, "get_image_path", lambda parent, d: asked.append(d) or None)
    dialog.browse_button.click()
    assert asked[-1] == str(signature_png.parent)
    assert dialog.image_path == str(signature_png)


def test_drop_loads_image(qtbot, signature_store, signature_png):
    dialog = _import_dialog(qtbot, signature_store)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(signature_png))])
    event = QDropEvent(
        QPointF(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    dialog.dropEvent(event)
    assert Path(dialog.image_path) == signature_png
    assert dialog.processed is not None


def test_name_follows_file_until_edited(
    qtbot, tmp_path, signature_store, signature_photo, signature_png
):
    dialog = _import_dialog(qtbot, signature_store, signature_photo)
    other = signature_png.with_name("other.png")
    other.write_bytes(signature_png.read_bytes())
    dialog.load(str(other))
    assert dialog.name_edit.text() == "other"
    dialog.name_edit.setText("Mine")
    dialog.load(str(signature_photo))
    assert dialog.name_edit.text() == "Mine"


def test_even_out_paper_toggle_reprepares(qtbot, signature_store, signature_photo):
    dialog = _import_dialog(qtbot, signature_store, signature_photo)
    first = dialog._prepared
    dialog.even_paper_check.setChecked(False)
    assert dialog._prepared is not first
    assert dialog.threshold_slider.value() == dialog._prepared.otsu_threshold


def test_privacy_note_present(qtbot, signature_store):
    dialog = _import_dialog(qtbot, signature_store)
    assert dialog.privacy_label.text() == PRIVACY
    assert dialog.windowTitle() == "Import Signature"


def test_import_signature_helper(qtbot, monkeypatch, signature_store, signature_png):
    def fake_exec(self):
        self.accept()
        return self.result()

    monkeypatch.setattr(SignatureImportDialog, "exec", fake_exec)
    record = signature_dialogs.import_signature(signature_store, None, str(signature_png))
    assert record is not None and signature_store.get(record.id) == record
    monkeypatch.setattr(SignatureImportDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert signature_dialogs.import_signature(signature_store, None, str(signature_png)) is None
    assert len(signature_store.records()) == 1


# -- manager dialog ----------------------------------------------------------------
@pytest.fixture
def store_with_two(store_with_one, signature_png):
    store_with_one.add("Initials", QImage(str(signature_png)))
    return store_with_one


def _manager(qtbot, store) -> SignatureManagerDialog:
    dialog = SignatureManagerDialog(store)
    qtbot.addWidget(dialog)
    return dialog


def _names(dialog: SignatureManagerDialog) -> list[str]:
    return [dialog.list.item(i).text() for i in range(dialog.list.count())]


def _bold(dialog: SignatureManagerDialog) -> list[str]:
    items = (dialog.list.item(i) for i in range(dialog.list.count()))
    return [item.text() for item in items if item.font().bold()]


def test_manager_lists_records_with_thumbnails(qtbot, store_with_two):
    dialog = _manager(qtbot, store_with_two)
    assert _names(dialog) == ["My signature", "Initials"]
    assert _bold(dialog) == ["My signature"]
    for i in range(dialog.list.count()):
        assert not dialog.list.item(i).icon().isNull()
    assert dialog.list.iconSize().width() == 120
    assert dialog.current_id() == store_with_two.records()[0].id
    assert not dialog.default_button.isEnabled()  # already the default


def test_manager_rename(qtbot, monkeypatch, store_with_two):
    asked = []
    monkeypatch.setattr(
        dialogs, "ask_text", lambda p, title, label, initial: asked.append(initial) or "Signed"
    )
    dialog = _manager(qtbot, store_with_two)
    dialog.list.setCurrentRow(1)
    dialog.rename_button.click()
    assert asked == ["Initials"]
    assert [r.name for r in store_with_two.records()] == ["My signature", "Signed"]
    assert _names(dialog) == ["My signature", "Signed"]
    assert dialog.list.currentRow() == 1
    # Cancel or an empty name: unchanged.
    for answer in (None, "  "):
        monkeypatch.setattr(dialogs, "ask_text", lambda p, t, lbl, i, a=answer: a)
        dialog.rename_button.click()
    assert _names(dialog) == ["My signature", "Signed"]


def test_manager_delete_confirms(qtbot, monkeypatch, store_with_two):
    asked = []
    monkeypatch.setattr(
        dialogs, "confirm_delete_signature", lambda p, name: asked.append(name) or False
    )
    dialog = _manager(qtbot, store_with_two)
    dialog.delete_button.click()
    assert asked == ["My signature"]
    assert len(store_with_two.records()) == 2
    monkeypatch.setattr(dialogs, "confirm_delete_signature", lambda p, name: True)
    dialog.delete_button.click()
    assert [r.name for r in store_with_two.records()] == ["Initials"]
    assert _names(dialog) == ["Initials"]
    assert _bold(dialog) == ["Initials"]  # default fell back
    dialog.delete_button.click()
    assert dialog.list.count() == 0
    assert not dialog.delete_button.isEnabled()
    assert not dialog.rename_button.isEnabled()
    assert not dialog.default_button.isEnabled()


def test_manager_set_default(qtbot, store_with_two):
    dialog = _manager(qtbot, store_with_two)
    dialog.list.setCurrentRow(1)
    assert dialog.default_button.isEnabled()
    dialog.default_button.click()
    assert store_with_two.default_id == store_with_two.records()[1].id
    assert _bold(dialog) == ["Initials"]
    assert dialog.list.currentRow() == 1
    assert not dialog.default_button.isEnabled()


def test_manager_add_uses_import_dialog(qtbot, monkeypatch, store_with_one, signature_png):
    def fake_import(store, parent=None, path=None):
        return store.add("New", QImage(str(signature_png)))

    monkeypatch.setattr(signature_dialogs, "import_signature", fake_import)
    dialog = _manager(qtbot, store_with_one)
    dialog.add_button.click()
    assert _names(dialog) == ["My signature", "New"]
    assert dialog.list.currentRow() == 1
    monkeypatch.setattr(signature_dialogs, "import_signature", lambda s, p=None, path=None: None)
    dialog.add_button.click()
    assert dialog.list.count() == 2


def test_manager_follows_external_store_changes(qtbot, store_with_one, signature_png):
    dialog = _manager(qtbot, store_with_one)
    store_with_one.add("Elsewhere", QImage(str(signature_png)))
    assert _names(dialog) == ["My signature", "Elsewhere"]


def test_manager_store_failure_warns(qtbot, monkeypatch, store_with_two, warnings):
    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(store_with_two, "set_default", fail)
    dialog = _manager(qtbot, store_with_two)
    dialog.list.setCurrentRow(1)
    dialog.default_button.click()
    assert warnings == ["The signature could not be saved."]


def test_manager_close(qtbot, store_with_one):
    dialog = _manager(qtbot, store_with_one)
    dialog.show()
    dialog.buttons.buttons()[0].click()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert not dialog.isVisible()


# -- dialogs.py helpers --------------------------------------------------------------
def test_get_image_path(monkeypatch):
    seen = {}

    def fake(parent, title, directory, filters):
        seen.update(title=title, directory=directory, filters=filters)
        return "C:/x/sig.png", filters

    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake)
    assert dialogs.get_image_path(None, "C:/x") == "C:/x/sig.png"
    assert seen["title"] == "Choose a signature image"
    assert seen["filters"] == "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)"
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: ("", ""))
    assert dialogs.get_image_path(None, "") is None


def test_ask_text(monkeypatch):
    monkeypatch.setattr(
        QInputDialog, "getText", lambda p, t, lbl, mode, initial: (initial + "!", True)
    )
    assert dialogs.ask_text(None, "T", "L", "abc") == "abc!"
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("x", False))
    assert dialogs.ask_text(None, "T", "L", "abc") is None


def test_confirm_delete_signature(monkeypatch):
    seen = []

    def fake(parent, title, text, buttons, default):
        seen.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", fake)
    assert dialogs.confirm_delete_signature(None, "Mine") is True
    assert seen == ["Delete the signature “Mine”?"]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    assert dialogs.confirm_delete_signature(None, "Mine") is False
