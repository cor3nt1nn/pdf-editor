"""Tests of the setup program (Inno Setup, dist/PDFEditor-<version>-setup.exe, M8-I4).

Skipped unless ``PDFEDITOR_INSTALLER`` names the built setup:

    powershell -File scripts\\build_exe.ps1 -Installer -Smoke
    # or, after a build:
    $env:PDFEDITOR_INSTALLER = "$PWD\\dist\\PDFEditor-0.2.0-setup.exe"; uv run pytest -m installer

A silent per-user install into a temporary folder (no shortcut, no "Open with" entry),
an upgrade over it and a silent uninstall. The only real registry key touched is the
setup's own ``HKCU\\...\\Uninstall\\{AppId}_is1``, which the uninstall removes (and the
teardown — registered before the setup runs, so a timeout or an error cannot skip it —
should the uninstall fail); the whole module skips when PDF Editor is really installed on
this machine, for this user (HKCU) or for all users (HKLM, both registry views). The
user's data folders (``%APPDATA%\\PDFEditor``, ``%LOCALAPPDATA%\\PDFEditor``) must be left
untouched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pdfeditor import __version__

if sys.platform == "win32":
    import winreg

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import smoke_frozen  # noqa: E402

SETUP = os.environ.get("PDFEDITOR_INSTALLER")
#: installer/pdfeditor.iss AppId (tests/test_installer_script.py pins it too).
APP_ID = "{2F4F77DF-7029-452C-AE27-01CC3FD48311}"
UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
UNINSTALL_KEY = UNINSTALL + "\\" + APP_ID + "_is1"
#: Keys that only core/file_assoc.py may write (never the installer, never these tests).
ASSOCIATION_KEYS = (
    r"Software\Classes\PDFEditor.pdf",
    r"Software\Classes\Applications\PDFEditor.exe",
    r"Software\PDFEditor\Capabilities",
)
#: 37.9 MB at M8 (scripts/build_exe.ps1 $MaxSetupMB).
MAX_SETUP_MB = 45
INSTALL_TIMEOUT = 60
SILENT = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/CURRENTUSER", "/NOICONS", "/NORESTART"]
TASKS = "/MERGETASKS=!desktopicon,!openwith"


def _key_exists(path: str, hive: int | None = None, view: int = 0) -> bool:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER if hive is None else hive, path, 0, winreg.KEY_READ | view
        )
    except FileNotFoundError:
        return False
    winreg.CloseKey(key)
    return True


def _installed() -> bool:
    """PDF Editor is really installed: its Uninstall key (or an entry named "PDF Editor")
    exists for this user or for all users (HKLM, 64- and 32-bit views)."""
    if sys.platform != "win32":
        return False
    places = [(winreg.HKEY_CURRENT_USER, 0)] + [
        (winreg.HKEY_LOCAL_MACHINE, view)
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)
    ]
    for hive, view in places:
        if _key_exists(UNINSTALL_KEY, hive, view) or _our_uninstall_keys(hive, view):
            return True
    return False


def _values(path: str, hive: int | None = None, view: int = 0) -> dict[str, object]:
    out: dict[str, object] = {}
    hive = winreg.HKEY_CURRENT_USER if hive is None else hive
    with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as key:
        index = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, index)
            except OSError:
                return out
            out[name] = value
            index += 1


def _our_uninstall_keys(hive: int | None = None, view: int = 0) -> list[str]:
    """Uninstall entries of PDF Editor (by AppId or display name) in ``hive``."""
    names: list[str] = []
    hive = winreg.HKEY_CURRENT_USER if hive is None else hive
    try:
        key = winreg.OpenKey(hive, UNINSTALL, 0, winreg.KEY_READ | view)
    except FileNotFoundError:
        return names
    with key:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(key, index)
            except OSError:
                break
            index += 1
            if APP_ID.lower() in name.lower():
                names.append(name)
                continue
            try:
                if _values(UNINSTALL + "\\" + name, hive, view).get("DisplayName") == "PDF Editor":
                    names.append(name)
            except OSError:
                pass
    return names


# After the helpers: the skip condition calls them at import time.
pytestmark = [
    pytest.mark.installer,
    pytest.mark.skipif(not SETUP, reason="PDFEDITOR_INSTALLER is not set (needs a build)"),
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
    pytest.mark.skipif(
        bool(SETUP) and _installed(),
        reason="PDF Editor is installed on this machine (its Uninstall key exists)",
    ),
]


def _registry_snapshot() -> dict[str, object]:
    """Association keys (existence and values) and our shortcuts, to compare after."""
    snap: dict[str, object] = {}
    for path in ASSOCIATION_KEYS:
        snap[path] = _values(path) if _key_exists(path) else None
    open_with = r"Software\Classes\.pdf\OpenWithProgids"
    snap[open_with] = "PDFEditor.pdf" in _values(open_with) if _key_exists(open_with) else None
    programs = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
    for link in (programs / "PDF Editor.lnk", desktop / "PDF Editor.lnk"):
        snap[str(link)] = link.exists()
    return snap


def _user_data_snapshot() -> dict[str, dict[str, int] | None]:
    """Every entry (relative path → mtime) of the user's real data folders."""
    snap: dict[str, dict[str, int] | None] = {}
    for env in ("APPDATA", "LOCALAPPDATA"):
        root = Path(os.environ[env]) / "PDFEditor"
        if not root.exists():
            snap[str(root)] = None
            continue
        entries = {".": root.stat().st_mtime_ns}
        for path in root.rglob("*"):
            entries[str(path.relative_to(root))] = path.stat().st_mtime_ns
        snap[str(root)] = entries
    return snap


def _version_strings(path: Path) -> dict[str, str]:
    """String version resource of ``path`` in its first listed translation, stripped
    (Inno Setup does not use smoke_frozen's 040904B0 block)."""
    import ctypes
    from ctypes import wintypes

    version = ctypes.WinDLL("version")
    size = version.GetFileVersionInfoSizeW(str(path), None)
    buffer = ctypes.create_string_buffer(size)
    assert version.GetFileVersionInfoW(str(path), 0, size, buffer)
    pointer = ctypes.c_void_p()
    length = wintypes.UINT()
    query = r"\VarFileInfo\Translation"
    assert version.VerQueryValueW(buffer, query, ctypes.byref(pointer), ctypes.byref(length))
    words = (wintypes.WORD * 2).from_address(pointer.value)
    block = f"{words[0]:04X}{words[1]:04X}"
    result = {}
    for key in ("ProductVersion", "ProductName", "FileDescription"):
        query = rf"\StringFileInfo\{block}\{key}"
        if version.VerQueryValueW(buffer, query, ctypes.byref(pointer), ctypes.byref(length)):
            result[key] = ctypes.wstring_at(pointer.value, length.value).rstrip(chr(0)).strip()
    return result


def _fixed_file_version(path: Path) -> str:
    """The binary file version (VS_FIXEDFILEINFO) of ``path`` as "a.b.c.d"."""
    import ctypes
    from ctypes import wintypes

    version = ctypes.WinDLL("version")
    size = version.GetFileVersionInfoSizeW(str(path), None)
    buffer = ctypes.create_string_buffer(size)
    assert version.GetFileVersionInfoW(str(path), 0, size, buffer)
    pointer = ctypes.c_void_p()
    length = wintypes.UINT()
    assert version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length))
    fixed = (wintypes.DWORD * 13).from_address(pointer.value)
    ms, ls = fixed[2], fixed[3]  # dwFileVersionMS, dwFileVersionLS
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def _numeric_version(text: str) -> str:
    """scripts/build_exe.ps1's FileVersion: leading numbers, padded to four parts."""
    import re

    parts = re.match(r"\d+(\.\d+)*", text).group(0).split(".")[:4]
    return ".".join(parts + ["0"] * (4 - len(parts)))


def _run_setup(setup: Path, target: Path, log: Path) -> tuple[int, float]:
    start = time.monotonic()
    done = subprocess.run(
        [str(setup), *SILENT, TASKS, f"/DIR={target}", f"/LOG={log}"],
        timeout=INSTALL_TIMEOUT * 2,
        check=False,
    )
    return done.returncode, time.monotonic() - start


def _wait_until(condition, timeout: float = 60) -> bool:  # noqa: ANN001 - a callable
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.25)
    return condition()


def _uninstall(target: Path) -> int:
    """Silent uninstall; unins000.exe hands over to a copy of itself in %TEMP% and returns
    at once, so wait for the folder and the Uninstall key to go."""
    uninstaller = target / "unins000.exe"
    try:
        code = subprocess.run(
            [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            timeout=INSTALL_TIMEOUT,
            check=False,
        ).returncode
    except subprocess.TimeoutExpired:  # killed; the caller checks what is left
        return -1
    _wait_until(lambda: not target.exists() and not _key_exists(UNINSTALL_KEY))
    return code


class Install:
    """State shared by the ordered tests of this module."""

    def __init__(self, setup: Path, work: Path) -> None:
        self.setup = setup
        self.work = work
        self.target = work / "Program Files" / "PDF Editor"  # spaces in the path
        self.registry_before = _registry_snapshot()
        self.user_data_before = _user_data_snapshot()
        self.first: tuple[int, float] | None = None
        self.upgrade: tuple[int, float] | None = None
        self.uninstall_code: int | None = None


def _cleanup(state: Install) -> None:
    """Never leave the setup's Uninstall key or the folder behind (also after a timeout or
    an error in the setup itself). The key is deleted only when it names our temporary
    folder: the module skipped if PDF Editor was installed before."""
    try:
        if (state.target / "unins000.exe").is_file():
            _uninstall(state.target)
    finally:
        if _key_exists(UNINSTALL_KEY):
            try:
                location = str(_values(UNINSTALL_KEY).get("InstallLocation", ""))
            except OSError:
                location = ""
            if not location or Path(location).is_relative_to(state.work):
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        shutil.rmtree(state.target, ignore_errors=True)


@pytest.fixture(scope="module")
def install(request, tmp_path_factory) -> Install:
    setup = Path(SETUP or "")
    assert setup.is_file(), f"{setup} not found"
    state = Install(setup, tmp_path_factory.mktemp("installer"))
    request.addfinalizer(lambda: _cleanup(state))  # before anything can fail
    state.first = _run_setup(setup, state.target, state.work / "install.log")
    return state


def test_setup_program(install) -> None:
    assert install.setup.stat().st_size <= MAX_SETUP_MB * 1024 * 1024
    assert install.setup.name == f"PDFEditor-{__version__}-setup.exe"
    version = _version_strings(install.setup)
    assert version["ProductVersion"] == __version__  # Inno Setup pads it with spaces
    assert version["ProductName"] == "PDF Editor"
    assert version["FileDescription"] == "PDF Editor Setup"
    assert _fixed_file_version(install.setup) == _numeric_version(__version__)


def test_silent_install(install) -> None:
    code, seconds = install.first
    assert code == 0, (install.work / "install.log").read_text(encoding="utf-8", errors="replace")
    assert seconds < INSTALL_TIMEOUT


def test_installed_files(install) -> None:
    target = install.target
    for name in ("PDFEditor.exe", "unins000.exe", "unins000.dat", "LICENSE", "README.md"):
        assert (target / name).is_file(), name
    assert (target / "THIRD_PARTY_LICENSES.md").is_file()
    assert sorted(p.name for p in (target / "licenses").glob("*.txt"))
    internal = target / "_internal"
    assert (internal / "pdfeditor" / "i18n" / "pdfeditor_fr.qm").is_file()
    tessdata = internal / "pdfeditor" / "resources" / "tessdata"
    assert {"fra.traineddata", "eng.traineddata"} <= {p.name for p in tessdata.iterdir()}
    source = smoke_frozen.DEFAULT_EXE.parent
    if source.is_dir():  # the folder the setup was built from: same file list
        expected = {str(p.relative_to(source)) for p in source.rglob("*") if p.is_file()}
        actual = {str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()}
        assert expected <= actual
        assert actual - expected == {"unins000.exe", "unins000.dat"}


def test_uninstall_key(install) -> None:
    assert _our_uninstall_keys() == [APP_ID + "_is1"]
    values = _values(UNINSTALL_KEY)
    assert values["DisplayName"] == "PDF Editor"
    assert values["DisplayVersion"] == __version__
    assert values["Publisher"] == "PDF Editor contributors"
    assert Path(str(values["InstallLocation"])) == install.target
    assert Path(str(values["DisplayIcon"]).split(",")[0]) == install.target / "PDFEditor.exe"
    assert "unins000.exe" in str(values["UninstallString"])
    assert "/SILENT" in str(values["QuietUninstallString"])


def test_self_check_from_installed_exe(install) -> None:
    out = install.work / "self-check" / "out"
    code, report = smoke_frozen.run_self_check(install.target / "PDFEditor.exe", out)
    failed = [c for c in report.get("checks", []) if not c["ok"]]
    assert failed == []
    assert code == 0
    assert report["frozen"] is True
    assert report["version"] == __version__
    checks = {c["name"]: c["detail"] for c in report["checks"]}
    assert str(install.target) in checks["ocr"]["tessdata"]
    assert checks["ocr_worker"]["recall"] >= 0.8


def test_no_association_or_shortcut(install) -> None:
    """The "Open with" task was off and /NOICONS given: nothing outside the folder."""
    assert _registry_snapshot() == install.registry_before


def test_upgrade(install) -> None:
    """Installing again over it (an upgrade) keeps one Uninstall entry and replaces
    ``_internal`` entirely."""
    stale = install.target / "_internal" / "stale-from-an-old-build.dll"
    stale.write_bytes(b"old")
    install.upgrade = _run_setup(install.setup, install.target, install.work / "upgrade.log")
    code, seconds = install.upgrade
    assert code == 0, (install.work / "upgrade.log").read_text(encoding="utf-8", errors="replace")
    assert seconds < INSTALL_TIMEOUT
    assert not stale.exists()
    assert (install.target / "PDFEditor.exe").is_file()
    assert _our_uninstall_keys() == [APP_ID + "_is1"]
    assert _values(UNINSTALL_KEY)["DisplayVersion"] == __version__
    assert _registry_snapshot() == install.registry_before


def test_silent_uninstall(install) -> None:
    install.uninstall_code = _uninstall(install.target)
    assert install.uninstall_code == 0
    assert not install.target.exists()
    assert not _key_exists(UNINSTALL_KEY)
    assert _our_uninstall_keys() == []
    # --unregister-file-type ran and found nothing of its own to remove.
    assert _registry_snapshot() == install.registry_before


def test_user_data_untouched(install) -> None:
    """Install, self-check (with directory overrides), upgrade and a silent uninstall
    never touch the user's real settings, signatures or logs."""
    assert install.uninstall_code is not None, "runs after the uninstall test"
    assert _user_data_snapshot() == install.user_data_before
