"""Application entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from pdfeditor import __version__
from pdfeditor.constants import APP_ID, ORG_NAME
from pdfeditor.core.settings import Settings
from pdfeditor.i18n import LANGUAGES, install_translators, system_lang

__all__ = ["install_translators", "main", "parse_args", "system_lang"]

log = logging.getLogger(__name__)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pdfeditor", description="PDF Editor")
    parser.add_argument("file", nargs="?", help="PDF file to open")
    parser.add_argument("--lang", choices=LANGUAGES, help="user interface language")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = parse_args(argv[1:])
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if QApplication.instance() is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    app = QApplication.instance() or QApplication(argv)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_ID)
    app.setApplicationVersion(__version__)

    settings = Settings()
    install_translators(app, args.lang or settings.language or system_lang())

    from pdfeditor.ui.main_window import MainWindow

    window = MainWindow(settings)
    window.show()
    if args.file:
        QTimer.singleShot(0, lambda: window.open_file(args.file))
    return app.exec()
