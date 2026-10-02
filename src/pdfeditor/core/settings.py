"""Typed wrapper around QSettings (INI format)."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, QStandardPaths
from PySide6.QtGui import QColor

from pdfeditor.constants import APP_ID, ORG_NAME, ZoomMode
from pdfeditor.core.recent import RECENT_MAX

LANGUAGES = ("en", "fr")
#: Default colour ("#rrggbb") of new text markups, by markup kind (``AnnotKind`` value).
MARKUP_COLOR_DEFAULTS = {
    "highlight": "#ffff00",
    "underline": "#ff0000",
    "strikeout": "#ff0000",
}
#: Environment variable naming a directory that holds the INI file instead of the
#: per-user location (``%APPDATA%\PDFEditor\PDFEditor.ini``). Used by the frozen tests.
SETTINGS_DIR_ENV = "PDFEDITOR_SETTINGS_DIR"


def default_qsettings() -> QSettings:
    """The application's INI settings: ``$PDFEDITOR_SETTINGS_DIR/PDFEditor.ini`` when that
    variable is set, else the per-user INI file."""
    override = os.environ.get(SETTINGS_DIR_ENV)
    if override:
        return QSettings(str(Path(override) / f"{APP_ID}.ini"), QSettings.Format.IniFormat)
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, ORG_NAME, APP_ID)


def data_dir() -> Path:
    """Per-user application data directory (created on demand)."""
    path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    path.mkdir(parents=True, exist_ok=True)
    return path


class Settings:
    """Typed accessors for persisted application settings."""

    def __init__(self, qsettings: QSettings | None = None) -> None:
        self._s = qsettings if qsettings is not None else default_qsettings()

    @property
    def qsettings(self) -> QSettings:
        return self._s

    def file_name(self) -> str:
        return self._s.fileName()

    def sync(self) -> None:
        self._s.sync()

    # -- helpers -----------------------------------------------------------
    def _str(self, key: str, default: str | None = None) -> str | None:
        value = self._s.value(key, default)
        if value is None or value == "":
            return default
        return str(value)

    def _bool(self, key: str, default: bool) -> bool:
        value = self._s.value(key, default)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def _bytes(self, key: str) -> QByteArray:
        value = self._s.value(key)
        if isinstance(value, QByteArray):
            return value
        if isinstance(value, bytes | bytearray):
            return QByteArray(bytes(value))
        return QByteArray()

    # -- properties --------------------------------------------------------
    @property
    def language(self) -> str | None:
        value = self._str("ui/language")
        return value if value in LANGUAGES else None

    @language.setter
    def language(self, value: str | None) -> None:
        if value is None:
            self._s.remove("ui/language")
        elif value in LANGUAGES:
            self._s.setValue("ui/language", value)
        else:
            raise ValueError(f"unsupported language: {value!r}")

    @property
    def zoom_mode(self) -> ZoomMode:
        try:
            return ZoomMode(self._str("view/zoom_mode", ZoomMode.FIT_WIDTH.value))
        except ValueError:
            return ZoomMode.FIT_WIDTH

    @zoom_mode.setter
    def zoom_mode(self, value: ZoomMode) -> None:
        self._s.setValue("view/zoom_mode", ZoomMode(value).value)

    @property
    def zoom_percent(self) -> float:
        try:
            return float(self._s.value("view/zoom_percent", 100.0))
        except (TypeError, ValueError):
            return 100.0

    @zoom_percent.setter
    def zoom_percent(self, value: float) -> None:
        self._s.setValue("view/zoom_percent", float(value))

    @property
    def thumbnails_visible(self) -> bool:
        return self._bool("view/thumbnails_visible", True)

    @thumbnails_visible.setter
    def thumbnails_visible(self, value: bool) -> None:
        self._s.setValue("view/thumbnails_visible", bool(value))

    @property
    def window_geometry(self) -> QByteArray:
        return self._bytes("window/geometry")

    @window_geometry.setter
    def window_geometry(self, value: QByteArray) -> None:
        self._s.setValue("window/geometry", QByteArray(value))

    @property
    def window_state(self) -> QByteArray:
        return self._bytes("window/state")

    @window_state.setter
    def window_state(self, value: QByteArray) -> None:
        self._s.setValue("window/state", QByteArray(value))

    @property
    def last_open_dir(self) -> str:
        return self._str("files/last_open_dir", "") or ""

    @last_open_dir.setter
    def last_open_dir(self, value: str) -> None:
        self._s.setValue("files/last_open_dir", str(value))

    @property
    def recent_files(self) -> list[str]:
        value = self._s.value("files/recent", [])
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [value]
        return [str(v) for v in value if v][:RECENT_MAX]

    @recent_files.setter
    def recent_files(self, value: list[str]) -> None:
        """Paths only (never passwords), most recent first, at most ``RECENT_MAX``."""
        self._s.setValue("files/recent", [str(v) for v in value][:RECENT_MAX])

    # Export Copy dialog choices (docs/M5_PLAN.md 1.1), all on by default.
    @property
    def export_flatten_forms(self) -> bool:
        return self._bool("export/flatten_forms", True)

    @export_flatten_forms.setter
    def export_flatten_forms(self, value: bool) -> None:
        self._s.setValue("export/flatten_forms", bool(value))

    @property
    def export_flatten_annots(self) -> bool:
        return self._bool("export/flatten_annots", True)

    @export_flatten_annots.setter
    def export_flatten_annots(self, value: bool) -> None:
        self._s.setValue("export/flatten_annots", bool(value))

    @property
    def export_keep_encryption(self) -> bool:
        return self._bool("export/keep_encryption", True)

    @export_keep_encryption.setter
    def export_keep_encryption(self, value: bool) -> None:
        self._s.setValue("export/keep_encryption", bool(value))

    @property
    def export_keep_metadata(self) -> bool:
        return self._bool("export/keep_metadata", True)

    @export_keep_metadata.setter
    def export_keep_metadata(self, value: bool) -> None:
        self._s.setValue("export/keep_metadata", bool(value))

    @property
    def highlight_fields(self) -> bool:
        """Highlight fillable form fields."""
        return self._bool("forms/highlight_fields", True)

    @highlight_fields.setter
    def highlight_fields(self, value: bool) -> None:
        self._s.setValue("forms/highlight_fields", bool(value))

    @property
    def auto_shrink_text(self) -> bool:
        """Switch a single-line text field to auto-size when the typed text overflows."""
        return self._bool("forms/auto_shrink_text", True)

    @auto_shrink_text.setter
    def auto_shrink_text(self, value: bool) -> None:
        self._s.setValue("forms/auto_shrink_text", bool(value))

    def _float(self, key: str, default: float) -> float:
        try:
            value = float(self._s.value(key, default))
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    @property
    def annot_font_size(self) -> float:
        """Font size (points) of new text boxes."""
        return self._float("annots/font_size", 11.0)

    @annot_font_size.setter
    def annot_font_size(self, value: float) -> None:
        self._s.setValue("annots/font_size", float(value))

    @property
    def annot_color(self) -> str:
        """Colour ("#rrggbb") of new text boxes and stamps."""
        return self._str("annots/color", "#000000") or "#000000"

    @annot_color.setter
    def annot_color(self, value: str) -> None:
        self._s.setValue("annots/color", str(value))

    @property
    def stamp_size(self) -> float:
        """Side (points) of a stamp placed outside a checkbox."""
        return self._float("annots/stamp_size", 12.0)

    @stamp_size.setter
    def stamp_size(self, value: float) -> None:
        self._s.setValue("annots/stamp_size", float(value))

    @property
    def signature_width(self) -> float:
        """Default width (points) of a placed signature."""
        return self._float("annots/signature_width", 150.0)

    @signature_width.setter
    def signature_width(self, value: float) -> None:
        self._s.setValue("annots/signature_width", float(value))

    # -- text markups (M6b) ------------------------------------------------------------
    def markup_color(self, kind: str) -> str:
        """Colour ("#rrggbb") of new markups of ``kind`` ("highlight", "underline" or
        "strikeout"; ``KeyError`` for any other kind)."""
        default = MARKUP_COLOR_DEFAULTS[str(kind)]
        value = self._str(f"markup/{kind}_color", default) or default
        return value if QColor(value).isValid() else default

    def set_markup_color(self, kind: str, value: str) -> None:
        if str(kind) not in MARKUP_COLOR_DEFAULTS:
            raise KeyError(kind)
        self._s.setValue(f"markup/{kind}_color", str(value))

    @property
    def markup_highlight_color(self) -> str:
        return self.markup_color("highlight")

    @markup_highlight_color.setter
    def markup_highlight_color(self, value: str) -> None:
        self.set_markup_color("highlight", value)

    @property
    def markup_underline_color(self) -> str:
        return self.markup_color("underline")

    @markup_underline_color.setter
    def markup_underline_color(self, value: str) -> None:
        self.set_markup_color("underline", value)

    @property
    def markup_strikeout_color(self) -> str:
        return self.markup_color("strikeout")

    @markup_strikeout_color.setter
    def markup_strikeout_color(self, value: str) -> None:
        self.set_markup_color("strikeout", value)
