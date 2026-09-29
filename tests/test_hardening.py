from __future__ import annotations

import re

import pymupdf
import pytest
from PySide6.QtGui import QKeySequence

from pdfeditor import __version__
from pdfeditor.core import commands, document
from pdfeditor.core.document import PdfDocument
from pdfeditor.i18n import install_translators, remove_translators
from pdfeditor.resources import app_icon, icon, resource_path
from pdfeditor.ui import dialogs
from pdfeditor.ui import main_window as mw_module
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.page_view import PageView
from pdfeditor.ui.tools import base as tools_base

ICONS = (
    "open save undo redo rotate_cw rotate_ccw prev next zoom_in zoom_out hand thumbnails app"
).split()


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        dialogs, "warn", lambda parent, title, text, details=None: warnings.append((title, text))
    )
    w = MainWindow(settings)
    w.warnings = warnings
    qtbot.addWidget(w)
    w.resize(900, 700)
    w.show()
    yield w
    w.undo_stack.setClean()
    w.close()


def test_about_contents(qtbot, window: MainWindow, monkeypatch) -> None:
    html = dialogs.about_html()
    assert __version__ in html
    assert "Affero" in html and "AGPL-3.0" in html
    assert pymupdf.VersionBind in html
    assert "PySide6" in html and "Qt" in html
    shown: list[str] = []
    monkeypatch.setattr(
        dialogs.QMessageBox, "about", staticmethod(lambda p, title, text: shown.append(title))
    )
    window.act_about.trigger()
    assert shown == ["About PDF Editor"]
    assert window.act_about in window.menu_help.actions()


def test_about_in_french(qapp) -> None:
    install_translators(qapp, "fr")
    try:
        html = dialogs.about_html()
        assert "logiciel libre" in html
        assert f"Version {__version__}" in html
    finally:
        remove_translators(qapp)


def test_icons_load(qtbot, window: MainWindow) -> None:
    for name in ICONS:
        assert resource_path("icons", f"{name}.svg").is_file(), name
        assert not icon(name).isNull(), name
    assert resource_path("app.ico").is_file()
    assert not app_icon().isNull()
    assert not window.windowIcon().isNull()
    for act in window.toolbar.actions():
        if act.isSeparator() or not act.text():
            continue
        assert not act.icon().isNull(), act.objectName()


def test_shortcuts_unique_and_expected(window: MainWindow) -> None:
    seen: dict[str, str] = {}
    for act in window.findChildren(mw_module.QAction):
        for seq in act.shortcuts():
            key = seq.toString()
            if not key:
                continue
            assert key not in seen, f"{key} used by {seen[key]} and {act.objectName()}"
            seen[key] = act.objectName()
    expected = {
        "Ctrl+O": "open",
        "Ctrl+S": "save",
        "Ctrl+Shift+S": "save_as",
        "Ctrl+W": "close",
        "Ctrl+Q": "quit",
        "Ctrl+Z": "undo",
        "Ctrl+Y": "redo",
        "Ctrl+R": "rotate_cw",
        "Ctrl+Shift+R": "rotate_ccw",
        "Ctrl++": "zoom_in",
        "Ctrl+-": "zoom_out",
        "Ctrl+1": "fit_width",
        "Ctrl+2": "fit_page",
        "Ctrl+0": "actual_size",
        "F4": "toggle_thumbnails",
    }
    for key, name in expected.items():
        assert seen.get(QKeySequence(key).toString()) == name, key


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("missing", "The file does not exist."),
        ("empty", "The file is empty."),
        ("corrupt", "The file is damaged or is not a PDF document."),
    ],
)
def test_bad_files_are_reported(window: MainWindow, tmp_path, simple_pdf, setup, message):
    path = tmp_path / f"{setup}.pdf"
    if setup == "empty":
        path.write_bytes(b"")
    elif setup == "corrupt":
        path.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 20)
    assert not window.open_file(str(path))
    assert len(window.warnings) == 1
    assert message in window.warnings[0][1]
    assert window.document_view.document is None
    assert window.open_file(str(simple_pdf))  # still usable


def _make_repairable(src, dst) -> None:
    data = src.read_bytes()
    data = re.sub(rb"startxref\s+\d+", b"startxref\n999999", data)
    dst.write_bytes(data)


def test_repaired_file_opens_and_saves(window: MainWindow, simple_pdf, tmp_path) -> None:
    broken = tmp_path / "broken.pdf"
    _make_repairable(simple_pdf, broken)
    assert window.open_file(str(broken))
    doc = window.document_view.document
    assert doc.was_repaired
    assert not doc.can_save_incrementally()
    assert "repaired" in window.statusBar().currentMessage()
    window.act_rotate_cw.trigger()
    assert window.save()
    reopened = PdfDocument.open(broken)
    assert not reopened.was_repaired
    assert reopened.page_rotation(0) == 90
    reopened.close()


def test_saving_deleted_file_offers_save_as(window: MainWindow, simple_pdf, tmp_path, monkeypatch):
    window.open_file(str(simple_pdf))
    window.act_rotate_cw.trigger()
    monkeypatch.setattr(mw_module, "file_exists", lambda path: False)
    target = tmp_path / "rescued.pdf"
    monkeypatch.setattr(dialogs, "get_save_path", lambda parent, suggested: str(target))
    assert window.save()
    assert "no longer exists" in window.warnings[0][1]
    assert target.is_file()
    assert window.document_view.file_name == "rescued.pdf"
    assert not window.isWindowModified()


def test_m2_entry_points_exist(simple_pdf) -> None:
    doc = PdfDocument.open(simple_pdf)
    assert hasattr(doc, "lock") and hasattr(doc.lock, "acquire")
    assert hasattr(doc, "page_changed") and hasattr(doc, "structure_changed")
    doc.close()
    assert callable(PageView.page_rect_to_viewport)
    assert callable(PageView.viewport_to_page)
    assert issubclass(commands.DocumentCommand, commands.QUndoCommand)
    assert hasattr(tools_base, "ToolManager") and hasattr(tools_base, "Tool")
    assert hasattr(document, "SaveError")
