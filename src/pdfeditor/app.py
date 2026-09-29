"""Application entry point."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from pdfeditor import __version__
from pdfeditor.constants import APP_ID, ORG_NAME
from pdfeditor.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_ID)
    app.setApplicationVersion(__version__)
    window = MainWindow()
    window.show()
    return app.exec()
