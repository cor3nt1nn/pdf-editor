"""Hidden ``--self-check DIR`` mode: a headless check of an installed or frozen build.

Generates its own documents with the core APIs (an AES-256 form, FreeText + check-mark
stamp, a signature with a soft mask), then opens, fills, places, renders, saves
(incremental and full) and exports them (flattened and clean). Also checks the Qt side
of the bundle: French translators, SVG icons, image format plugins; and (M8) text
recognition with the bundled language data, in this process and in the worker process,
and fontTools subsetting of an installed font. Everything is
written into ``DIR`` (PDFs, ``report.json``, ``self-check.log``); the user's settings,
signature store and log are never touched. No window is shown.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Image formats the app relies on (signature import, icons).
REQUIRED_IMAGE_FORMATS = ("svg", "jpeg", "png", "tiff", "webp", "bmp", "gif")
#: Icons checked for a non-null pixmap (the SVG icon engine must be bundled).
CHECKED_ICONS = ("open", "save", "signature", "stamp_check", "text")
REPORT_NAME = "report.json"
#: (context, source, expected French) looked up with the French translators installed:
#: one from Qt's qtbase_fr.qm, one from pdfeditor_fr.qm. Passed to translate() as
#: variables so that lupdate does not extract them.
TRANSLATION_SAMPLES = {
    "qtbase": ("QPlatformTheme", "Cancel", "Annuler"),
    "app": ("MainWindow", "&File", "&Fichier"),
}


class _Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.data: dict[str, Any] = {}

    def run(self, name: str, func: Callable[[], Any]) -> Any:
        """Run one check: ``func`` returns details (or raises / asserts on failure)."""
        try:
            detail = func()
        except Exception as exc:  # noqa: BLE001 - every failure goes into the report
            log.error("self-check %s failed: %s", name, exc)
            self.checks.append({"name": name, "ok": False, "error": traceback.format_exc()})
            return None
        log.info("self-check %s ok", name)
        self.checks.append({"name": name, "ok": True, "detail": detail})
        return detail

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c["ok"] for c in self.checks)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# -- Qt side -------------------------------------------------------------------
def check_translators(app: Any) -> dict[str, Any]:
    from PySide6.QtCore import QCoreApplication, QLibraryInfo

    from pdfeditor.i18n import install_translators, remove_translators

    try:
        count = len(install_translators(app, "fr"))
        samples = {
            key: QCoreApplication.translate(context, source)
            for key, (context, source, _) in TRANSLATION_SAMPLES.items()
        }
    finally:
        remove_translators(app)
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    _require(count == 2, f"{count} translators installed")
    expected = {key: french for key, (_, _, french) in TRANSLATION_SAMPLES.items()}
    _require(samples == expected, f"samples: {samples}")
    return {"count": count, "samples": samples, "qt_translations": path}


def check_icons() -> dict[str, Any]:
    from pdfeditor.resources import app_icon, icon

    result = {}
    for name in CHECKED_ICONS:
        found = icon(name)
        result[name] = not found.isNull() and not found.pixmap(32, 32).isNull()
    found = app_icon()
    result["app"] = not found.isNull() and not found.pixmap(32, 32).isNull()
    missing = [name for name, ok in result.items() if not ok]
    _require(not missing, f"null icons: {missing}")
    return result


def check_image_formats() -> list[str]:
    from PySide6.QtGui import QImageReader

    formats = sorted(bytes(f.data()).decode() for f in QImageReader.supportedImageFormats())
    missing = [f for f in REQUIRED_IMAGE_FORMATS if f not in formats]
    _require(not missing, f"missing image formats: {missing}")
    return formats


def check_paths() -> dict[str, Any]:
    from PySide6.QtCore import QStandardPaths

    local = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    _require("PDFEditor" in Path(local).parts, f"AppLocalDataLocation is {local}")
    return {"app_local_data": local}


# -- entry point ---------------------------------------------------------------
def run(directory: str | os.PathLike[str]) -> int:
    """Run every check, write ``DIR/report.json``; 0 if all passed, else 1.

    Creates the QApplication (offscreen platform unless ``QT_QPA_PLATFORM`` is set)
    with the application's organisation and name when there is none yet."""
    from PySide6.QtWidgets import QApplication

    from pdfeditor import __version__
    from pdfeditor.constants import APP_ID, ORG_NAME
    from pdfeditor.core import self_check as documents

    out = Path(directory).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([sys.argv[0] if sys.argv else "pdfeditor"])
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_ID)
    app.setApplicationVersion(__version__)

    report = _Report()
    report.data.update(
        {
            "version": __version__,
            "frozen": bool(getattr(sys, "frozen", False)),
            "executable": sys.executable,
            "python": platform.python_version(),
            "platform": app.platformName(),
        }
    )
    report.run("versions", _versions)
    report.run("paths", check_paths)
    report.run("translators", lambda: check_translators(app))
    report.run("icons", check_icons)
    report.run("image_formats", check_image_formats)
    report.run("form", lambda: documents.check_form(out))
    report.run("annotations", lambda: documents.check_annotations(out))
    report.run("font_subset", lambda: documents.check_font_subset(out))
    report.run("ocr", lambda: documents.check_ocr(out))
    report.run("ocr_worker", lambda: documents.check_ocr_worker(out))
    report.data["ok"] = report.ok
    report.data["checks"] = report.checks
    (out / REPORT_NAME).write_text(
        json.dumps(report.data, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    log.info("self-check %s: %s", "passed" if report.ok else "FAILED", out / REPORT_NAME)
    return 0 if report.ok else 1


def _versions() -> dict[str, str]:
    import PySide6

    from pdfeditor.core.document import pdf_library_versions

    return {"pyside6": PySide6.__version__, **dict(pdf_library_versions())}
