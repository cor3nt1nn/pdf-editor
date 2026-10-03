"""Static checks of the Inno Setup script installer/pdfeditor.iss (M8-I2): no compiler
needed. The compiled setup is tested by tests/test_installer.py (marker ``installer``)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pdfeditor import app

ISS = Path(__file__).resolve().parent.parent / "installer" / "pdfeditor.iss"
BUILD_EXE = ISS.parent.parent / "scripts" / "build_exe.ps1"
#: The installation's identity for upgrades and the uninstaller: never change it.
APP_ID = "{2F4F77DF-7029-452C-AE27-01CC3FD48311}"

FR_MESSAGES = {
    "OpenWithTask": (
        "Ajouter PDF Editor à la liste « Ouvrir avec » des fichiers PDF (ce compte uniquement)"
    ),
    "DeleteUserData": (
        "Supprimer aussi vos paramètres, signatures enregistrées et fichiers journaux de "
        "PDF Editor ?%n%n%1%n%2"
    ),
    "RegisterFailed": (
        "PDF Editor n’a pas pu être ajouté à la liste « Ouvrir avec ». "
        "Vous pourrez le faire plus tard depuis Paramètres."
    ),
    "DeleteFailed": (
        "Certains fichiers de %1 n’ont pas pu être supprimés (PDF Editor est-il encore ouvert ?). "
        "Vous pouvez supprimer ce dossier vous-même."
    ),
    "UserDataKept": (
        "PDF Editor a été désinstallé pour tous les utilisateurs. Les paramètres, signatures "
        "enregistrées et fichiers journaux de chaque utilisateur sont conservés dans son "
        r"profil, dossiers AppData\Roaming\PDFEditor et AppData\Local\PDFEditor ; "
        "chacun peut les supprimer."
    ),
}
EN_MESSAGES = {
    "OpenWithTask": "Add PDF Editor to the “Open with” list of PDF files (this account only)",
    "DeleteUserData": (
        "Also delete your PDF Editor settings, saved signatures and log files?%n%n%1%n%2"
    ),
    "RegisterFailed": (
        "PDF Editor could not be added to the “Open with” list. You can do it later from Settings."
    ),
    "DeleteFailed": (
        "Some files in %1 could not be deleted (is PDF Editor still open?). "
        "You can delete that folder yourself."
    ),
    "UserDataKept": (
        "PDF Editor was removed for all users. Each user’s settings, saved signatures and log "
        r"files are kept in that user’s profile, in the AppData\Roaming\PDFEditor and "
        r"AppData\Local\PDFEditor folders; each user can delete them."
    ),
}


@pytest.fixture(scope="module")
def text() -> str:
    raw = ISS.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "Inno Setup reads UTF-8 scripts with a BOM"
    return raw.decode("utf-8-sig")


@pytest.fixture(scope="module")
def sections(text) -> dict[str, list[str]]:
    """Section name → its non-comment, non-blank lines (preprocessor lines kept)."""
    out: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        match = re.fullmatch(r"\[(\w+)\]", stripped)
        if match:
            current = match.group(1)
            out.setdefault(current, [])
        elif stripped and not stripped.startswith(";") and current:
            out[current].append(stripped)
    return out


@pytest.fixture(scope="module")
def setup(sections) -> dict[str, str]:
    return dict(line.split("=", 1) for line in sections["Setup"] if "=" in line and line[0] != "#")


def _defines(text: str) -> dict[str, str]:
    return dict(re.findall(r'^#define (\w+) "([^"]*)"', text, re.MULTILINE))


def test_identity(setup) -> None:
    assert setup["AppId"] == "{" + APP_ID  # "{{" escapes the brace in Inno Setup
    assert setup["AppName"] == "{#AppName}"
    assert setup["AppVersion"] == "{#AppVersion}"
    assert setup["UninstallDisplayName"] == "{#AppName}"
    assert setup["AppPublisher"] == "PDF Editor contributors"


def test_defines_match_the_app(text) -> None:
    defines = _defines(text)
    assert defines["AppName"] == "PDF Editor"
    assert defines["AppExeName"] == "PDFEditor.exe"
    assert defines["RegisterFlag"] == app.REGISTER_FLAG
    assert defines["UnregisterFlag"] == app.UNREGISTER_FLAG
    assert "#error" in text.split("#ifndef AppVersion", 1)[1].split("#endif", 1)[0]


def test_numeric_file_version(setup, text) -> None:
    """The binary file version must be n.n.n.n: build_exe.ps1 passes FileVersion, derived
    from __version__; the text versions keep the full __version__."""
    assert setup["VersionInfoVersion"] == "{#FileVersion}"
    assert setup["VersionInfoTextVersion"] == "{#AppVersion}"
    assert setup["VersionInfoProductVersion"] == "{#AppVersion}"
    assert "#error" in text.split("#ifndef FileVersion", 1)[1].split("#endif", 1)[0]
    build = BUILD_EXE.read_text(encoding="utf-8")
    assert '"-dFileVersion=$FileVersion"' in build


def _derivation() -> str:
    """build_exe.ps1's FileVersion derivation (from the regex test to the join)."""
    build = BUILD_EXE.read_text(encoding="utf-8")
    start = build.index("if ($Version -notmatch")
    end = build.index("\n", build.index("$FileVersion = $FileParts -join"))
    return build[start:end]


@pytest.mark.skipif(shutil.which("powershell") is None, reason="needs Windows PowerShell")
@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.1.0", "0.1.0.0"), ("0.2.0rc1", "0.2.0.0"), ("1", "1.0.0.0"), ("1.2.3.4.5", "1.2.3.4")],
)
def test_file_version_derivation(version, expected) -> None:
    script = f'$Version = "{version}"\n{_derivation()}\nWrite-Output $FileVersion'
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == expected


def test_current_version_has_a_file_version() -> None:
    from pdfeditor import __version__

    assert re.match(r"\d+(\.\d+)*", __version__)


def test_per_user_x64_install(setup, sections) -> None:
    assert setup["DefaultDirName"] == r"{autopf}\PDFEditor"
    assert setup["PrivilegesRequired"] == "lowest"
    assert setup["PrivilegesRequiredOverridesAllowed"] == "dialog"
    assert setup["SetupArchitecture"] == "x64"
    assert setup["ArchitecturesAllowed"] == "x64compatible"
    assert setup["ArchitecturesInstallIn64BitMode"] == "x64compatible"
    assert setup["DisableProgramGroupPage"] == "yes"
    assert setup["CloseApplications"] == "yes"
    assert setup["OutputBaseFilename"] == "PDFEditor-{#AppVersion}-setup"
    assert setup["Compression"] == "lzma2/ultra64"
    assert setup["SolidCompression"] == "yes"
    assert setup["VersionInfoProductTextVersion"] == "{#AppVersion}"


def test_languages(sections) -> None:
    langs = sections["Languages"]
    assert 'Name: "en"; MessagesFile: "compiler:Default.isl"' in langs
    assert 'Name: "fr"; MessagesFile: "compiler:Languages\\French.isl"' in langs
    assert len(langs) == 2


def test_custom_messages(sections) -> None:
    messages = dict(line.split("=", 1) for line in sections["CustomMessages"])
    for key, value in EN_MESSAGES.items():
        assert messages[f"en.{key}"] == value
    for key, value in FR_MESSAGES.items():
        assert messages[f"fr.{key}"] == value
    assert len(messages) == 2 * len(EN_MESSAGES)
    assert EN_MESSAGES.keys() == FR_MESSAGES.keys()


def test_tasks_unchecked(sections) -> None:
    tasks = sections["Tasks"]
    assert len(tasks) == 2
    assert all("Flags: unchecked" in t for t in tasks)
    (open_with,) = [t for t in tasks if 'Name: "openwith"' in t]
    assert 'Description: "{cm:OpenWithTask}"' in open_with
    # All users (elevated): the uninstaller could not unregister the original user.
    assert open_with.endswith("Check: not IsAdminInstallMode")
    assert any('Name: "desktopicon"' in t for t in tasks)


def test_files_and_cleanup(sections) -> None:
    (files,) = sections["Files"]
    assert files.startswith('Source: "{#SourceDir}\\*"; DestDir: "{app}"')
    assert "recursesubdirs" in files and "createallsubdirs" in files
    internal = 'Type: filesandordirs; Name: "{app}\\_internal"'
    assert sections["InstallDelete"] == [internal]
    assert sections["UninstallDelete"] == [internal]


def test_icons(sections) -> None:
    start, desktop = sections["Icons"]
    assert start.startswith('Name: "{autoprograms}\\{#AppName}"')
    assert "Check: WantStartMenuIcon" in start
    assert desktop.endswith("Tasks: desktopicon")


def test_no_registry_section(sections) -> None:
    """Association keys are written only by core/file_assoc.py (through the flags)."""
    assert "Registry" not in sections
    assert "Software\\Classes" not in "\n".join(sum(sections.values(), []))


def test_flags_used(sections, text) -> None:
    assert "UninstallRun" not in sections  # unregistered from [Code], to log the result
    code = "\n".join(sections["Code"])
    assert "'{#RegisterFlag}'" in code
    step = code.split("procedure CurUninstallStepChanged", 1)[1]
    unregister = step.split("if CurUninstallStep = usUninstall then", 1)[1].split("end;", 1)[0]
    assert "'{#UnregisterFlag}'" in unregister and "SW_HIDE" in unregister
    assert "Log(Format('{#UnregisterFlag} exit code: %d (%s)'" in unregister
    assert "Log(Format('{#RegisterFlag} exit code: %d (%s)'" in code
    defines = _defines(text)
    assert defines["ExitRegistryError"] == str(app.EXIT_REGISTRY_ERROR)
    assert defines["ExitRegisterFailed"] == str(app.EXIT_REGISTER_FAILED)
    assert "{#ExitRegistryError}: Result :=" in code
    assert "{#ExitRegisterFailed}: Result :=" in code
    assert "WizardIsTaskSelected('openwith')" in code
    assert "ExecAsOriginalUser" in code
    assert "CustomMessage('RegisterFailed')" in code
    (run,) = sections["Run"]
    assert "postinstall skipifsilent" in run  # only the "Launch" box


def test_user_data_prompt(sections) -> None:
    code = "\n".join(sections["Code"])
    assert "usPostUninstall" in code and "not UninstallSilent" in code
    assert "MB_DEFBUTTON2" in code  # default answer: No
    assert "{userappdata}\\PDFEditor" in code and "{localappdata}\\PDFEditor" in code
    assert code.count("DelTree(") == 1  # in DeleteUserFolder, called for both folders
    assert code.count("DeleteUserFolder(Roaming)") == code.count("DeleteUserFolder(Local)") == 1
    helper = code.split("procedure DeleteUserFolder", 1)[1].split("end;\nend;", 1)[0]
    assert "not DelTree(Dir, True, True, True)" in helper
    assert "Log(" in helper and "CustomMessage('DeleteFailed')" in helper


def test_app_mutex(setup, text) -> None:
    """Setup and the uninstaller ask to close a running PDF Editor (the app owns it)."""
    assert setup["AppMutex"] == "{#AppMutexName}"
    assert _defines(text)["AppMutexName"] == app.INSTANCE_MUTEX
    assert "{" not in app.INSTANCE_MUTEX  # a brace would need escaping in [Setup]


def test_all_users_uninstall_deletes_no_user_data(sections) -> None:
    """Elevated, {userappdata}/{localappdata} are the administrator's folders: an
    all-users uninstall deletes nothing and says where each user's data lives."""
    code = "\n".join(sections["Code"])
    step = code.split("procedure CurUninstallStepChanged", 1)[1]
    admin = step.index("if IsAdminInstallMode then")
    assert admin < step.index("ExpandConstant('{userappdata}")
    branch = step[admin : step.index("Exit;", admin)]
    assert "CustomMessage('UserDataKept')" in branch and "SuppressibleMsgBox" in branch
    assert "DelTree" not in branch


FETCH = BUILD_EXE.parent / "fetch_innosetup.ps1"
PINNED = BUILD_EXE.parent.parent / "build" / "tools" / "innosetup" / "tools"


def _fetch(roots: list[Path]) -> tuple[str, str]:
    """Run fetch_innosetup.ps1 searching ``roots`` instead of Program Files (Windows
    resets %ProgramFiles% for every process: it cannot be redirected)."""
    env = dict(os.environ)
    env.pop("ISCC", None)
    command = f"& '{FETCH}' -SearchRoots " + ",".join(f"'{r}'" for r in roots)
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip().splitlines()[-1], done.stdout


@pytest.mark.skipif(
    shutil.which("powershell") is None or not (PINNED / "ISCC.exe").is_file(),
    reason="needs Windows PowerShell and the pinned Inno Setup in build/tools",
)
def test_fetch_skips_an_old_compiler(tmp_path) -> None:
    """Installer review 8: an installed compiler older than 7.1 is not used (its file
    version says so); a 7.1 one is, even without a version resource (the portable package
    has none: the compiler's banner tells)."""
    import sys

    roots = [tmp_path / "ProgramFiles", tmp_path / "Programs"]
    old = roots[0] / "Inno Setup 6"
    old.mkdir(parents=True)
    shutil.copyfile(sys._base_executable, old / "ISCC.exe")  # file version 3.12
    found, out = _fetch(roots)
    assert Path(found) == PINNED / "ISCC.exe"
    assert "skipping" in out and str(old / "ISCC.exe") in out
    new = roots[1] / "Inno Setup 7"
    shutil.copytree(PINNED, new)
    found, _out = _fetch(roots)
    assert Path(found) == new / "ISCC.exe"
