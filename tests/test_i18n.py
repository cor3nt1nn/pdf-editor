from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication, QMessageBox

import pdfeditor.app as app_module
import pdfeditor.i18n as i18n
from pdfeditor.ui import dialogs
from pdfeditor.ui import main_window as mw_module
from pdfeditor.ui.main_window import MainWindow, restart_command

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_i18n  # noqa: E402


@pytest.fixture
def french(qapp):
    i18n.install_translators(qapp, "fr")
    yield
    i18n.remove_translators(qapp)


def test_translations_complete() -> None:
    assert check_i18n.check(fresh=True) == []


def test_check_detects_unfinished(tmp_path) -> None:
    ts = tmp_path / "x.ts"
    ts.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="fr_FR">
<context><name>Ctx</name>
<message><source>Done</source><translation>Fait</translation></message>
<message><source>Todo</source><translation type="unfinished"></translation></message>
<message><source>Empty</source><translation></translation></message>
<message><source>Gone</source><translation type="vanished">Parti</translation></message>
</context></TS>
""",
        encoding="utf-8",
    )
    assert check_i18n.unfinished(ts) == [("Ctx", "Todo"), ("Ctx", "Empty")]


class _FakeLocale:
    def __init__(self, language):
        self._language = language

    def language(self):
        return self._language


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (QLocale.Language.French, "fr"),
        (QLocale.Language.English, "en"),
        (QLocale.Language.German, "en"),
    ],
)
def test_system_lang(monkeypatch, language, expected) -> None:
    class Patched:
        Language = QLocale.Language

        @staticmethod
        def system():
            return _FakeLocale(language)

    monkeypatch.setattr(i18n, "QLocale", Patched)
    assert i18n.system_lang() == expected
    assert app_module.system_lang() == expected


def test_install_and_remove(qapp) -> None:
    assert i18n.install_translators(qapp, "en") == []
    assert i18n.current_language() == "en"
    installed = i18n.install_translators(qapp, "fr")
    assert len(installed) == 2  # qtbase_fr + pdfeditor_fr
    assert i18n.current_language() == "fr"
    assert QApplication.translate("MainWindow", "&File") == "&Fichier"
    i18n.remove_translators(qapp)
    assert QApplication.translate("MainWindow", "&File") == "&File"


def test_french_ui(qtbot, french, settings, simple_pdf) -> None:
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.show()
    assert w.menu_file.title() == "&Fichier"
    assert w.menu_edit.title() == "&Édition"
    assert w.menu_view.title() == "&Affichage"
    assert w.act_open.text() == "&Ouvrir…"
    assert w.act_undo.text() == "Annuler"
    assert w.zoom_widget.itemText(0) == "Ajuster à la largeur"
    assert w.language_actions["fr"].isChecked()
    assert w.open_file(str(simple_pdf))
    assert w.status_page.text() == "Page 1 / 3"
    w.act_rotate_cw.trigger()
    assert w.act_undo.text() == "Annuler la rotation de la page"
    assert w.act_redo.text() == "Rétablir"
    w.undo_stack.setClean()
    w.close()


def test_french_standard_dialog_buttons(qtbot, french) -> None:
    box = QMessageBox(
        QMessageBox.Icon.Question,
        "t",
        "x",
        QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel,
    )
    qtbot.addWidget(box)
    box.show()
    assert box.button(QMessageBox.StandardButton.Save).text().replace("&", "") == "Enregistrer"
    assert box.button(QMessageBox.StandardButton.Cancel).text().replace("&", "") == "Annuler"


def test_main_lang_fr(qtbot, qapp, settings, monkeypatch) -> None:
    created: list[MainWindow] = []

    class Capturing(MainWindow):
        def __init__(self, s):
            super().__init__(settings)
            created.append(self)

    monkeypatch.setattr(mw_module, "MainWindow", Capturing)
    monkeypatch.setattr(app_module, "Settings", lambda: settings)
    monkeypatch.setattr(app_module.QApplication, "exec", staticmethod(lambda: 0))
    try:
        assert app_module.main(["pdfeditor", "--lang", "fr"]) == 0
        w = created[0]
        qtbot.addWidget(w)
        assert w.menu_file.title() == "&Fichier"
        assert w.menu_help.title() == "&Aide"
        w.close()
    finally:
        i18n.remove_translators(qapp)


def test_language_switch_prompts_and_relaunches(qtbot, settings, simple_pdf, monkeypatch):
    launched: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        mw_module.QProcess,
        "startDetached",
        staticmethod(lambda program, args: launched.append((program, list(args))) or (True, 1)),
    )
    asked: list[bool] = []
    monkeypatch.setattr(dialogs, "ask_restart", lambda parent: asked.append(True) or True)
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.show()
    w.open_file(str(simple_pdf))
    assert i18n.current_language() == "en"

    # dirty document + Cancel: language saved, but no restart
    w.act_rotate_cw.trigger()
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Cancel
    )
    w.language_actions["fr"].trigger()
    assert settings.language == "fr"
    assert asked == [True]
    assert launched == []
    assert w.isVisible()

    # discard changes: relaunch with the open file, window closes
    monkeypatch.setattr(
        dialogs, "confirm_save_changes", lambda p, n: QMessageBox.StandardButton.Discard
    )
    w.language_actions["fr"].trigger()
    assert launched == [restart_command(str(simple_pdf))]
    program, args = launched[0]
    assert program == sys.executable
    assert args[-1] == str(simple_pdf)
    assert not w.isVisible()


def test_selecting_current_language_does_not_prompt(qtbot, settings, monkeypatch) -> None:
    monkeypatch.setattr(dialogs, "ask_restart", lambda parent: pytest.fail("prompted"))
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.language_actions["en"].trigger()
    assert settings.language == "en"
    w.close()
