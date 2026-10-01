"""Runtime facts shared by ``app`` and the UI (the About box) without the UI importing
``pdfeditor.app``: whether this is the frozen build, and where the file log goes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths

#: Environment variable naming the directory of the file log instead of
#: ``AppLocalDataLocation/logs``; also turns the file log on outside a frozen build.
LOG_DIR_ENV = "PDFEDITOR_LOG_DIR"
LOG_NAME = "pdfeditor.log"


def is_frozen() -> bool:
    """Running from the PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def log_path() -> Path:
    """Where the file log is written: ``$PDFEDITOR_LOG_DIR/pdfeditor.log`` or
    ``AppLocalDataLocation/logs/pdfeditor.log``. With organisation and application both
    named "PDFEditor" (``app.main``), that is
    ``%LOCALAPPDATA%\\PDFEditor\\PDFEditor\\logs\\pdfeditor.log``. Needs the
    application's organisation and name to be set."""
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        return Path(override) / LOG_NAME
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(base) / "logs" / LOG_NAME
