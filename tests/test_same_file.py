"""Own-path guard against path aliases (M5 review finding 1): junctions, ``\\\\?\\``
prefixes, 8.3 short names and UNC administrative shares name the open file too."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

import fixtures
import pytest

from pdfeditor.core.document import ExportOptions, PdfDocument
from pdfeditor.core.files import same_file
from pdfeditor.ui.main_window import same_path

pytestmark = [
    pytest.mark.usefixtures("qapp"),
    pytest.mark.skipif(sys.platform != "win32", reason="Windows path aliases"),
]


@pytest.fixture
def real_pdf(tmp_path) -> Path:
    folder = tmp_path / "Real Folder with spaces"
    folder.mkdir()
    return fixtures.make_simple_pdf(folder / "document é.pdf")


def _junction(real_pdf: Path) -> str:
    link = real_pdf.parent.parent / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(real_pdf.parent)],
        capture_output=True,
    )
    if result.returncode != 0 or not link.exists():
        pytest.skip("cannot create a directory junction")
    return str(link / real_pdf.name)


def _short_name(real_pdf: Path) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(real_pdf), buf, len(buf))
    short = buf.value if 0 < n < len(buf) else ""
    if not short or os.path.normcase(short) == os.path.normcase(str(real_pdf)):
        pytest.skip("8.3 short names are disabled on this volume")
    return short


def _unc(real_pdf: Path) -> str:
    drive, rest = os.path.splitdrive(str(real_pdf))
    unc = "\\\\localhost\\" + drive[0] + "$" + rest
    if not os.path.exists(unc):
        pytest.skip("administrative share \\\\localhost\\C$ not accessible")
    return unc


ALIASES = {
    "junction": _junction,
    "prefix": lambda p: "\\\\?\\" + str(p),
    "short_name": _short_name,
    "unc": _unc,
}


@pytest.fixture(params=list(ALIASES))
def alias(request, real_pdf) -> str:
    return ALIASES[request.param](real_pdf)


def test_alias_is_same_file(real_pdf, alias) -> None:
    assert os.path.normcase(alias) != os.path.normcase(str(real_pdf))
    assert same_file(alias, real_pdf)
    assert same_file(str(real_pdf), alias)
    assert same_path(alias, str(real_pdf))  # the UI guard shares the helper


def test_export_through_alias_refused(real_pdf, alias) -> None:
    original = real_pdf.read_bytes()
    doc = PdfDocument.open(real_pdf)
    doc.set_page_rotation(0, 90)
    with pytest.raises(ValueError):
        doc.export_copy(alias, ExportOptions())
    assert doc.can_save_incrementally()  # nothing was written
    doc.close()
    assert real_pdf.read_bytes() == original
    assert not os.path.exists(str(real_pdf) + ".tmp")


def test_save_as_alias_keeps_path(real_pdf, alias) -> None:
    doc = PdfDocument.open(real_pdf)
    changed: list[str] = []
    doc.path_changed.connect(changed.append)
    doc.save_as(alias)
    assert doc.path == str(real_pdf)
    assert changed == []
    doc.close()


def test_different_or_missing_files(tmp_path, real_pdf) -> None:
    other = fixtures.make_simple_pdf(tmp_path / "other.pdf")
    assert not same_file(other, real_pdf)
    assert not same_file(tmp_path / "missing.pdf", real_pdf)
    assert not same_file(tmp_path / "missing.pdf", tmp_path / "missing2.pdf")
    assert same_file(tmp_path / "missing.pdf", str(tmp_path / "MISSING.PDF"))
    assert not same_file("a\0b.pdf", real_pdf)
