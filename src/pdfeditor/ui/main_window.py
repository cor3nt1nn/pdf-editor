"""Main application window."""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow

from pdfeditor.constants import APP_NAME


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 800)
