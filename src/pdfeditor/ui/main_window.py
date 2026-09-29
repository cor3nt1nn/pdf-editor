"""Main application window."""

from __future__ import annotations

import logging

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow

from pdfeditor.constants import APP_NAME
from pdfeditor.core.settings import Settings

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.settings = settings if settings is not None else Settings()
        self.setObjectName("MainWindow")
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 800)
        self._restore_window_state()

    def _restore_window_state(self) -> None:
        geometry = self.settings.window_geometry
        if not geometry.isEmpty():
            self.restoreGeometry(geometry)
        state = self.settings.window_state
        if not state.isEmpty():
            self.restoreState(state)

    def _save_window_state(self) -> None:
        self.settings.window_geometry = self.saveGeometry()
        self.settings.window_state = self.saveState()
        self.settings.sync()

    def open_file(self, path: str) -> bool:
        log.info("open requested: %s", path)
        return False

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_window_state()
        super().closeEvent(event)
