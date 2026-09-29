"""Application entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from importlib import resources

from PySide6.QtCore import QLibraryInfo, QLocale, Qt, QTimer, QTranslator
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from pdfeditor import __version__
from pdfeditor.constants import APP_ID, ORG_NAME
from pdfeditor.core.settings import LANGUAGES, Settings

log = logging.getLogger(__name__)

_translators: list[QTranslator] = []  # keep installed translators alive


def system_lang() -> str:
    """Default UI language: French if the system locale is French, else English."""
    return "fr" if QLocale.system().language() == QLocale.Language.French else "en"


def install_translators(app: QApplication, lang: str) -> list[QTranslator]:
    """Install Qt base and application translators for ``lang``. Returns them (keep alive)."""
    installed: list[QTranslator] = []
    if lang == "en":
        return installed
    qt_dir = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    base = QTranslator(app)
    if base.load(f"qtbase_{lang}", qt_dir):
        app.installTranslator(base)
        installed.append(base)
    else:
        log.warning("Qt base translation for %s not found in %s", lang, qt_dir)
    ours = QTranslator(app)
    try:
        qm = resources.files("pdfeditor.i18n").joinpath(f"pdfeditor_{lang}.qm")
        with resources.as_file(qm) as qm_path:
            if qm_path.exists() and ours.load(str(qm_path)):
                app.installTranslator(ours)
                installed.append(ours)
            else:
                log.warning("Application translation for %s not found", lang)
    except (ModuleNotFoundError, FileNotFoundError):
        log.warning("Application translation for %s not available", lang)
    return installed


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
    lang = args.lang or settings.language or system_lang()
    _translators[:] = install_translators(app, lang)

    from pdfeditor.ui.main_window import MainWindow

    window = MainWindow(settings)
    window.show()
    if args.file:
        QTimer.singleShot(0, lambda: window.open_file(args.file))
    return app.exec()
