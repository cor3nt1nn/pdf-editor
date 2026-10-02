"""M5-T6: licence files, Help ▸ Keyboard Shortcuts…, Help ▸ Third-Party Licenses…, About."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QComboBox, QLabel, QTableWidget, QTextBrowser

from pdfeditor import app
from pdfeditor.i18n import install_translators, remove_translators
from pdfeditor.resources import license_files, resource_path, third_party_notice
from pdfeditor.ui import dialogs
from pdfeditor.ui import main_window as mw_module
from pdfeditor.ui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1]
LICENSES = {
    "AGPL-3.0.txt": "GNU AFFERO GENERAL PUBLIC LICENSE",
    "GPL-3.0.txt": "GNU GENERAL PUBLIC LICENSE",
    "LGPL-3.0.txt": "GNU LESSER GENERAL PUBLIC LICENSE",
    "PSF-2.0.txt": "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2",
    "PyInstaller-GPL-2.0-bootloader-exception.txt": "Bootloader Exception",
}


@pytest.fixture
def window(qtbot, settings):
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.resize(900, 700)
    yield w
    w.close()


# -- licence files -----------------------------------------------------------------


def test_license_files_exist_and_are_listed() -> None:
    files = {p.name: p for p in license_files()}
    assert set(files) == set(LICENSES)
    for name, marker in LICENSES.items():
        text = files[name].read_text(encoding="utf-8")
        assert marker in text, name
        assert len(text) > 5000, name  # full texts, not a summary
    assert files["AGPL-3.0.txt"].read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert "Version 3, 29 June 2007" in files["LGPL-3.0.txt"].read_text(encoding="utf-8")
    assert "Version 2, June 1991" in files[
        "PyInstaller-GPL-2.0-bootloader-exception.txt"
    ].read_text(encoding="utf-8")
    assert resource_path("licenses").is_dir()


def test_third_party_notice_lists_components_and_files() -> None:
    notice = third_party_notice()
    assert notice == ROOT / "THIRD_PARTY_LICENSES.md"
    text = notice.read_text(encoding="utf-8")
    for name in LICENSES:
        assert f"`{name}`" in text, name
    for component in ("PySide6", "shiboken6", "Qt", "PyMuPDF", "MuPDF", "Python", "PyInstaller"):
        assert component in text, component
    assert "_internal\\PySide6" in text  # how to replace the Qt libraries
    assert "bootloader exception" in text


def test_third_party_notice_next_to_frozen_exe(tmp_path, monkeypatch) -> None:
    (tmp_path / "THIRD_PARTY_LICENSES.md").write_text("# frozen", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "PDFEditor.exe"))
    assert third_party_notice() == tmp_path / "THIRD_PARTY_LICENSES.md"


def test_spec_and_build_script_ship_the_licenses() -> None:
    spec = (ROOT / "pdfeditor.spec").read_text(encoding="utf-8")
    assert '"resources" / "licenses"' in spec
    script = (ROOT / "scripts" / "build_exe.ps1").read_text(encoding="utf-8")
    assert "THIRD_PARTY_LICENSES.md" in script
    assert "licenses" in script


# -- licences dialog -----------------------------------------------------------------


def test_licenses_dialog_shows_notice_and_texts(qtbot) -> None:
    dialog = dialogs.make_licenses_dialog(None)
    qtbot.addWidget(dialog)
    chooser = dialog.findChild(QComboBox, "license_chooser")
    browser = dialog.findChild(QTextBrowser, "license_browser")
    names = [chooser.itemText(i) for i in range(chooser.count())]
    assert names == ["THIRD_PARTY_LICENSES.md", *sorted(LICENSES)]
    assert "PyMuPDF" in browser.toPlainText()
    chooser.setCurrentIndex(names.index("LGPL-3.0.txt"))
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in browser.toPlainText()


def test_third_party_action_in_help_menu(window: MainWindow, monkeypatch) -> None:
    shown: list[object] = []
    monkeypatch.setattr(dialogs, "show_third_party_licenses", lambda parent: shown.append(parent))
    assert window.act_third_party in window.menu_help.actions()
    window.act_third_party.trigger()
    assert shown == [window]


# -- About --------------------------------------------------------------------------


def test_about_has_licenses_link_and_opens_dialog(qtbot, monkeypatch) -> None:
    html = dialogs.about_html()
    assert f'href="{dialogs.LICENSES_LINK}"' in html
    assert "Third-party licenses" in html
    assert "Portable build" not in html and "Log file" not in html
    shown: list[object] = []
    monkeypatch.setattr(dialogs, "show_third_party_licenses", lambda parent: shown.append(parent))
    opened: list[str] = []
    monkeypatch.setattr(
        dialogs.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString()))
    )
    box = dialogs.make_about_box(None)
    qtbot.addWidget(box)
    label = box.findChild(QLabel, "qt_msgbox_label")
    assert label is not None and not label.openExternalLinks()
    label.linkActivated.emit(dialogs.LICENSES_LINK)
    assert shown == [box]
    label.linkActivated.emit("https://www.gnu.org/licenses/agpl-3.0.html")
    assert opened == ["https://www.gnu.org/licenses/agpl-3.0.html"]


def test_about_frozen_shows_log_path_and_portable(qapp, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv(app.LOG_DIR_ENV, str(tmp_path / "logs"))
    html = dialogs.about_html()
    assert "Portable build" in html
    assert f"Log file: {tmp_path / 'logs' / 'pdfeditor.log'}" in html
    install_translators(qapp, "fr")
    try:
        html = dialogs.about_html()
        assert "Version portable" in html
        assert "Fichier journal : " in html
        assert "Licences tierces" in html
    finally:
        remove_translators(qapp)


def test_about_action_execs_box(window: MainWindow, monkeypatch) -> None:
    titles: list[str] = []
    monkeypatch.setattr(
        dialogs.QMessageBox, "exec", lambda self: titles.append(self.windowTitle()) or 0
    )
    window.act_about.trigger()
    assert titles == ["About PDF Editor"]


# -- keyboard shortcuts ---------------------------------------------------------------


def _all_shortcut_actions(window: MainWindow) -> list[QAction]:
    return [
        act
        for act in window.findChildren(QAction)
        if any(not seq.isEmpty() for seq in act.shortcuts())
    ]


def _check_sections(window: MainWindow, sections) -> None:
    rows = {(a, k) for _title, rows in sections for a, k in rows}
    for act in _all_shortcut_actions(window):
        text = mw_module.strip_mnemonic(act.text())
        keys = mw_module.shortcut_text(act)
        assert (text, keys) in rows, (act.objectName(), text, keys)
        assert "&" not in text or "&&" in act.text()
    for _title, section_rows in sections:
        for action, keys in section_rows:
            assert action and keys
    interaction = dict(sections[-1][1])
    assert len(interaction) == 11
    assert len(sections) >= 5


def test_f1_opens_shortcuts_dialog(window: MainWindow, monkeypatch) -> None:
    assert window.act_shortcuts.shortcut() == QKeySequence("F1")
    owners = [a for a in _all_shortcut_actions(window) if QKeySequence("F1") in a.shortcuts()]
    assert owners == [window.act_shortcuts]  # F1 used once
    assert window.menu_help.actions()[0] is window.act_shortcuts
    shown: list = []
    monkeypatch.setattr(dialogs, "show_shortcuts", lambda parent, s: shown.append((parent, s)))
    window.act_shortcuts.trigger()
    assert len(shown) == 1 and shown[0][0] is window
    _check_sections(window, shown[0][1])


def test_shortcut_sections_grouped_by_menu(window: MainWindow) -> None:
    sections = window.shortcut_sections()
    titles = [t for t, _rows in sections]
    assert titles == ["File", "Edit", "Pages", "View", "Help", "Pointer and editing"]
    file_rows = dict(sections[0][1])
    native = QKeySequence.SequenceFormat.NativeText
    assert file_rows["Open…"] == QKeySequence("Ctrl+O").toString(native)
    assert file_rows["Export Copy…"] == QKeySequence("Ctrl+E").toString(native)
    edit_rows = dict(sections[1][1])
    assert edit_rows["Redo"].count(",") == 1  # Ctrl+Y, Ctrl+Shift+Z
    assert "Delete Annotation" in edit_rows
    interaction = dict(sections[-1][1])
    assert interaction["Zoom under the pointer"] == "Ctrl+Wheel"
    assert interaction["Place without snapping / over a form field"] == "Alt+Click"
    assert interaction["Next / previous field"] == "Tab / Shift+Tab"
    assert interaction["Next / previous page"] == " / ".join(
        QKeySequence(k).toString(native) for k in ("PgDown", "PgUp")
    )
    assert interaction["First / last page"] == " / ".join(
        QKeySequence(k).toString(native) for k in ("Home", "End")
    )
    _check_sections(window, sections)


def test_shortcuts_dialog_table(qtbot, window: MainWindow) -> None:
    sections = window.shortcut_sections()
    dialog = dialogs.make_shortcuts_dialog(window, sections)
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "Keyboard Shortcuts"
    table = dialog.findChild(QTableWidget, "shortcuts_table")
    assert table.horizontalHeaderItem(0).text() == "Action"
    assert table.horizontalHeaderItem(1).text() == "Shortcut"
    expected_rows = sum(1 + len(rows) for _t, rows in sections)
    assert table.rowCount() == expected_rows
    cells = {
        (table.item(r, 0).text(), table.item(r, 1).text())
        for r in range(table.rowCount())
        if table.item(r, 1) is not None
    }
    for _title, rows in sections:
        assert set(rows) <= cells


def test_shortcuts_in_french(qtbot, qapp, settings) -> None:
    install_translators(qapp, "fr")
    try:
        w = MainWindow(settings)
        qtbot.addWidget(w)
        assert w.act_shortcuts.text() == "&Raccourcis clavier…"
        assert w.act_third_party.text() == "Licences &tierces…"
        sections = w.shortcut_sections()
        titles = [t for t, _rows in sections]
        assert titles[0] == "Fichier" and titles[-1] == "Souris et saisie"
        interaction = dict(sections[-1][1])
        assert interaction["Zoomer sous le pointeur"] == "Ctrl+Molette"
        assert interaction["Placer sans magnétisme / sur un champ de formulaire"] == "Alt+Clic"
        assert "Cocher/décocher la case sélectionnée" in interaction
        assert "Annuler, désélectionner" in interaction
        assert "Page suivante / précédente" in interaction
        assert "Première / dernière page" in interaction
        _check_sections(w, sections)
        dialog = dialogs.make_shortcuts_dialog(w, sections)
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Raccourcis clavier"
        table = dialog.findChild(QTableWidget, "shortcuts_table")
        assert table.horizontalHeaderItem(1).text() == "Raccourci"
        assert not any(re.search(r"(?<!&)&(?!&)", t) for t in titles)
        w.close()
    finally:
        remove_translators(qapp)


def test_page_navigation_keys_listed_are_handled(qtbot, window: MainWindow, many_pages_pdf):
    """The "Next / previous page" and "First / last page" rows of the shortcuts dialog
    are handled by the page view (review finding 5)."""
    window.show()
    qtbot.waitExposed(window)
    assert window.open_file(str(many_pages_pdf))
    pv = window.page_view
    pv.setFocus()
    last = pv.page_count - 1
    assert last >= 2
    qtbot.keyClick(pv, Qt.Key.Key_PageDown)
    assert pv.current_page == 1
    qtbot.keyClick(pv, Qt.Key.Key_PageUp)
    assert pv.current_page == 0
    qtbot.keyClick(pv, Qt.Key.Key_End)
    assert pv.current_page == last
    qtbot.keyClick(pv, Qt.Key.Key_Home)
    assert pv.current_page == 0
    window.undo_stack.setClean()
