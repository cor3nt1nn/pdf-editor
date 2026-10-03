"""Static checks of the Inno Setup script installer/pdfeditor.iss (M8-I2): no compiler
needed. The compiled setup is tested by tests/test_installer.py (marker ``installer``)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pdfeditor import app

ISS = Path(__file__).resolve().parent.parent / "installer" / "pdfeditor.iss"
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


def test_flags_used(sections) -> None:
    (uninstall_run,) = sections["UninstallRun"]
    assert 'Parameters: "{#UnregisterFlag}"' in uninstall_run
    assert "runhidden" in uninstall_run and "RunOnceId" in uninstall_run
    code = "\n".join(sections["Code"])
    assert "'{#RegisterFlag}'" in code
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
    assert code.count("DelTree(") == 2


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
