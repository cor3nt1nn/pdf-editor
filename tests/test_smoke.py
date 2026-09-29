from __future__ import annotations

from pdfeditor import __version__
from pdfeditor.ui.main_window import MainWindow


def test_version() -> None:
    assert __version__ == "0.1.0"


def test_main_window_title(qtbot) -> None:
    w = MainWindow()
    qtbot.addWidget(w)
    assert w.windowTitle() == "PDF Editor"
