"""Application entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from pdfeditor import __version__
from pdfeditor.constants import APP_ID, ORG_NAME
from pdfeditor.core.settings import Settings
from pdfeditor.i18n import LANGUAGES, install_translators, system_lang

__all__ = [
    "configure_logging",
    "install_translators",
    "log_path",
    "main",
    "parse_args",
    "self_check",
    "system_lang",
]

log = logging.getLogger(__name__)

#: Environment variable naming the directory of the file log instead of
#: ``AppLocalDataLocation/logs``; also turns the file log on outside a frozen build.
LOG_DIR_ENV = "PDFEDITOR_LOG_DIR"
LOG_NAME = "pdfeditor.log"
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUPS = 3
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
SELF_CHECK_LOG = "self-check.log"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pdfeditor", description="PDF Editor")
    parser.add_argument("file", nargs="?", help="PDF file to open")
    parser.add_argument("--lang", choices=LANGUAGES, help="user interface language")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # Hidden test hooks (frozen smoke tests, scripts/smoke_frozen.py).
    parser.add_argument("--self-check", metavar="DIR", help=argparse.SUPPRESS)
    parser.add_argument("--quit-after", metavar="MS", type=int, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def is_frozen() -> bool:
    """Running from the PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def log_path() -> Path:
    """Where the file log is written: ``$PDFEDITOR_LOG_DIR/pdfeditor.log`` or
    ``AppLocalDataLocation/logs/pdfeditor.log`` (``%LOCALAPPDATA%\\PDFEditor\\logs`` in
    the frozen build). Needs the application's organisation and name to be set."""
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        return Path(override) / LOG_NAME
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(base) / "logs" / LOG_NAME


def _drop_file_handlers(root: logging.Logger) -> None:
    for handler in list(root.handlers):
        if getattr(handler, "_pdfeditor", False):
            root.removeHandler(handler)
            handler.close()


def configure_logging(frozen: bool, path: Path | None = None) -> Path | None:
    """Set up logging; returns the file log's path, or None without a file log.

    A rotating file log (1 MB x 3) at ``path`` (default :func:`log_path`) when ``frozen``
    or ``path`` is given or ``$PDFEDITOR_LOG_DIR`` is set: a windowed build has no
    stderr. Plus a stderr handler when there is a stderr and the root logger has no
    handler yet (development). Replaces the file handler of an earlier call. Call it
    after the application's organisation and name are set (they make the path)."""
    root = logging.getLogger()
    _drop_file_handlers(root)
    if path is None and (frozen or os.environ.get(LOG_DIR_ENV)):
        path = log_path()
    if sys.stderr is not None and not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
    except OSError as exc:
        log.warning("cannot write the log file %s: %s", path, exc)
        return None
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler._pdfeditor = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    log.info("PDF Editor %s (%s), log: %s", __version__, "frozen" if frozen else "dev", path)
    return path


def self_check(directory: str | os.PathLike[str]) -> int:
    """The hidden ``--self-check DIR`` mode (no window): see :mod:`pdfeditor.self_check`.
    Logs to ``DIR/self-check.log``; returns 0 iff every check passed."""
    from pdfeditor import self_check as checks

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    configure_logging(is_frozen(), out / SELF_CHECK_LOG)
    try:
        return checks.run(out)
    finally:
        _drop_file_handlers(logging.getLogger())


def _log_uncaught(kind, value, tb) -> None:  # noqa: ANN001 - sys.excepthook signature
    log.critical("uncaught exception", exc_info=(kind, value, tb))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = parse_args(argv[1:])
    if args.self_check:
        return self_check(args.self_check)

    if QApplication.instance() is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    app = QApplication.instance() or QApplication(argv)
    # Before anything reads QStandardPaths (log, signatures) or QSettings.
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_ID)
    app.setApplicationVersion(__version__)
    frozen = is_frozen()
    configure_logging(frozen)
    if frozen:
        sys.excepthook = _log_uncaught

    settings = Settings()
    install_translators(app, args.lang or settings.language or system_lang())

    from pdfeditor.ui.main_window import MainWindow

    window = MainWindow(settings)
    window.show()
    if args.file:
        QTimer.singleShot(0, lambda: window.open_file(args.file))
    if args.quit_after is not None:
        log.info("quitting after %d ms (--quit-after)", args.quit_after)
        QTimer.singleShot(max(0, args.quit_after), app.quit)
    return app.exec()
