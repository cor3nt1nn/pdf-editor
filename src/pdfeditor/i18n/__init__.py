"""Translations: .ts sources and compiled .qm files, plus translator installation."""

from __future__ import annotations

import logging
from importlib import resources

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

log = logging.getLogger(__name__)

LANGUAGES = ("en", "fr")
#: Native language names shown in the Language menu (never translated).
LANGUAGE_NAMES = {"en": "English", "fr": "Français"}

_installed: list[QTranslator] = []
_current = "en"


def system_lang() -> str:
    """Default UI language: French if the system locale is French, else English."""
    return "fr" if QLocale.system().language() == QLocale.Language.French else "en"


def current_language() -> str:
    """Language whose translators are installed ("en" when none)."""
    return _current


def remove_translators(app: QCoreApplication) -> None:
    global _current
    for translator in _installed:
        app.removeTranslator(translator)
    _installed.clear()
    _current = "en"


def install_translators(app: QCoreApplication, lang: str) -> list[QTranslator]:
    """Install Qt base (qtbase_<lang>.qm) and application (pdfeditor_<lang>.qm) translators.

    Replaces previously installed ones. Returns the installed translators.
    """
    global _current
    remove_translators(app)
    if lang not in LANGUAGES:
        log.warning("unsupported language %r, using English", lang)
        lang = "en"
    _current = lang
    if lang == "en":
        return []
    qt_dir = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    base = QTranslator(app)
    if base.load(f"qtbase_{lang}", qt_dir):
        app.installTranslator(base)
        _installed.append(base)
    else:
        log.warning("Qt base translation for %s not found in %s", lang, qt_dir)
    ours = QTranslator(app)
    qm = resources.files(__name__).joinpath(f"pdfeditor_{lang}.qm")
    with resources.as_file(qm) as qm_path:
        if qm_path.is_file() and ours.load(str(qm_path)):
            app.installTranslator(ours)
            _installed.append(ours)
        else:
            log.warning("application translation for %s not found", lang)
    return list(_installed)
