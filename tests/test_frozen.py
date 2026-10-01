"""Tests of the frozen build (PyInstaller, dist/PDFEditor/PDFEditor.exe).

Skipped unless ``PDFEDITOR_FROZEN_EXE`` names the built exe:

    powershell -File scripts\\build_exe.ps1 -Smoke
    # or, after a build:
    $env:PDFEDITOR_FROZEN_EXE = "$PWD\\dist\\PDFEditor\\PDFEditor.exe"; uv run pytest -m frozen

The GUI launch needs a desktop session.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from pdfcheck import strict_read

from pdfeditor import __version__

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import smoke_frozen  # noqa: E402

EXE = os.environ.get("PDFEDITOR_FROZEN_EXE")

pytestmark = [
    pytest.mark.frozen,
    pytest.mark.skipif(not EXE, reason="PDFEDITOR_FROZEN_EXE is not set (needs a build)"),
]

MAX_FOLDER_MB = 110
QT_DLLS = {"Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Svg.dll"}
QT_PLUGINS = {
    "platforms/qwindows.dll",
    "platforms/qoffscreen.dll",
    "imageformats/qsvg.dll",
    "imageformats/qjpeg.dll",
    "imageformats/qico.dll",
    "imageformats/qgif.dll",
    "imageformats/qtiff.dll",
    "imageformats/qwebp.dll",
    "iconengines/qsvgicon.dll",
    "styles/qmodernwindowsstyle.dll",
}
PASSWORD = "frozen-secret"


@pytest.fixture(scope="module")
def exe() -> Path:
    path = Path(EXE or "")
    assert path.is_file(), f"{path} not found"
    return path


@pytest.fixture(scope="module")
def self_check(exe, tmp_path_factory) -> tuple[int, dict, Path]:
    out = tmp_path_factory.mktemp("frozen") / "self-check"
    code, report = smoke_frozen.run_self_check(exe, out)
    return code, report, out


def test_self_check_report(self_check) -> None:
    code, report, out = self_check
    failed = [c for c in report.get("checks", []) if not c["ok"]]
    assert failed == []
    assert code == 0
    assert report["ok"] is True
    assert report["frozen"] is True
    assert report["version"] == __version__
    checks = {c["name"]: c["detail"] for c in report["checks"]}
    assert checks["translators"]["count"] == 2
    assert all(checks["icons"].values())
    assert {"svg", "jpeg", "png", "tiff", "webp", "bmp", "gif"} <= set(checks["image_formats"])
    assert Path(checks["paths"]["app_local_data"]).name == "PDFEditor"
    assert (out / "self-check.log").is_file()


@pytest.mark.parametrize(
    ("name", "password"),
    [
        ("form-aes256.pdf", PASSWORD),
        ("form-flattened.pdf", PASSWORD),
        ("form-clean.pdf", PASSWORD),
        ("annotations.pdf", None),
        ("annotations-flattened.pdf", None),
        ("annotations-clean.pdf", None),
    ],
)
def test_written_files_pass_pypdf_strict(self_check, name, password) -> None:
    out = self_check[2]
    result = strict_read(out / name, password)
    if "flattened" in name:
        assert result.fields is None
        assert all(not page.get("/Annots") for page in result.reader.pages)
    elif name.startswith("form"):
        assert result.fields is not None and "name" in result.fields


def test_gui_launch_french(exe, self_check, tmp_path) -> None:
    pdf = self_check[2] / "annotations.pdf"
    code, log_file = smoke_frozen.run_gui(exe, pdf, tmp_path)
    assert code == 0
    assert log_file.is_file()
    text = log_file.read_text(encoding="utf-8")
    assert smoke_frozen.problem_lines(text) == []
    assert "(frozen)" in text and "--quit-after" in text
    # The test settings went to the override directory, not the user's INI file.
    assert (tmp_path / "settings" / "PDFEditor.ini").is_file()


def test_version_flag_exits_cleanly(exe) -> None:
    assert smoke_frozen.run_version(exe) == 0


def test_version_resource(exe) -> None:
    info = smoke_frozen.file_version(exe)
    assert info["ProductVersion"] == __version__
    assert info["FileVersion"] == __version__
    assert info["fixed"].startswith(__version__)


def test_bundle_is_pruned(exe) -> None:
    qt = exe.parent / "_internal" / "PySide6"
    assert {p.name for p in qt.glob("Qt6*.dll")} == QT_DLLS
    plugins = {p.relative_to(qt / "plugins").as_posix() for p in (qt / "plugins").rglob("*.dll")}
    assert plugins == QT_PLUGINS
    assert [p.name for p in (qt / "translations").iterdir()] == ["qtbase_fr.qm"]
    assert not (qt / "opengl32sw.dll").exists()
    size = sum(p.stat().st_size for p in exe.parent.rglob("*") if p.is_file())
    assert size <= MAX_FOLDER_MB * 1024 * 1024
