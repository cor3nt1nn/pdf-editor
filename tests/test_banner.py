"""M2-T7: document-level banner (dynamic/static XFA, form filling permission)."""

from __future__ import annotations

import pytest
from fixtures import XFA_FIELD_NAME, make_dynamic_xfa_pdf
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QMessageBox

import pdfeditor.i18n as i18n
from pdfeditor.core.forms import XfaKind
from pdfeditor.ui import dialogs
from pdfeditor.ui.banner import InfoBanner
from pdfeditor.ui.main_window import MainWindow

STATIC_TEXT = "This form contains XFA data; saving will convert it to a standard PDF form."
PERMISSION_TEXT = "Form filling is not permitted by this document’s security settings."


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
    yield w
    w.undo_stack.setClean()
    w.close()


def _banner(window: MainWindow) -> InfoBanner:
    return window.document_view.banner


def test_no_banner_without_document(window) -> None:
    assert not _banner(window).isVisible()
    assert _banner(window).message is None


def test_dynamic_xfa_warning(window, dynamic_xfa_pdf) -> None:
    assert window.open_file(str(dynamic_xfa_pdf))
    banner = _banner(window)
    assert banner.isVisible()
    assert banner.kind == "warning"
    assert "Adobe" in banner.text
    assert "Microsoft Print to PDF" in banner.text
    assert not window.act_form_tool.isEnabled()
    assert window.tool_manager.active_tool is not window.form_tool


def test_dynamic_single_stream_warning(window, tmp_path) -> None:
    path = make_dynamic_xfa_pdf(tmp_path / "single.pdf", single_stream=True)
    assert window.open_file(str(path))
    assert _banner(window).kind == "warning"


def test_static_xfa_info_disappears_after_strip(window, static_xfa_pdf) -> None:
    assert window.open_file(str(static_xfa_pdf))
    banner = _banner(window)
    assert banner.isVisible()
    assert banner.kind == "info"
    assert banner.text == STATIC_TEXT
    assert window.act_form_tool.isEnabled()
    doc = window.document_view.document
    info = next(w for w in doc.widgets(0) if w.name == XFA_FIELD_NAME)
    doc.set_field_value(0, info.xref, "nouveau")
    window.document_view.save()
    assert doc.xfa_kind is XfaKind.NONE
    assert not banner.isVisible()
    assert banner.message is None


def test_static_xfa_banner_stays_after_plain_save(window, static_xfa_pdf) -> None:
    assert window.open_file(str(static_xfa_pdf))
    window.document_view.document.set_page_rotation(0, 90)
    window.document_view.save()  # reloads, but the XFA is kept (no field edited)
    assert _banner(window).isVisible()
    assert _banner(window).text == STATIC_TEXT


def test_owner_locked_permission_banner(window, owner_locked_pdf) -> None:
    assert window.open_file(str(owner_locked_pdf))
    banner = _banner(window)
    assert banner.isVisible()
    assert banner.kind == "info"
    assert banner.text == PERMISSION_TEXT


def test_fillable_form_has_no_banner(window, lo_form_pdf, simple_pdf) -> None:
    assert window.open_file(str(lo_form_pdf))
    assert not _banner(window).isVisible()
    assert window.open_file(str(simple_pdf))
    assert not _banner(window).isVisible()


def test_banner_cleared_on_other_document_and_close(window, dynamic_xfa_pdf, lo_form_pdf):
    assert window.open_file(str(dynamic_xfa_pdf))
    assert window.open_file(str(lo_form_pdf))
    assert not _banner(window).isVisible()
    assert window.open_file(str(dynamic_xfa_pdf))
    assert window.close_document()
    assert not _banner(window).isVisible()


def test_close_button_hides_until_next_document(qtbot, window, static_xfa_pdf) -> None:
    assert window.open_file(str(static_xfa_pdf))
    banner = _banner(window)
    qtbot.mouseClick(banner.close_button, Qt.MouseButton.LeftButton)
    assert not banner.isVisible()
    # A reload (save without field edits) does not bring a closed banner back...
    window.document_view.document.set_page_rotation(0, 90)
    window.document_view.save()
    assert not banner.isVisible()
    # ...but opening a document again does.
    assert window.open_file(str(static_xfa_pdf))
    assert banner.isVisible()


def test_banner_translated(qapp, window, dynamic_xfa_pdf) -> None:
    i18n.install_translators(qapp, "fr")
    try:
        assert window.open_file(str(dynamic_xfa_pdf))
        text = _banner(window).text
        assert text.startswith("Ce formulaire utilise XFA dynamique")
        assert "Adobe" in text
        assert InfoBanner().close_button.toolTip() == "Fermer"
    finally:
        i18n.remove_translators(qapp)


def test_banner_colours_follow_palette(qtbot) -> None:
    banner = InfoBanner()
    qtbot.addWidget(banner)
    dark = QPalette()
    dark.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
    dark.setColor(QPalette.ColorRole.WindowText, QColor(240, 240, 240))
    banner.setPalette(dark)
    banner.show_message("hello", "warning")
    assert "#f0f0f0" in banner.styleSheet()  # palette text colour
    background = banner.styleSheet().split("background-color: ")[1][:7]
    assert QColor(background).lightness() < 100  # stays dark on a dark palette
    with pytest.raises(ValueError):
        banner.show_message("x", "error")
