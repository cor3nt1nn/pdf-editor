"""M5-T2: File ▸ Export Copy… (dialog, MainWindow.export_copy)."""

from __future__ import annotations

import os

import pymupdf
import pytest
from fixtures import PASSWORD
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox

import pdfeditor.i18n as i18n
from pdfeditor.core.document import ExportOptions, PdfDocument, SaveError
from pdfeditor.ui import dialogs
from pdfeditor.ui.export_dialog import ExportDialog
from pdfeditor.ui.main_window import MainWindow

FIELD = "Zone de texte 8_54"


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    warnings: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(
        dialogs,
        "warn",
        lambda parent, title, text, details=None: warnings.append((title, text, details)),
    )
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Discard
    )
    monkeypatch.setattr(dialogs, "ask_password", lambda p, n, w: PASSWORD)
    w = MainWindow(settings)
    w.warnings = warnings
    qtbot.addWidget(w)
    w.resize(900, 700)
    w.show()
    qtbot.waitExposed(w)
    yield w
    w.undo_stack.setClean()
    w.close()


@pytest.fixture
def accept(monkeypatch):
    """ExportDialog.exec() accepts at once, after ``tweak(dialog)`` if one is set."""
    state: dict[str, object] = {"tweak": None, "dialogs": []}

    def fake_exec(dialog):
        state["dialogs"].append(dialog)
        if state["tweak"] is not None:
            state["tweak"](dialog)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ExportDialog, "exec", fake_exec)
    return state


@pytest.fixture
def export_to(monkeypatch, tmp_path):
    """dialogs.get_export_path returns ``out.pdf`` in tmp_path; suggestions are recorded."""
    calls: list[str] = []
    target = {"path": str(tmp_path / "out.pdf")}

    def fake(parent, suggested):
        calls.append(suggested)
        return target["path"]

    monkeypatch.setattr(dialogs, "get_export_path", fake)
    target["calls"] = calls
    return target


def _info(window: MainWindow, name: str):
    doc = window.document_view.document
    return [w for w in doc.all_widgets() if w.name == name][0]


def _dialog(window: MainWindow, settings) -> ExportDialog:
    dialog = ExportDialog(window.document_view.document, settings, window)
    return dialog


def test_action_in_file_menu_and_disabled_without_document(window, simple_pdf) -> None:
    act = window.act_export
    assert act.shortcut() == QKeySequence("Ctrl+E")
    assert act.text() == "&Export Copy…"
    actions = window.menu_file.actions()
    assert actions.index(act) == actions.index(window.act_save_as) + 1
    assert not act.isEnabled()
    assert not window.export_copy()
    assert window.open_file(str(simple_pdf))
    assert act.isEnabled()
    window.close_document()
    assert not act.isEnabled()


def test_dialog_defaults_plain_document(window, settings, simple_pdf) -> None:
    assert window.open_file(str(simple_pdf))
    d = _dialog(window, settings)
    assert d.windowTitle() == "Export Copy"
    assert d.flatten_forms_box.isChecked() and d.flatten_forms_box.isEnabled()
    assert d.flatten_annots_box.isChecked() and d.flatten_annots_box.isEnabled()
    assert d.keep_metadata_box.isChecked()
    assert d.keep_encryption_box.isHidden()
    assert "file identifier" in d.keep_metadata_box.toolTip()
    assert d.xfa_note.isHidden()
    assert not d.note.isHidden()
    assert d.note.text().startswith("A copy is written; the open document is not changed.")
    assert d.button_box.button(QDialogButtonBox.StandardButton.Ok).text() == "Export"
    assert d.options() == ExportOptions()


def test_dialog_owner_locked_keeps_encryption(window, settings, owner_locked_pdf) -> None:
    settings.export_keep_encryption = False
    assert window.open_file(str(owner_locked_pdf))
    d = _dialog(window, settings)
    box = d.keep_encryption_box
    assert not box.isHidden()
    assert box.isChecked() and not box.isEnabled()
    box.setChecked(False)  # cannot happen in the UI; still forced
    assert d.options().keep_encryption
    d.save_choices()
    assert settings.export_keep_encryption is False  # the forced value is not remembered


def test_dialog_user_password_box_enabled(window, settings, lo_form_encrypted_pdf) -> None:
    assert window.open_file(str(lo_form_encrypted_pdf))
    d = _dialog(window, settings)
    box = d.keep_encryption_box
    assert not box.isHidden() and box.isEnabled() and box.isChecked()
    box.setChecked(False)
    assert not d.options().keep_encryption
    d.save_choices()
    assert settings.export_keep_encryption is False
    assert not _dialog(window, settings).keep_encryption_box.isChecked()


def test_dialog_dynamic_xfa(window, settings, dynamic_xfa_pdf) -> None:
    assert window.open_file(str(dynamic_xfa_pdf))
    d = _dialog(window, settings)
    for box in (d.flatten_forms_box, d.flatten_annots_box):
        assert not box.isEnabled() and not box.isChecked()
    assert not d.xfa_note.isHidden()
    assert d.xfa_note.text() == "This form uses dynamic XFA: its fields cannot be flattened here."
    options = d.options()
    assert not options.flatten_forms and not options.flatten_annots
    d.save_choices()
    assert settings.export_flatten_forms and settings.export_flatten_annots  # untouched


def test_export_writes_copy_keeps_state(
    qtbot, window, lo_form_pdf, accept, export_to, tmp_path
) -> None:
    w = window
    assert w.open_file(str(lo_form_pdf))
    original = lo_form_pdf.read_bytes()
    w.form_tool.focus(_info(w, FIELD))
    w.document_view.field_editor.editor.setText("exported value")
    assert w.document_view.field_editor.commit()
    assert w.isWindowModified()
    w.settings.last_open_dir = str(tmp_path)

    assert w.export_copy()

    assert export_to["calls"] == [os.path.join(str(tmp_path), "lo_form - flattened.pdf")]
    out = tmp_path / "out.pdf"
    assert out.is_file()
    with pymupdf.open(out) as copy:
        assert not copy.is_form_pdf
        assert "exported value" in "".join(page.get_text() for page in copy)
    assert w.statusBar().currentMessage() == "Exported to “out.pdf”"
    assert QApplication.overrideCursor() is None
    assert w.isWindowModified() and "[*]" in w.windowTitle()
    assert w.document_view.document.path == str(lo_form_pdf)
    assert lo_form_pdf.read_bytes() == original
    assert w.warnings == []
    w.act_undo.trigger()
    assert not w.isWindowModified()
    assert _info(w, FIELD).value != "exported value"


def test_pending_field_edit_committed_before_export(window, lo_form_pdf, accept, export_to):
    w = window
    assert w.open_file(str(lo_form_pdf))
    w.form_tool.focus(_info(w, FIELD))
    w.document_view.field_editor.editor.setText("pending value")
    seen: list[bool] = []
    accept["tweak"] = lambda d: seen.append(w.document_view.is_dirty)
    assert w.export_copy()
    assert seen == [True]
    assert w.undo_stack.count() == 1
    with pymupdf.open(export_to["path"]) as copy:
        assert "pending value" in "".join(page.get_text() for page in copy)


def test_current_path_refused(window, simple_pdf, accept, export_to, monkeypatch) -> None:
    w = window
    assert w.open_file(str(simple_pdf))
    original = simple_pdf.read_bytes()
    called: list[str] = []
    monkeypatch.setattr(PdfDocument, "export_copy", lambda self, path, o=None: called.append(path))
    export_to["path"] = str(simple_pdf).upper()
    assert not w.export_copy()
    assert called == []
    assert w.warnings == [
        ("Export Copy", "Choose another name: the copy cannot replace the open document.", None)
    ]
    assert simple_pdf.read_bytes() == original


def test_save_error_warns_with_details(window, simple_pdf, accept, export_to, monkeypatch):
    w = window
    assert w.open_file(str(simple_pdf))

    def fail(self, path, options=None):
        raise SaveError("Permission denied: out.pdf")

    monkeypatch.setattr(PdfDocument, "export_copy", fail)
    assert not w.export_copy()
    assert QApplication.overrideCursor() is None
    assert w.warnings == [
        (
            "Export failed",
            "The copy could not be written as “out.pdf”. Check that the folder exists and "
            "that you are allowed to write there.",
            "Permission denied: out.pdf",
        )
    ]
    assert w.statusBar().currentMessage() == ""


def test_cancel_exports_nothing(window, simple_pdf, export_to, monkeypatch) -> None:
    w = window
    assert w.open_file(str(simple_pdf))
    monkeypatch.setattr(ExportDialog, "exec", lambda d: QDialog.DialogCode.Rejected)
    assert not w.export_copy()
    assert export_to["calls"] == []
    export_to["path"] = None
    monkeypatch.setattr(ExportDialog, "exec", lambda d: QDialog.DialogCode.Accepted)
    assert not w.export_copy()  # file dialog cancelled
    assert w.warnings == []


def test_choices_persisted_and_clean_copy_name(
    window, settings, simple_pdf, accept, export_to, tmp_path
) -> None:
    w = window
    assert w.open_file(str(simple_pdf))
    w.settings.last_open_dir = str(tmp_path)

    def untick(d: ExportDialog) -> None:
        d.flatten_forms_box.setChecked(False)
        d.flatten_annots_box.setChecked(False)
        d.keep_metadata_box.setChecked(False)

    accept["tweak"] = untick
    assert w.export_copy()
    assert export_to["calls"][-1] == os.path.join(str(tmp_path), "simple - copy.pdf")
    assert not settings.export_flatten_forms
    assert not settings.export_flatten_annots
    assert not settings.export_keep_metadata
    assert settings.export_keep_encryption
    d = _dialog(w, settings)
    assert not d.flatten_forms_box.isChecked()
    assert not d.keep_metadata_box.isChecked()
    assert d.options() == ExportOptions(
        flatten_forms=False, flatten_annots=False, keep_metadata=False
    )


def test_french_strings(qtbot, qapp, window, settings, simple_pdf) -> None:
    assert window.open_file(str(simple_pdf))
    i18n.install_translators(qapp, "fr")
    try:
        d = _dialog(window, settings)
        assert d.windowTitle() == "Exporter une copie"
        assert d.flatten_forms_box.text() == "Aplatir les champs de formulaire"
        assert d.keep_metadata_box.text() == (
            "Conserver les propriétés du document (titre, auteur…)"
        )
        assert QApplication.translate("MainWindow", "&Export Copy…") == "&Exporter une copie…"
        assert QApplication.translate("Dialogs", "Export a Copy") == "Exporter une copie"
    finally:
        i18n.remove_translators(qapp)
