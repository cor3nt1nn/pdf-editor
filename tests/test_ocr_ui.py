"""M8-T5: Edit ▸ Recognise Text (OCR)…, its dialogs, the "looks scanned" banner."""

from __future__ import annotations

import shutil

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDialog, QMessageBox

import pdfeditor.i18n as i18n
from pdfeditor.core import ocr_service
from pdfeditor.core.document import PdfDocument
from pdfeditor.ui import dialogs
from pdfeditor.ui.banner import InfoBanner
from pdfeditor.ui.main_window import MainWindow, strip_mnemonic
from pdfeditor.ui.ocr_dialog import OcrDialog, OcrProgress

TIMEOUT_MS = 30_000
PASSWORD = "secret"


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
    """OcrDialog.exec() accepts at once, after ``tweak(dialog)`` if one is set."""
    state: dict[str, object] = {"tweak": None, "dialogs": []}

    def fake_exec(dialog):
        state["dialogs"].append(dialog)
        if state["tweak"] is not None:
            state["tweak"](dialog)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(OcrDialog, "exec", fake_exec)
    return state


def _copy(src, tmp_path, name="doc.pdf"):
    path = tmp_path / name
    shutil.copyfile(src, path)
    return path


def test_action_and_menu(window, simple_pdf) -> None:
    act = window.act_ocr
    assert act.objectName() == "recognise_text"
    assert act.shortcut() == QKeySequence("Ctrl+Shift+O")
    assert strip_mnemonic(act.text()) == "Recognise Text (OCR)…"
    assert not act.isEnabled()
    actions = window.menu_edit.actions()
    assert actions.index(act) > actions.index(window.act_delete_annot)
    window.open_file(str(simple_pdf))
    assert act.isEnabled()
    window.close_document()
    assert not act.isEnabled()
    rows = dict(dict(window.shortcut_sections())["Edit"])
    assert rows["Recognise Text (OCR)…"] == QKeySequence("Ctrl+Shift+O").toString(
        QKeySequence.SequenceFormat.NativeText
    )


def test_dialog_defaults_and_scopes(qtbot, settings, mixed_scan) -> None:
    doc = PdfDocument.open(mixed_scan)
    dialog = OcrDialog(doc, settings, current_page=0)
    qtbot.addWidget(dialog)
    assert dialog.without_text_radio.isChecked()
    assert dialog.scope() == "without_text"
    assert dialog.searchable_box.isChecked() and dialog.searchable_box.isEnabled()
    assert dialog.make_searchable()
    assert dialog.pages() == [1]
    dialog.page_radio.setChecked(True)
    assert dialog.pages() == [0]
    dialog.all_radio.setChecked(True)
    assert dialog.pages() == [0, 1]
    dialog.searchable_box.setChecked(False)
    dialog.save_choices()
    assert settings.ocr_scope == "all" and settings.ocr_make_searchable is False
    again = OcrDialog(doc, settings, current_page=1)
    qtbot.addWidget(again)
    assert again.all_radio.isChecked() and not again.searchable_box.isChecked()
    assert dialog.languages_label.text() == "Languages: French and English"
    doc.close()


def test_dialog_without_modify_permission(qtbot, settings, scan_clean, tmp_path) -> None:
    import pymupdf

    path = tmp_path / "locked.pdf"
    with pymupdf.open(str(scan_clean.path)) as src:
        src.save(
            str(path),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=PASSWORD,
            owner_pw=PASSWORD + "-owner",
            permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY,
        )
    doc = PdfDocument.open(path, password=PASSWORD)
    settings.ocr_make_searchable = True
    dialog = OcrDialog(doc, settings, current_page=0)
    qtbot.addWidget(dialog)
    box = dialog.searchable_box
    assert not box.isEnabled() and not box.isChecked()
    assert "security settings" in box.toolTip()
    assert not dialog.make_searchable()
    dialog.save_choices()
    assert settings.ocr_make_searchable is True  # not a choice the user made
    doc.close()


def test_settings_defaults(settings) -> None:
    assert settings.ocr_make_searchable is True
    assert settings.ocr_scope == "without_text"
    settings.ocr_scope = "page"
    assert settings.ocr_scope == "page"
    with pytest.raises(ValueError):
        settings.ocr_scope = "everything"
    settings.qsettings.setValue("ocr/scope", "bogus")
    assert settings.ocr_scope == "without_text"


def test_progress_dialog(qtbot) -> None:
    progress = OcrProgress(3)
    qtbot.addWidget(progress)
    assert progress.labelText() == "Recognising text… page 1 of 3"
    progress.set_done(2)
    assert progress.labelText() == "Recognising text… page 3 of 3"
    assert progress.value() == 2
    progress.set_done(3)
    assert progress.labelText() == "Recognising text… page 3 of 3"


def test_recognise_text_end_to_end(qtbot, window, accept, mixed_scan, tmp_path) -> None:
    window.open_file(str(_copy(mixed_scan, tmp_path)))
    doc = window.document_view.document
    banner = window.document_view.banner
    assert banner.isVisible() and banner.action_text == "Recognise text…"
    service = window.document_view.ocr_service
    with qtbot.waitSignal(service.finished, timeout=TIMEOUT_MS):
        assert window.recognise_text()
        assert not window.act_ocr.isEnabled()
        assert window._ocr_progress is not None and window._ocr_progress.isVisible()
        assert not banner.isVisible()  # no scan notice while it runs
    assert window._ocr_progress is None
    assert window.act_ocr.isEnabled()
    assert window.statusBar().currentMessage() == "Text recognised on 1 page(s)"
    assert doc.has_ocr_layer(1)
    assert window.undo_stack.count() == 1
    assert window.undo_stack.undoText() == "Recognise text"
    assert window.document_view.is_dirty
    assert not banner.isVisible()  # the page has text now
    window.undo()
    assert not doc.has_ocr_layer(1)
    window.redo()
    assert doc.has_ocr_layer(1)


def test_memory_only_run_pushes_nothing(qtbot, window, accept, mixed_scan, tmp_path) -> None:
    accept["tweak"] = lambda d: d.searchable_box.setChecked(False)
    window.open_file(str(_copy(mixed_scan, tmp_path)))
    doc = window.document_view.document
    service = window.document_view.ocr_service
    with qtbot.waitSignal(service.finished, timeout=TIMEOUT_MS):
        window.recognise_text()
    assert window.undo_stack.count() == 0 and not window.document_view.is_dirty
    assert doc.page_text(1).source == "ocr"
    assert not window.document_view.banner.isVisible()  # recognised (in memory)


def test_cancel_from_the_progress_dialog(qtbot, window, accept, scan_clean, tmp_path) -> None:
    import pymupdf

    path = tmp_path / "three.pdf"
    with pymupdf.open(str(scan_clean.path)) as src:
        out = pymupdf.open()
        for _ in range(3):
            out.insert_pdf(src)
        out.save(str(path))
    window.open_file(str(path))
    service = window.document_view.ocr_service
    done: list[int] = []
    service.page_done.connect(lambda i, _r: done.append(i))
    window.recognise_text()
    qtbot.waitUntil(lambda: done == [0], timeout=TIMEOUT_MS)
    with qtbot.waitSignal(service.finished, timeout=TIMEOUT_MS) as blocker:
        window._ocr_progress.canceled.emit()  # the Cancel button
    assert blocker.args == [True]
    assert window.statusBar().currentMessage() == "Text recognition was cancelled"
    assert window.undo_stack.count() == 1  # the page done is one undo step
    assert window.document_view.document.has_ocr_layer(0)
    assert not window.document_view.document.has_ocr_layer(2)


def test_every_page_has_text(window, accept, simple_pdf) -> None:
    window.open_file(str(simple_pdf))
    assert not window.recognise_text()
    assert window.statusBar().currentMessage() == "Every page already has text."
    assert not window.document_view.ocr_service.is_running


def test_dialog_rejected(window, monkeypatch, simple_pdf) -> None:
    monkeypatch.setattr(OcrDialog, "exec", lambda d: QDialog.DialogCode.Rejected)
    window.open_file(str(simple_pdf))
    assert not window.recognise_text()


def test_worker_cannot_start(qtbot, window, accept, mixed_scan, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ocr_service, "worker_command", lambda: [str(tmp_path / "none.exe")])
    window.open_file(str(_copy(mixed_scan, tmp_path)))
    service = window.document_view.ocr_service
    with qtbot.waitSignal(service.failed, timeout=TIMEOUT_MS):
        window.recognise_text()
    assert window.statusBar().currentMessage() == "Text recognition failed"
    title, text, details = window.warnings[-1]
    assert title == "Recognise Text"
    assert text == "The text recognition process could not be started."
    assert "could not be started" in details
    assert window.act_ocr.isEnabled() and window._ocr_progress is None


def test_closing_the_document_cancels(qtbot, window, accept, mixed_scan, tmp_path) -> None:
    window.open_file(str(_copy(mixed_scan, tmp_path)))
    service = window.document_view.ocr_service
    window.recognise_text()
    assert service.is_running
    window.close_document()
    assert not service.is_running
    assert window._ocr_progress is None
    assert window.undo_stack.count() == 0


def test_banner_on_scans_only(window, mixed_scan, scan_clean, simple_pdf, tmp_path) -> None:
    banner = window.document_view.banner
    window.open_file(str(simple_pdf))
    assert not banner.isVisible()
    window.open_file(str(_copy(scan_clean.path, tmp_path, "scan.pdf")))
    assert banner.isVisible()
    assert banner.text == "This document looks scanned: 1 of 1 pages have no selectable text."
    assert banner.action_button.isVisible()
    # Dismissed: stays closed for this document (also after a save).
    banner.close_button.click()
    assert not banner.isVisible()
    window.document_view.refresh_banner()
    assert not banner.isVisible()
    window.open_file(str(_copy(mixed_scan, tmp_path, "mixed.pdf")))
    assert banner.isVisible()
    assert banner.text == "This document looks scanned: 1 of 2 pages have no selectable text."


def test_banner_button_opens_the_dialog(window, monkeypatch, scan_clean, tmp_path) -> None:
    opened = []
    monkeypatch.setattr(
        OcrDialog, "exec", lambda d: opened.append(d.scope()) or QDialog.DialogCode.Rejected
    )
    window.open_file(str(_copy(scan_clean.path, tmp_path, "scan.pdf")))
    window.document_view.banner.action_button.click()
    assert opened == ["without_text"]


def test_banner_action_button(qtbot) -> None:
    banner = InfoBanner()
    qtbot.addWidget(banner)
    banner.show_message("Plain", "info")
    assert not banner.action_button.isVisibleTo(banner) and banner.action_text == ""
    banner.show_message("With action", "info", action_text="Do it")
    assert banner.action_button.isVisibleTo(banner) and banner.action_text == "Do it"
    with qtbot.waitSignal(banner.action_triggered):
        banner.action_button.click()
    assert banner.message == ("With action", "info")
    banner.clear()
    assert banner.action_text == "" and not banner.action_button.isVisibleTo(banner)


def test_french_strings(window, simple_pdf) -> None:
    app = QCoreApplication.instance()
    i18n.install_translators(app, "fr")
    try:
        tr = QCoreApplication.translate
        assert tr("MainWindow", "Recognise &Text (OCR)…") == "Reconnaître le &texte (OCR)…"
        assert tr("OcrDialog", "Pages without text") == "Les pages sans texte"
        assert tr("OcrProgress", "Recognising text… page {n} of {m}") == (
            "Reconnaissance du texte… page {n} sur {m}"
        )
        assert tr("DocumentView", "Recognise text…") == "Reconnaître le texte…"
        assert tr("Commands", "Recognise text") == "la reconnaissance du texte"
    finally:
        i18n.remove_translators(app)
