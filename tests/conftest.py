from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402

from pdfeditor.core.settings import Settings  # noqa: E402


@pytest.fixture
def ini_path(tmp_path):
    return tmp_path / "PDFEditor.ini"


@pytest.fixture
def settings(ini_path) -> Settings:
    return Settings(QSettings(str(ini_path), QSettings.Format.IniFormat))
