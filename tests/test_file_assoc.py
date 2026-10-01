"""M5-T4: Windows "Open with" registration (core/file_assoc.py, Settings menu).

Registry tests write ONLY under HKCU\\Software\\PDFEditorTests\\<uuid>, deleted afterwards.
MainWindow tests patch every file_assoc call: no registry access at all.
"""

from __future__ import annotations

import sys
import uuid

import pytest

from pdfeditor.core import file_assoc
from pdfeditor.ui import dialogs
from pdfeditor.ui.main_window import MainWindow

winreg = pytest.importorskip("winreg")

TESTS_KEY = r"Software\PDFEditorTests"
HKCU = winreg.HKEY_CURRENT_USER
EXE = r"C:\Program Files Test\PDFEditor\PDFEditor.exe"


def _delete_tree(path: str) -> None:
    assert path.startswith(TESTS_KEY)  # never anything else
    try:
        with winreg.OpenKey(HKCU, path) as key:
            subs = []
            while True:
                try:
                    subs.append(winreg.EnumKey(key, len(subs)))
                except OSError:
                    break
    except FileNotFoundError:
        return
    for sub in subs:
        _delete_tree(path + "\\" + sub)
    winreg.DeleteKey(HKCU, path)


def _exists(path: str) -> bool:
    try:
        winreg.OpenKey(HKCU, path).Close()
    except FileNotFoundError:
        return False
    return True


def _value(path: str, name: str | None = None):
    with winreg.OpenKey(HKCU, path) as key:
        return winreg.QueryValueEx(key, name)


@pytest.fixture
def notify_calls(monkeypatch) -> list[int]:
    """SHChangeNotify is counted, never run."""
    calls: list[int] = []
    monkeypatch.setattr(file_assoc, "notify_shell", lambda: calls.append(1))
    return calls


@pytest.fixture
def root(notify_calls):
    """A throwaway registry base under HKCU/Software/PDFEditorTests."""
    base = TESTS_KEY + "\\" + uuid.uuid4().hex
    try:
        yield base
    finally:
        _delete_tree(base)
        try:
            winreg.DeleteKey(HKCU, TESTS_KEY)  # only succeeds when empty
        except OSError:
            pass


def _register(root: str, exe_cmd: list[str] | None = None) -> None:
    assert root.startswith(TESTS_KEY + "\\")
    file_assoc.register(exe_cmd=exe_cmd or [EXE], icon=f'"{EXE}",0', root=root)


def test_register_writes_expected_keys(root, notify_calls) -> None:
    _register(root)
    classes = root + r"\Software\Classes"
    command = f'"{EXE}" "%1"'
    app = classes + r"\Applications\PDFEditor.exe"
    assert _value(app, "FriendlyAppName") == ("PDF Editor", winreg.REG_SZ)
    assert _value(app + r"\DefaultIcon")[0] == f'"{EXE}",0'
    assert _value(app + r"\shell\open\command") == (command, winreg.REG_SZ)
    assert _value(app + r"\SupportedTypes", ".pdf") == ("", winreg.REG_SZ)
    progid = classes + r"\PDFEditor.pdf"
    assert _value(progid)[0] == "PDF Document (PDF Editor)"
    assert _value(progid, "FriendlyTypeName")[0] == "PDF Document (PDF Editor)"
    assert _value(progid + r"\DefaultIcon")[0] == f'"{EXE}",0'
    assert _value(progid + r"\shell\open\command")[0] == command
    value, kind = _value(classes + r"\.pdf\OpenWithProgids", "PDFEditor.pdf")
    assert kind == winreg.REG_NONE and not value
    with pytest.raises(FileNotFoundError):  # never the user's default program
        _value(classes + r"\.pdf")
    app_paths = root + r"\Software\Microsoft\Windows\CurrentVersion\App Paths\PDFEditor.exe"
    assert _value(app_paths)[0] == EXE
    assert _value(app_paths, "Path")[0] == r"C:\Program Files Test\PDFEditor"
    caps = root + r"\Software\PDFEditor\Capabilities"
    assert _value(caps, "ApplicationName")[0] == "PDF Editor"
    assert _value(caps, "ApplicationDescription")[0]
    assert _value(caps + r"\FileAssociations", ".pdf")[0] == "PDFEditor.pdf"
    assert _value(root + r"\Software\RegisteredApplications", "PDFEditor")[0] == caps
    assert file_assoc.is_registered(root=root)
    assert file_assoc.registered_command(root=root) == command
    assert notify_calls == [1]


def test_dev_command_quotes_arguments_and_skips_app_paths(root) -> None:
    py = r"C:\My Venv\Scripts\pythonw.exe"
    _register(root, [py, "-m", "pdfeditor"])
    cmd = _value(root + r"\Software\Classes\PDFEditor.pdf\shell\open\command")[0]
    assert cmd == f'"{py}" -m pdfeditor "%1"'
    assert not _exists(root + r"\Software\Microsoft\Windows\CurrentVersion\App Paths\PDFEditor.exe")


def test_unregister_removes_everything_and_is_idempotent(root, notify_calls) -> None:
    _register(root)
    file_assoc.unregister(root=root)
    assert not file_assoc.is_registered(root=root)
    for path in (
        r"\Software\Classes\Applications\PDFEditor.exe",
        r"\Software\Classes\PDFEditor.pdf",
        r"\Software\Classes\.pdf",
        r"\Software\Microsoft\Windows\CurrentVersion\App Paths\PDFEditor.exe",
        r"\Software\PDFEditor",
    ):
        assert not _exists(root + path), path
    with pytest.raises(FileNotFoundError):
        _value(root + r"\Software\RegisteredApplications", "PDFEditor")
    assert notify_calls == [1, 1]
    file_assoc.unregister(root=root)  # nothing left: no error


def test_unregister_keeps_shared_keys_and_other_values(root) -> None:
    classes = root + r"\Software\Classes"
    with winreg.CreateKey(HKCU, classes + r"\.pdf") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, "AcroExch.Document.DC")
    with winreg.CreateKey(HKCU, classes + r"\.pdf\OpenWithProgids") as key:
        winreg.SetValueEx(key, "Other.pdf", 0, winreg.REG_NONE, b"")
    with winreg.CreateKey(HKCU, root + r"\Software\RegisteredApplications") as key:
        winreg.SetValueEx(key, "Other", 0, winreg.REG_SZ, r"Software\Other\Capabilities")
    _register(root)
    assert _value(classes + r"\.pdf")[0] == "AcroExch.Document.DC"  # default untouched
    file_assoc.unregister(root=root)
    assert _value(classes + r"\.pdf")[0] == "AcroExch.Document.DC"
    assert _value(classes + r"\.pdf\OpenWithProgids", "Other.pdf")[1] == winreg.REG_NONE
    with pytest.raises(FileNotFoundError):
        _value(classes + r"\.pdf\OpenWithProgids", "PDFEditor.pdf")
    assert _value(root + r"\Software\RegisteredApplications", "Other")[0]


def test_is_registered_false_on_empty_root(root) -> None:
    assert not file_assoc.is_registered(root=root)
    assert file_assoc.registered_command(root=root) is None


def test_notify_shell_swallows_failures(monkeypatch) -> None:
    import ctypes

    class Boom:
        def __getattr__(self, name):
            raise OSError("no shell")

    monkeypatch.setattr(ctypes, "windll", Boom())
    file_assoc.notify_shell()  # no exception


def test_app_command_dev_and_frozen(monkeypatch, tmp_path) -> None:
    python = tmp_path / "Scripts" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"")
    (tmp_path / "Scripts" / "pythonw.exe").write_bytes(b"")
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", str(python))
    assert file_assoc.app_command() == [
        str(tmp_path / "Scripts" / "pythonw.exe"),
        "-m",
        "pdfeditor",
    ]
    assert file_assoc.app_icon().endswith('app.ico",0')
    exe = str(tmp_path / "PDFEditor.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", exe)
    assert file_assoc.app_command() == [exe]
    assert file_assoc.app_icon() == f'"{exe}",0'
    assert file_assoc.command_line([exe]) == f'"{exe}" "%1"'


def test_unsupported_platform(monkeypatch) -> None:
    monkeypatch.setattr(file_assoc, "is_supported", lambda: False)
    assert not file_assoc.is_registered(root=TESTS_KEY + r"\none")
    with pytest.raises(OSError):
        file_assoc.register(root=TESTS_KEY + r"\none")
    with pytest.raises(OSError):
        file_assoc.unregister(root=TESTS_KEY + r"\none")


# -- MainWindow: every file_assoc call is patched -----------------------------------
class FakeAssoc:
    def __init__(self, monkeypatch) -> None:
        self.command: str | None = None
        self.calls: list[str] = []
        self.error: OSError | None = None
        current = file_assoc.command_line([EXE])
        monkeypatch.setattr(file_assoc, "is_supported", lambda: True)
        monkeypatch.setattr(file_assoc, "app_command", lambda: [EXE])
        monkeypatch.setattr(file_assoc, "registered_command", lambda root="": self.command)
        monkeypatch.setattr(file_assoc, "is_registered", lambda root="": self.command is not None)

        def register(**kwargs) -> None:
            assert not kwargs  # real roots are only ever used by the real app
            self.calls.append("register")
            if self.error:
                raise self.error
            self.command = current

        def unregister(root: str = "") -> None:
            self.calls.append("unregister")
            if self.error:
                raise self.error
            self.command = None

        monkeypatch.setattr(file_assoc, "register", register)
        monkeypatch.setattr(file_assoc, "unregister", unregister)
        monkeypatch.setattr(file_assoc, "notify_shell", lambda: pytest.fail("not patched"))


@pytest.fixture
def assoc(monkeypatch):
    return FakeAssoc(monkeypatch)


@pytest.fixture
def window(qtbot, settings, assoc, monkeypatch):
    warnings: list[tuple] = []
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: warnings.append((a, k)))
    w = MainWindow(settings)
    w.warnings = warnings
    qtbot.addWidget(w)
    return w


def _menu_titles(w: MainWindow) -> list[str]:
    return [a.text() for a in w.menuBar().actions()]


def test_settings_menu_between_view_and_help(window) -> None:
    titles = _menu_titles(window)
    assert titles.index("&Settings") == titles.index("&View") + 1
    assert titles.index("&Help") == titles.index("&Settings") + 1
    acts = window.menu_settings.actions()
    assert acts == [window.act_register_assoc, window.act_unregister_assoc]
    assert window.act_register_assoc.text() == "Register with Windows (Open with)…"


def test_actions_follow_registration_state(window, assoc) -> None:
    window.menu_settings.aboutToShow.emit()
    assert window.act_register_assoc.isEnabled()
    assert not window.act_unregister_assoc.isEnabled()
    assoc.command = file_assoc.command_line([EXE])
    window.menu_settings.aboutToShow.emit()
    assert not window.act_register_assoc.isEnabled()
    assert window.act_unregister_assoc.isEnabled()
    assoc.command = '"D:\\old\\PDFEditor.exe" "%1"'  # app moved: may register again
    window._update_assoc_actions()
    assert window.act_register_assoc.isEnabled()
    assert window.act_unregister_assoc.isEnabled()


def test_actions_disabled_when_unsupported(window, monkeypatch) -> None:
    monkeypatch.setattr(file_assoc, "is_supported", lambda: False)
    window._update_assoc_actions()
    assert not window.act_register_assoc.isEnabled()
    assert not window.act_unregister_assoc.isEnabled()


def test_register_confirmed(window, assoc, monkeypatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(dialogs, "confirm_register", lambda p, cmd: shown.append(cmd) or True)
    window.act_register_assoc.trigger()
    assert shown == [f'"{EXE}" "%1"']
    assert assoc.calls == ["register"]
    assert window.statusBar().currentMessage() == "Registered with Windows"
    assert not window.act_register_assoc.isEnabled()
    assert window.act_unregister_assoc.isEnabled()


def test_register_cancelled_changes_nothing(window, assoc, monkeypatch) -> None:
    monkeypatch.setattr(dialogs, "confirm_register", lambda p, cmd: False)
    assert not window.register_file_assoc()
    assert assoc.calls == []
    assert window.statusBar().currentMessage() == ""
    assert window.warnings == []


def test_unregister(window, assoc) -> None:
    assoc.command = file_assoc.command_line([EXE])
    window.act_unregister_assoc.trigger()
    assert assoc.calls == ["unregister"]
    assert window.statusBar().currentMessage() == "Removed from Windows"
    assert window.act_register_assoc.isEnabled()
    assert not window.act_unregister_assoc.isEnabled()


def test_failure_shows_warning_with_details(window, assoc, monkeypatch) -> None:
    monkeypatch.setattr(dialogs, "confirm_register", lambda p, cmd: True)
    assoc.error = PermissionError(5, "Access is denied")
    assert not window.register_file_assoc()
    assert not window.unregister_file_assoc()
    assert len(window.warnings) == 2
    (args, kwargs) = window.warnings[0]
    assert args[2] == "The registration could not be changed."
    assert "Access is denied" in kwargs["details"]


def test_confirm_register_dialog_shows_command(qtbot, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    seen: list[str] = []

    def fake_exec(box) -> int:
        seen.append(box.text())
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert not dialogs.confirm_register(None, '"C:\\x\\PDFEditor.exe" "%1"')
    assert "Open with" in seen[0] and 'Command: "C:\\x\\PDFEditor.exe" "%1"' in seen[0]
