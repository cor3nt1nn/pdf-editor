"""Typed wrapper around QSettings (INI format)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, QStandardPaths

from pdfeditor.constants import APP_ID, ORG_NAME, ZoomMode

LANGUAGES = ("en", "fr")


def default_qsettings() -> QSettings:
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
        return [str(v) for v in value]

    @recent_files.setter
    def recent_files(self, value: list[str]) -> None:
        self._s.setValue("files/recent", list(value))

    @property
    def flatten_on_export(self) -> bool:
        return self._bool("files/flatten_on_export", False)

    @flatten_on_export.setter
    def flatten_on_export(self, value: bool) -> None:
        self._s.setValue("files/flatten_on_export", bool(value))

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
