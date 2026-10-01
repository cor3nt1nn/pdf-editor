"""Windows "Open with" registration for PDF files, per user (HKCU, no admin rights).

Written keys (``<base>`` is ``root``, empty for the real HKCU locations)::

    <base>\\Software\\Classes\\Applications\\PDFEditor.exe
        FriendlyAppName, DefaultIcon, shell\\open\\command, SupportedTypes\\.pdf
    <base>\\Software\\Classes\\PDFEditor.pdf               (ProgID)
        (default), FriendlyTypeName, DefaultIcon, shell\\open\\command
    <base>\\Software\\Classes\\.pdf\\OpenWithProgids        PDFEditor.pdf = REG_NONE
    <base>\\Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\PDFEditor.exe
        (frozen build only)
    <base>\\Software\\PDFEditor\\Capabilities               (+ FileAssociations)
    <base>\\Software\\RegisteredApplications                PDFEditor = ...\\Capabilities

The default value of ``.pdf`` (the user's default PDF program) is never touched.
Tests pass ``root=r"Software\\PDFEditorTests\\<uuid>"`` so nothing real is modified.
No Qt here; every function except :func:`is_supported` raises ``OSError`` on failure.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    import winreg
else:  # pragma: no cover - Windows only
    winreg = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

PROGID = "PDFEditor.pdf"
APP_KEY = "PDFEditor.exe"
REGISTERED_NAME = "PDFEditor"
FRIENDLY_NAME = "PDF Editor"
TYPE_NAME = "PDF Document (PDF Editor)"
DESCRIPTION = "Free and open-source PDF editor: fill forms, add text and stamps, sign."

CLASSES = r"Software\Classes"
APP_PATHS = r"Software\Microsoft\Windows\CurrentVersion\App Paths"
VENDOR = r"Software\PDFEditor"
REGISTERED_APPS = r"Software\RegisteredApplications"

SHCNE_ASSOCCHANGED = 0x08000000
SHCNF_IDLIST_FLUSH = 0x1000  # SHCNF_IDLIST (0) | SHCNF_FLUSH


def is_supported() -> bool:
    return sys.platform == "win32"


def _require() -> None:
    if not is_supported():
        raise OSError("file associations are only supported on Windows")


def _path(root: str, base: str, *parts: str) -> str:
    return "\\".join(p for p in (root.strip("\\"), base, *parts) if p)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_command() -> list[str]:
    """Command that starts the app: the exe when frozen, else ``pythonw -m pdfeditor``."""
    if is_frozen():
        return [sys.executable]
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return [str(pythonw if pythonw.is_file() else exe), "-m", "pdfeditor"]


def app_icon() -> str:
    """``DefaultIcon`` value: the exe's first icon when frozen, else resources/app.ico."""
    if is_frozen():
        return f'"{sys.executable}",0'
    ico = Path(__file__).resolve().parent.parent / "resources" / "app.ico"
    return f'"{ico}",0'


def command_line(exe_cmd: list[str]) -> str:
    """Shell ``open`` command: quoted executable, arguments, then ``"%1"``."""
    exe, *args = exe_cmd
    quoted = [f'"{exe}"'] + [f'"{a}"' if (" " in a or not a) else a for a in args]
    return " ".join([*quoted, '"%1"'])


def _set(path: str, values: dict[str | None, str]) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def register(*, exe_cmd: list[str] | None = None, icon: str | None = None, root: str = "") -> None:
    """Add PDF Editor to the "Open with" list of .pdf files (current user only)."""
    _require()
    exe_cmd = exe_cmd or app_command()
    icon = icon or app_icon()
    command = command_line(exe_cmd)
    classes = _path(root, CLASSES)
    app = _path(classes, "Applications", APP_KEY)
    _set(app, {"FriendlyAppName": FRIENDLY_NAME})
    _set(app + r"\DefaultIcon", {None: icon})
    _set(app + r"\shell\open\command", {None: command})
    _set(app + r"\SupportedTypes", {".pdf": ""})
    progid = _path(classes, PROGID)
    _set(progid, {None: TYPE_NAME, "FriendlyTypeName": TYPE_NAME})
    _set(progid + r"\DefaultIcon", {None: icon})
    _set(progid + r"\shell\open\command", {None: command})
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _path(classes, ".pdf", "OpenWithProgids")) as k:
        winreg.SetValueEx(k, PROGID, 0, winreg.REG_NONE, b"")
    if len(exe_cmd) == 1:
        # App Paths maps "PDFEditor.exe" to an executable: meaningless for pythonw -m.
        exe = exe_cmd[0]
        _set(_path(root, APP_PATHS, APP_KEY), {None: exe, "Path": os.path.dirname(exe)})
    capabilities = _path(root, VENDOR, "Capabilities")
    _set(capabilities, {"ApplicationName": FRIENDLY_NAME, "ApplicationDescription": DESCRIPTION})
    _set(capabilities + r"\FileAssociations", {".pdf": PROGID})
    _set(_path(root, REGISTERED_APPS), {REGISTERED_NAME: capabilities})
    log.info("registered for .pdf (Open with): %s", command)
    notify_shell()


def _delete_tree(path: str) -> None:
    """Delete ``path`` and its subkeys; a missing key is not an error."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            subkeys = []
            while True:
                try:
                    subkeys.append(winreg.EnumKey(key, len(subkeys)))
                except OSError:
                    break
        for sub in subkeys:
            _delete_tree(path + "\\" + sub)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass


def _delete_value(path: str, name: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def _delete_if_empty(path: str) -> None:
    """Delete ``path`` only if it has no subkey and no value (i.e. nobody else uses it)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            subkeys, values, _ = winreg.QueryInfoKey(key)
        if subkeys == 0 and values == 0:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass


def unregister(root: str = "") -> None:
    """Remove only what :func:`register` created; shared parent keys are kept."""
    _require()
    classes = _path(root, CLASSES)
    _delete_tree(_path(classes, "Applications", APP_KEY))
    _delete_tree(_path(classes, PROGID))
    open_with = _path(classes, ".pdf", "OpenWithProgids")
    _delete_value(open_with, PROGID)
    _delete_if_empty(open_with)
    _delete_if_empty(_path(classes, ".pdf"))
    _delete_tree(_path(root, APP_PATHS, APP_KEY))
    _delete_tree(_path(root, VENDOR, "Capabilities"))
    _delete_if_empty(_path(root, VENDOR))
    _delete_value(_path(root, REGISTERED_APPS), REGISTERED_NAME)
    log.info("unregistered from .pdf (Open with)")
    notify_shell()


def registered_command(root: str = "") -> str | None:
    """The registered ``open`` command of our ProgID, or None when not registered."""
    if not is_supported():
        return None
    path = _path(root, CLASSES, PROGID, "shell", "open", "command")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _ = winreg.QueryValueEx(key, None)
    except FileNotFoundError:
        return None
    return str(value)


def is_registered(root: str = "") -> bool:
    return registered_command(root) is not None


def notify_shell() -> None:
    """Tell Explorer that associations changed (SHCNE_ASSOCCHANGED); failures are ignored."""
    try:
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST_FLUSH, None, None)
    except Exception:  # noqa: BLE001 - cosmetic refresh only
        log.warning("SHChangeNotify failed", exc_info=True)
