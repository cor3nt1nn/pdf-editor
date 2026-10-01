"""Packaged resources: SVG icons, the Windows application icon and licence texts."""

from __future__ import annotations

import sys
from functools import cache
from importlib import resources
from pathlib import Path

from PySide6.QtGui import QIcon


def resource_path(*parts: str) -> Path:
    """Filesystem path of a packaged resource (the package is always installed unzipped)."""
    return Path(str(resources.files(__name__).joinpath(*parts)))


@cache
def icon(name: str) -> QIcon:
    """QIcon for ``icons/<name>.svg`` (null icon if missing)."""
    path = resource_path("icons", f"{name}.svg")
    return QIcon(str(path)) if path.is_file() else QIcon()


def app_icon() -> QIcon:
    ico = resource_path("app.ico")
    result = QIcon(str(ico)) if ico.is_file() else QIcon()
    svg = resource_path("icons", "app.svg")
    if svg.is_file():
        result.addFile(str(svg))
    return result


#: Name of the third-party licence notice, next to PDFEditor.exe in the frozen build and
#: at the root of the source tree.
THIRD_PARTY_NOTICE = "THIRD_PARTY_LICENSES.md"


def license_files() -> list[Path]:
    """Licence texts shipped in ``licenses/`` (sorted by file name)."""
    folder = resource_path("licenses")
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix == ".txt" and p.is_file())


def third_party_notice() -> Path | None:
    """``THIRD_PARTY_LICENSES.md``: next to the exe when frozen, else at the source root."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / THIRD_PARTY_NOTICE)
    candidates.append(Path(__file__).resolve().parents[3] / THIRD_PARTY_NOTICE)
    return next((p for p in candidates if p.is_file()), None)
