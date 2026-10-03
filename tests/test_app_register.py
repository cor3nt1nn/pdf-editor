"""The hidden ``--register-file-type`` / ``--unregister-file-type`` flags run by the
installer (M8-I1). ``file_assoc`` is always patched: nothing real is registered."""

from __future__ import annotations

import subprocess
import sys

import pytest

import pdfeditor.app as app_module
from pdfeditor.core import file_assoc


@pytest.fixture
def calls(monkeypatch) -> list[str]:
    """Patched ``file_assoc.register``/``unregister`` recording their calls; the Qt
    application class is replaced by one that fails if the flags create it."""
    done: list[str] = []
    monkeypatch.setattr(file_assoc, "register", lambda **_: done.append("register"))
    monkeypatch.setattr(file_assoc, "unregister", lambda *_, **__: done.append("unregister"))
    monkeypatch.setattr(file_assoc, "registered_command", lambda *_, **__: None)

    def no_app(*_args, **_kwargs):
        raise AssertionError("the registration flags must not create a QApplication")

    monkeypatch.setattr(app_module, "QApplication", no_app)
    monkeypatch.setattr(app_module, "parse_args", no_app)
    return done


def test_flag_constants() -> None:
    assert app_module.REGISTER_FLAG == "--register-file-type"
    assert app_module.UNREGISTER_FLAG == "--unregister-file-type"


def test_register_flag(calls) -> None:
    assert app_module.main(["PDFEditor.exe", app_module.REGISTER_FLAG]) == 0
    assert calls == ["register"]


def test_unregister_flag(calls) -> None:
    assert app_module.main(["PDFEditor.exe", app_module.UNREGISTER_FLAG]) == 0
    assert calls == ["unregister"]


def test_unregister_removes_our_own_registration(monkeypatch, calls) -> None:
    ours = file_assoc.command_line(file_assoc.app_command())
    monkeypatch.setattr(file_assoc, "registered_command", lambda *_, **__: ours.upper())
    assert app_module.main(["PDFEditor.exe", app_module.UNREGISTER_FLAG]) == 0
    assert calls == ["unregister"]


def test_unregister_keeps_another_copys_registration(monkeypatch, calls) -> None:
    """Uninstalling the setup version leaves the portable copy's "Open with" entry."""
    other = r'"D:\Portable\PDFEditor\PDFEditor.exe" "%1"'
    monkeypatch.setattr(file_assoc, "registered_command", lambda *_, **__: other)
    assert app_module.main(["PDFEditor.exe", app_module.UNREGISTER_FLAG]) == 0
    assert calls == []


@pytest.mark.parametrize("flag", [app_module.REGISTER_FLAG, app_module.UNREGISTER_FLAG])
def test_registry_error_exits_2(monkeypatch, calls, flag, caplog) -> None:
    def fail(*_args, **_kwargs):
        raise OSError("access denied")

    monkeypatch.setattr(file_assoc, "register", fail)
    monkeypatch.setattr(file_assoc, "unregister", fail)
    assert app_module.main(["PDFEditor.exe", flag]) == app_module.EXIT_REGISTRY_ERROR == 2
    assert "access denied" in caplog.text


@pytest.mark.parametrize("flag", [app_module.REGISTER_FLAG, app_module.UNREGISTER_FLAG])
def test_unexpected_error_exits_3(monkeypatch, calls, flag) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(file_assoc, "register", boom)
    monkeypatch.setattr(file_assoc, "unregister", boom)
    assert app_module.main(["PDFEditor.exe", flag]) == app_module.EXIT_REGISTER_FAILED == 3


def test_flag_only_as_first_argument(monkeypatch, calls) -> None:
    """A PDF that happens to be named like the flag is not taken for it."""
    seen = []

    def parse(argv):
        seen.append(argv)
        raise SystemExit(0)

    monkeypatch.setattr(app_module, "parse_args", parse)
    with pytest.raises(SystemExit):
        app_module.main(["PDFEditor.exe", "--lang", "fr", app_module.REGISTER_FLAG])
    assert calls == []
    assert seen == [["--lang", "fr", app_module.REGISTER_FLAG]]


def test_subprocess_creates_no_qt_application() -> None:
    """In a fresh process: the exit codes come back and no Qt application (hence no
    window) exists afterwards."""
    code = (
        "import sys\n"
        "from pdfeditor.core import file_assoc\n"
        "file_assoc.register = lambda **k: print('registered')\n"
        "file_assoc.unregister = lambda *a, **k: (_ for _ in ()).throw(OSError('no'))\n"
        "file_assoc.registered_command = lambda *a, **k: None\n"
        "import pdfeditor.app as app\n"
        "from PySide6.QtCore import QCoreApplication\n"
        "ok = app.main(['x', app.REGISTER_FLAG])\n"
        "bad = app.main(['x', app.UNREGISTER_FLAG])\n"
        "print(ok, bad, QCoreApplication.instance() is None)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["registered", "0", "2", "True"]
