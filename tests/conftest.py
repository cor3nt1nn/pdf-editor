from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fixtures  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402

from pdfeditor.core.settings import Settings  # noqa: E402


@pytest.fixture(autouse=True)
def _english_after_test():
    """app.main() installs the system language (French here); don't leak it to other tests."""
    yield
    from PySide6.QtCore import QCoreApplication

    from pdfeditor.i18n import remove_translators

    app = QCoreApplication.instance()
    if app is not None:
        remove_translators(app)


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


@pytest.fixture
def lo_form_pdf(tmp_path):
    return fixtures.make_lo_form_pdf(tmp_path / "lo_form.pdf")


@pytest.fixture
def lo_form_rotated_pdf(tmp_path):
    return fixtures.make_lo_form_pdf(tmp_path / "lo_form_rotated.pdf", rotate=90)


@pytest.fixture
def lo_form_encrypted_pdf(tmp_path):
    return fixtures.make_lo_form_pdf(tmp_path / "lo_form_encrypted.pdf", encrypted=True)


@pytest.fixture
def owner_locked_pdf(tmp_path):
    return fixtures.make_owner_locked_pdf(tmp_path / "owner_locked.pdf")


@pytest.fixture
def static_xfa_pdf(tmp_path):
    return fixtures.make_static_xfa_pdf(tmp_path / "static_xfa.pdf")


@pytest.fixture
def dynamic_xfa_pdf(tmp_path):
    return fixtures.make_dynamic_xfa_pdf(tmp_path / "dynamic_xfa.pdf")


@pytest.fixture
def many_fields_pdf(tmp_path):
    return fixtures.make_many_fields_pdf(tmp_path / "many_fields.pdf")


@pytest.fixture
def odd_widgets_pdf(tmp_path):
    return fixtures.make_odd_widgets_pdf(tmp_path / "odd_widgets.pdf")


@pytest.fixture
def annotated_pdf(tmp_path):
    return fixtures.make_annotated_pdf(tmp_path / "annotated.pdf")
