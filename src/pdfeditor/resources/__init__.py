"""Packaged resources: SVG icons and the Windows application icon."""

from __future__ import annotations

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
