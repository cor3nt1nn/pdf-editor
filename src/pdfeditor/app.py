"""Application entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from pdfeditor import __version__
from pdfeditor.constants import APP_ID, ORG_NAME
from pdfeditor.core.settings import Settings
from pdfeditor.core.snapshots import sweep_orphans
from pdfeditor.i18n import LANGUAGES, install_translators, system_lang
from pdfeditor.paths import LOG_DIR_ENV, LOG_NAME, is_frozen, log_path

__all__ = [
    "LOG_DIR_ENV",
    "LOG_NAME",
    "REGISTER_FLAG",
    "UNREGISTER_FLAG",
    "configure_logging",
    "file_type_registration",
    "install_translators",
    "is_frozen",
    "log_path",
    "main",
    "parse_args",
    "self_check",
    "system_lang",
]

log = logging.getLogger(__name__)

LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUPS = 3
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
SELF_CHECK_LOG = "self-check.log"
#: Hidden first argument of the OCR worker process (see ``core/ocr_worker.py``).
OCR_WORKER_FLAG = "--ocr-worker"
#: Hidden first arguments run by the Windows installer (M8): add PDF Editor to, or remove
#: it from, the "Open with" list of PDF files (``core/file_assoc.py``), then exit.
REGISTER_FLAG = "--register-file-type"
UNREGISTER_FLAG = "--unregister-file-type"
#: Exit codes of those modes (the installer logs them, installer/pdfeditor.iss): done, the
#: registry refused (``OSError``: access denied, a policy...), any other failure.
EXIT_REGISTERED = 0
EXIT_REGISTRY_ERROR = 2
EXIT_REGISTER_FAILED = 3
#: Named mutex held by the running (frozen) application: installer/pdfeditor.iss's
#: ``AppMutex``, so Setup and the uninstaller ask to close PDF Editor first.
INSTANCE_MUTEX = "PDFEditor-2F4F77DF-7029-452C-AE27-01CC3FD48311"
#: The handle, kept open for the life of the process (Windows closes it at exit).
_instance_mutex: int | None = None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pdfeditor", description="PDF Editor")
    parser.add_argument("file", nargs="?", help="PDF file to open")
    parser.add_argument("--lang", choices=LANGUAGES, help="user interface language")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # Hidden test hooks (frozen smoke tests, scripts/smoke_frozen.py).
    parser.add_argument("--self-check", metavar="DIR", help=argparse.SUPPRESS)
    parser.add_argument("--quit-after", metavar="MS", type=int, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


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


def file_type_registration(register: bool) -> int:
    """The hidden ``--register-file-type`` / ``--unregister-file-type`` modes (no window,
    no Qt application, no file log: the uninstaller runs it and must not recreate the
    user's data folder). Returns :data:`EXIT_REGISTERED` (0) on success,
    :data:`EXIT_REGISTRY_ERROR` (2) when the registry refused (``OSError``) and
    :data:`EXIT_REGISTER_FAILED` (3) on any other failure; never raises, since a windowed
    build would show a traceback box in the middle of a silent install.

    Unregistering leaves alone a registration that opens another copy of the app (say the
    portable zip): uninstalling one copy must not remove the other's "Open with" entry."""
    from pdfeditor.core import file_assoc

    try:
        if register:
            file_assoc.register()
        else:
            current = file_assoc.registered_command()
            ours = file_assoc.command_line(file_assoc.app_command())
            if current is not None and current.casefold() != ours.casefold():
                log.info("PDF files are registered to another copy (%s): kept", current)
                return EXIT_REGISTERED
            file_assoc.unregister()
    except OSError:
        log.exception("could not %s the PDF file type", "register" if register else "unregister")
        return EXIT_REGISTRY_ERROR
    except Exception:  # noqa: BLE001 - reported by the exit code
        log.exception("could not %s the PDF file type", "register" if register else "unregister")
        return EXIT_REGISTER_FAILED
    return EXIT_REGISTERED


def create_instance_mutex(name: str = INSTANCE_MUTEX) -> int | None:
    """Create (or open) the named mutex ``name`` and keep it for the life of the process;
    returns its handle, None off Windows or on failure (never fatal). Several running
    copies share it."""
    global _instance_mutex
    if sys.platform != "win32":
        return None
    if _instance_mutex is not None:
        return _instance_mutex
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        log.warning("could not create the mutex %s (error %d)", name, ctypes.get_last_error())
        return None
    _instance_mutex = int(handle)
    return _instance_mutex


def _log_uncaught(kind, value, tb) -> None:  # noqa: ANN001 - sys.excepthook signature
    log.critical("uncaught exception", exc_info=(kind, value, tb))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if argv[1:2] == [OCR_WORKER_FLAG]:
        # The OCR worker process (M8): no window, no Qt application.
        from pdfeditor.core.ocr_worker import main as ocr_worker_main

        return ocr_worker_main()
    if argv[1:2] in ([REGISTER_FLAG], [UNREGISTER_FLAG]):
        return file_type_registration(argv[1] == REGISTER_FLAG)
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
        create_instance_mutex()  # the installer's AppMutex: "close PDF Editor first"
    sweep_orphans()  # undo copies left in %TEMP% by a crashed session

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
