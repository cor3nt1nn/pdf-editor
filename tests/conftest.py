from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fixtures  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402

from pdfeditor.core.settings import Settings  # noqa: E402


@pytest.fixture
def ini_path(tmp_path):
    return tmp_path / "PDFEditor.ini"


@pytest.fixture
def settings(ini_path) -> Settings:
    return Settings(QSettings(str(ini_path), QSettings.Format.IniFormat))


@pytest.fixture
def simple_pdf(tmp_path):
    return fixtures.make_simple_pdf(tmp_path / "simple.pdf")


@pytest.fixture
def rotated_pdf(tmp_path):
    return fixtures.make_rotated_pdf(tmp_path / "rotated.pdf")


@pytest.fixture
def encrypted_pdf(tmp_path):
    return fixtures.make_encrypted_pdf(tmp_path / "encrypted.pdf")


@pytest.fixture
def form_pdf(tmp_path):
    return fixtures.make_form_pdf(tmp_path / "form.pdf")


@pytest.fixture
def many_pages_pdf(tmp_path):
    return fixtures.make_many_pages_pdf(tmp_path / "many.pdf")
