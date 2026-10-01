from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings

from pdfeditor.app import parse_args
from pdfeditor.constants import ZoomMode
from pdfeditor.core.settings import Settings
from pdfeditor.ui.main_window import MainWindow


def _reopen(ini_path) -> Settings:
    return Settings(QSettings(str(ini_path), QSettings.Format.IniFormat))


def test_defaults(settings: Settings) -> None:
    assert settings.language is None
    assert settings.zoom_mode is ZoomMode.FIT_WIDTH
    assert settings.zoom_percent == 100.0
    assert settings.thumbnails_visible is True
    assert settings.window_geometry.isEmpty()
    assert settings.window_state.isEmpty()
    assert settings.last_open_dir == ""
    assert settings.recent_files == []
    assert not hasattr(settings, "flatten_on_export")
    assert settings.export_flatten_forms is True
    assert settings.export_flatten_annots is True
    assert settings.export_keep_encryption is True
    assert settings.export_keep_metadata is True
    assert settings.highlight_fields is True
    assert settings.auto_shrink_text is True
    assert settings.signature_width == 150.0


def test_round_trip_all_properties(settings: Settings, ini_path) -> None:
    settings.language = "fr"
    settings.zoom_mode = ZoomMode.CUSTOM
    settings.zoom_percent = 133.5
    settings.thumbnails_visible = False
    settings.window_geometry = QByteArray(b"\x01\x02geo")
    settings.window_state = QByteArray(b"\x03state")
    settings.last_open_dir = "C:/Users/Test/Documents"
    settings.recent_files = ["C:/a.pdf", "C:/b é.pdf"]
    settings.export_flatten_forms = False
    settings.export_flatten_annots = False
    settings.export_keep_encryption = False
    settings.export_keep_metadata = False
    settings.highlight_fields = False
    settings.auto_shrink_text = False
    settings.signature_width = 182.5
    settings.sync()
    assert ini_path.exists()

    s = _reopen(ini_path)
    assert s.language == "fr"
    assert s.zoom_mode is ZoomMode.CUSTOM
    assert s.zoom_percent == 133.5
    assert s.thumbnails_visible is False
    assert bytes(s.window_geometry.data()) == b"\x01\x02geo"
    assert bytes(s.window_state.data()) == b"\x03state"
    assert s.last_open_dir == "C:/Users/Test/Documents"
    assert s.recent_files == ["C:/a.pdf", "C:/b é.pdf"]
    assert s.export_flatten_forms is False
    assert s.export_flatten_annots is False
    assert s.export_keep_encryption is False
    assert s.export_keep_metadata is False
    assert s.highlight_fields is False
    assert s.auto_shrink_text is False
    assert s.signature_width == 182.5


def test_export_choices_mixed(settings: Settings, ini_path) -> None:
    settings.export_flatten_forms = True
    settings.export_flatten_annots = False
    settings.sync()
    s = _reopen(ini_path)
    assert s.export_flatten_forms is True
    assert s.export_flatten_annots is False
    assert s.export_keep_encryption is True
    text = ini_path.read_text(encoding="utf-8")
    assert "[export]" in text
    assert "flatten_on_export" not in text


def test_single_recent_file_and_language_reset(settings: Settings, ini_path) -> None:
    settings.recent_files = ["C:/only.pdf"]
    settings.language = "en"
    settings.language = None
    settings.sync()
    s = _reopen(ini_path)
    assert s.recent_files == ["C:/only.pdf"]
    assert s.language is None


def test_invalid_values_fall_back(settings: Settings) -> None:
    settings.qsettings.setValue("view/zoom_mode", "bogus")
    settings.qsettings.setValue("ui/language", "de")
    settings.qsettings.setValue("view/zoom_percent", "abc")
    assert settings.zoom_mode is ZoomMode.FIT_WIDTH
    assert settings.language is None
    assert settings.zoom_percent == 100.0
    settings.qsettings.setValue("annots/signature_width", "wide")
    assert settings.signature_width == 150.0
    settings.qsettings.setValue("annots/signature_width", -5)
    assert settings.signature_width == 150.0


def test_geometry_restored(qtbot, settings: Settings, ini_path) -> None:
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.setGeometry(50, 60, 640, 480)
    w.show()
    qtbot.waitExposed(w)
    w.close()
    assert ini_path.exists()
    assert "geometry" in ini_path.read_text(encoding="utf-8")

    w2 = MainWindow(_reopen(ini_path))
    qtbot.addWidget(w2)
    assert w2.size().width() == 640
    assert w2.size().height() == 480


def test_parse_args() -> None:
    ns = parse_args(["doc.pdf", "--lang", "fr"])
    assert ns.file == "doc.pdf"
    assert ns.lang == "fr"
    assert parse_args([]).file is None
