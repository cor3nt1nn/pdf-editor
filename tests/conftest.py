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


@pytest.fixture(autouse=True)
def _private_default_signature_store(tmp_path_factory, monkeypatch):
    """A ``SignatureStore()`` built without a directory (e.g. ``MainWindow(settings)``)
    lives in a fresh temporary directory, never in the user's real store."""
    from pdfeditor.core import signature_store

    directory = tmp_path_factory.mktemp("default_signatures") / "signatures"
    monkeypatch.setattr(signature_store, "default_directory", lambda: directory)


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
def lo_form_full_access_pdf(tmp_path):
    """User password, every permission granted (no author restrictions)."""
    return fixtures.make_lo_form_pdf(
        tmp_path / "lo_form_full_access.pdf", encrypted=True, permissions=-4
    )


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


@pytest.fixture
def many_annots_pdf(tmp_path):
    return fixtures.make_many_annots_pdf(tmp_path / "many_annots.pdf")


@pytest.fixture
def odd_annots_pdf(tmp_path):
    return fixtures.make_odd_annots_pdf(tmp_path / "odd_annots.pdf")


@pytest.fixture
def word_form_pdf(tmp_path):
    return fixtures.make_word_form_pdf(tmp_path / "word_form.pdf")


@pytest.fixture
def word_form_rotated_pdf(tmp_path):
    return fixtures.make_word_form_pdf(tmp_path / "word_form_rotated.pdf", rotate=90)


@pytest.fixture
def print_pdf(tmp_path):
    return fixtures.make_print_pdf(tmp_path / "print.pdf")


@pytest.fixture
def signed_pdf(tmp_path):
    return fixtures.make_signed_pdf(tmp_path / "signed.pdf")


@pytest.fixture
def signature_png(tmp_path):
    return fixtures.make_signature_image(tmp_path / "signature.png", kind="clean")


@pytest.fixture
def signature_photo(tmp_path):
    return fixtures.make_signature_image(tmp_path / "signature.jpg", kind="photo")


@pytest.fixture
def signature_store(tmp_path):
    """An empty SignatureStore in tmp_path (never the user's real directory)."""
    from pdfeditor.core.signature_store import SignatureStore

    return SignatureStore(tmp_path / "signatures")


@pytest.fixture
def store_with_one(signature_store, signature_png):
    """``signature_store`` holding one signature ("My signature", the clean PNG), default."""
    from PySide6.QtGui import QImage

    signature_store.add("My signature", QImage(str(signature_png)))
    return signature_store


@pytest.fixture
def many_signatures_pdf(tmp_path):
    return fixtures.make_many_signatures_pdf(tmp_path / "many_signatures.pdf")


@pytest.fixture
def odd_stamps_pdf(tmp_path):
    return fixtures.make_odd_stamps_pdf(tmp_path / "odd_stamps.pdf")


@pytest.fixture
def outlined_pdf(tmp_path):
    return fixtures.make_outlined_pdf(tmp_path / "outlined.pdf")


@pytest.fixture
def nested_pages_pdf(tmp_path):
    return fixtures.make_nested_pages_pdf(tmp_path / "nested.pdf")


@pytest.fixture
def many_images_pdf(tmp_path):
    return fixtures.make_many_images_pdf(tmp_path / "many_images.pdf")
