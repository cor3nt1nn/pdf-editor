"""In-process tests of the hidden CLI flags (--self-check, --quit-after), the file log
and the settings/log directory overrides used by the frozen tests."""

from __future__ import annotations

import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest
from PySide6.QtCore import QStandardPaths

import pdfeditor.app as app_module
import pdfeditor.ui.main_window as mw_module
from pdfeditor import i18n
from pdfeditor.core import self_check as documents
from pdfeditor.core.settings import SETTINGS_DIR_ENV, default_qsettings
from pdfeditor.ui.main_window import MainWindow


@pytest.fixture
def root_logging():
    """Restore the root logger (level, our file handlers) after the test."""
    root = logging.getLogger()
    level = root.level
    yield root
    app_module._drop_file_handlers(root)
    root.setLevel(level)


def _file_handlers(root: logging.Logger) -> list[logging.Handler]:
    return [h for h in root.handlers if getattr(h, "_pdfeditor", False)]


def test_self_check_passes_in_process(qapp, tmp_path, root_logging) -> None:
    out = tmp_path / "check"
    assert app_module.main(["pdfeditor", "--self-check", str(out)]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["frozen"] is False
    names = [c["name"] for c in report["checks"]]
    assert names == [
        "versions",
        "paths",
        "translators",
        "icons",
        "image_formats",
        "form",
        "annotations",
        "font_subset",
        "ocr",
        "ocr_worker",
    ]
    checks = {c["name"]: c["detail"] for c in report["checks"]}
    assert checks["translators"]["count"] == 2
    assert checks["form"]["values"]["name"] == documents.FIELD_TEXT
    assert checks["annotations"]["annotations"] == ["signature", "stamp", "text"]
    assert checks["ocr"]["recall"] >= 0.8 and checks["ocr_worker"]["recall"] >= 0.8
    assert checks["ocr_worker"]["command"][-1] == "--ocr-worker"
    for name in (
        "form-aes256.pdf",
        "form-flattened.pdf",
        "form-clean.pdf",
        "annotations.pdf",
        "annotations-flattened.pdf",
        "annotations-clean.pdf",
    ):
        assert (out / name).is_file(), name
    log_text = (out / "self-check.log").read_text(encoding="utf-8")
    assert "self-check passed" in log_text
    # No window, no leftover translators or file handler.
    assert i18n.current_language() == "en"
    assert _file_handlers(root_logging) == []


def test_self_check_reports_a_failure(qapp, tmp_path, root_logging, monkeypatch) -> None:
    def broken(directory: Path) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(documents, "check_form", broken)
    out = tmp_path / "check"
    assert app_module.self_check(out) == 1
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["ok"] is False
    failed = [c for c in report["checks"] if not c["ok"]]
    assert [c["name"] for c in failed] == ["form"]
    assert "boom" in failed[0]["error"]
    assert "ERROR" in (out / "self-check.log").read_text(encoding="utf-8")


def test_quit_after_schedules_quit(qtbot, settings, simple_pdf, monkeypatch) -> None:
    created: list[MainWindow] = []
    quits: list[float] = []

    class Capturing(MainWindow):
        def __init__(self, s):
            super().__init__(settings)
            created.append(self)

    def fake_exec() -> int:
        # A real exec() would leave the test process' QApplication "closing" (QThreads
        # no longer start): wait for the timer's quit() in a local event loop instead.
        qtbot.waitUntil(lambda: bool(quits), timeout=5000)
        return 0

    monkeypatch.setattr(mw_module, "MainWindow", Capturing)
    monkeypatch.setattr(app_module, "Settings", lambda: settings)
    monkeypatch.setattr(app_module.QApplication, "exec", staticmethod(fake_exec))
    monkeypatch.setattr(
        app_module.QApplication, "quit", staticmethod(lambda: quits.append(time.monotonic()))
    )
    start = time.monotonic()
    assert app_module.main(["pdfeditor", "--quit-after", "300", str(simple_pdf)]) == 0
    assert len(quits) == 1 and quits[0] - start >= 0.25
    w = created[0]
    qtbot.addWidget(w)
    # The file was opened (singleShot(0)) before the quit timer fired.
    assert w.document_view.document.page_count == 3
    w.close()


def test_no_quit_timer_without_flag(qtbot, settings, monkeypatch) -> None:
    created: list[MainWindow] = []
    quits: list[int] = []

    class Capturing(MainWindow):
        def __init__(self, s):
            super().__init__(settings)
            created.append(self)

    def fake_exec() -> int:
        qtbot.wait(300)
        return 0

    monkeypatch.setattr(mw_module, "MainWindow", Capturing)
    monkeypatch.setattr(app_module, "Settings", lambda: settings)
    monkeypatch.setattr(app_module.QApplication, "exec", staticmethod(fake_exec))
    monkeypatch.setattr(app_module.QApplication, "quit", staticmethod(lambda: quits.append(1)))
    assert app_module.main(["pdfeditor"]) == 0
    qtbot.addWidget(created[0])
    assert quits == []
    created[0].close()


def test_file_log_from_env(qapp, tmp_path, root_logging, monkeypatch) -> None:
    monkeypatch.setenv(app_module.LOG_DIR_ENV, str(tmp_path / "logs"))
    path = app_module.configure_logging(False)
    assert path == tmp_path / "logs" / "pdfeditor.log"
    assert app_module.log_path() == path
    logging.getLogger("pdfeditor.test").info("hello log")
    (handler,) = _file_handlers(root_logging)
    assert isinstance(handler, RotatingFileHandler)
    assert (handler.maxBytes, handler.backupCount) == (1024 * 1024, 3)
    handler.flush()
    text = path.read_text(encoding="utf-8")
    assert "(dev), log:" in text and "INFO pdfeditor.test: hello log" in text
    # A second call replaces the handler instead of adding one.
    assert app_module.configure_logging(True) == path
    assert len(_file_handlers(root_logging)) == 1


def test_no_file_log_in_development(qapp, root_logging, monkeypatch) -> None:
    monkeypatch.delenv(app_module.LOG_DIR_ENV, raising=False)
    assert app_module.configure_logging(False) is None
    assert _file_handlers(root_logging) == []


def test_default_log_path_is_app_local_data(qapp, monkeypatch) -> None:
    monkeypatch.delenv(app_module.LOG_DIR_ENV, raising=False)
    qapp.setOrganizationName("PDFEditor")
    qapp.setApplicationName("PDFEditor")
    path = app_module.log_path()
    # %LOCALAPPDATA%\PDFEditor\PDFEditor\logs\pdfeditor.log (organisation\application),
    # as documented in README.md and Deviation 52.
    assert path.parts[-4:] == ("PDFEditor", "PDFEditor", "logs", "pdfeditor.log")
    local = os.environ.get("LOCALAPPDATA")
    if local and not QStandardPaths.isTestModeEnabled():
        expected = Path(local) / "PDFEditor" / "PDFEditor" / "logs" / "pdfeditor.log"
        assert os.path.normcase(path) == os.path.normcase(expected)


def test_unwritable_log_dir_is_not_fatal(qapp, tmp_path, root_logging) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert app_module.configure_logging(True, blocker / "logs" / "pdfeditor.log") is None
    assert _file_handlers(root_logging) == []


def test_settings_dir_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(SETTINGS_DIR_ENV, str(tmp_path))
    qsettings = default_qsettings()
    assert Path(qsettings.fileName()) == tmp_path / "PDFEditor.ini"
    qsettings.setValue("ui/language", "fr")
    qsettings.sync()
    assert "language=fr" in (tmp_path / "PDFEditor.ini").read_text(encoding="utf-8")


def test_ui_does_not_import_app() -> None:
    """Layering (review finding 6): the About box reads the runtime facts from
    ``pdfeditor.paths``; no ``ui``/``render``/``core`` module imports ``pdfeditor.app``."""
    import ast

    import pdfeditor
    from pdfeditor import paths

    root = Path(pdfeditor.__file__).parent
    offenders = []
    for package in ("ui", "render", "core"):
        for source in (root / package).rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    names = [base] + [f"{base}.{a.name}" for a in node.names]
                else:
                    continue
                if "pdfeditor.app" in names:
                    offenders.append(source.name)
    assert offenders == []
    assert app_module.log_path is paths.log_path
    assert app_module.is_frozen is paths.is_frozen
    assert app_module.LOG_DIR_ENV == paths.LOG_DIR_ENV == "PDFEDITOR_LOG_DIR"
