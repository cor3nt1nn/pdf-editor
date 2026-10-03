; PDF Editor setup script (Inno Setup 7; also compiles with Inno Setup 6).
;
; Built by scripts\build_exe.ps1 -Installer:
;   ISCC.exe -q -dAppVersion=<version> -dFileVersion=<n.n.n.n> -dSourceDir=<dist\PDFEditor> -dOutputDir=<dist> installer\pdfeditor.iss
; AppVersion is __version__ as is (it may be "0.2.0rc1"); FileVersion is its leading numbers
; padded to four ("0.2.0.0"): the version resource's binary file version must be numeric.
; Per-user install by default (no administrator rights), x64 only, English and French.
; The installer never writes file-association keys itself: the "Open with" task runs
; PDFEditor.exe --register-file-type and the uninstaller --unregister-file-type, which
; call core\file_assoc.py (docs\ARCHITECTURE.md, "M8 design record (installer)").

#ifndef AppVersion
  #error Define AppVersion: ISCC -dAppVersion=<version> installer\pdfeditor.iss
#endif
#ifndef FileVersion
  #error Define FileVersion: ISCC -dFileVersion=<n.n.n.n> installer\pdfeditor.iss
#endif
#define RepoRoot SourcePath + "..\"
#ifndef SourceDir
  #define SourceDir RepoRoot + "dist\PDFEditor"
#endif
#ifndef OutputDir
  #define OutputDir RepoRoot + "dist"
#endif
#define AppName "PDF Editor"
#define AppExeName "PDFEditor.exe"
; Must equal pdfeditor.app.REGISTER_FLAG / UNREGISTER_FLAG (tests/test_installer_script.py).
#define RegisterFlag "--register-file-type"
#define UnregisterFlag "--unregister-file-type"
; Their exit codes: pdfeditor.app.EXIT_REGISTRY_ERROR / EXIT_REGISTER_FAILED.
; Must equal pdfeditor.app.INSTANCE_MUTEX: the running app owns it, so Setup and the
; uninstaller ask to close PDF Editor first.
#define AppMutexName "PDFEditor-2F4F77DF-7029-452C-AE27-01CC3FD48311"
#define ExitRegistryError "2"
#define ExitRegisterFailed "3"

[Setup]
; The AppId identifies the installation for upgrades and the uninstaller: NEVER change it.
AppId={{2F4F77DF-7029-452C-AE27-01CC3FD48311}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=PDF Editor contributors
DefaultDirName={autopf}\PDFEditor
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
#if VER >= EncodeVer(7, 0, 0)
SetupArchitecture=x64
#endif
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=PDFEditor-{#AppVersion}-setup
Compression=lzma2/ultra64
SolidCompression=yes
#if VER < EncodeVer(7, 0, 0)
; Inno Setup 7 always compresses in a separate process (the directive is obsolete there).
LZMAUseSeparateProcess=yes
#endif
WizardStyle=modern
SetupIconFile={#RepoRoot}src\pdfeditor\resources\app.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
VersionInfoVersion={#FileVersion}
VersionInfoTextVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
ShowLanguageDialog=auto
CloseApplications=yes
AppMutex={#AppMutexName}
LicenseFile={#RepoRoot}LICENSE

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[CustomMessages]
en.OpenWithTask=Add PDF Editor to the “Open with” list of PDF files (this account only)
fr.OpenWithTask=Ajouter PDF Editor à la liste « Ouvrir avec » des fichiers PDF (ce compte uniquement)
en.DeleteUserData=Also delete your PDF Editor settings, saved signatures and log files?%n%n%1%n%2
fr.DeleteUserData=Supprimer aussi vos paramètres, signatures enregistrées et fichiers journaux de PDF Editor ?%n%n%1%n%2
en.RegisterFailed=PDF Editor could not be added to the “Open with” list. You can do it later from Settings.
fr.RegisterFailed=PDF Editor n’a pas pu être ajouté à la liste « Ouvrir avec ». Vous pourrez le faire plus tard depuis Paramètres.
en.DeleteFailed=Some files in %1 could not be deleted (is PDF Editor still open?). You can delete that folder yourself.
fr.DeleteFailed=Certains fichiers de %1 n’ont pas pu être supprimés (PDF Editor est-il encore ouvert ?). Vous pouvez supprimer ce dossier vous-même.
en.UserDataKept=PDF Editor was removed for all users. Each user’s settings, saved signatures and log files are kept in that user’s profile, in the AppData\Roaming\PDFEditor and AppData\Local\PDFEditor folders; each user can delete them.
fr.UserDataKept=PDF Editor a été désinstallé pour tous les utilisateurs. Les paramètres, signatures enregistrées et fichiers journaux de chaque utilisateur sont conservés dans son profil, dossiers AppData\Roaming\PDFEditor et AppData\Local\PDFEditor ; chacun peut les supprimer.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; Not offered for an all-users install: it would register the administrator account, and
; the elevated uninstaller could not remove the registration of the user who asked for it.
Name: "openwith"; Description: "{cm:OpenWithTask}"; Flags: unchecked; Check: not IsAdminInstallMode

[InstallDelete]
; An upgrade replaces the bundled libraries entirely (no stale DLL from an older build).
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Check: WantStartMenuIcon
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Code]
{ /NOICONS only ticks the hidden "no Start menu folder" box; honour it for the shortcut too. }
function WantStartMenuIcon: Boolean;
var
  I: Integer;
begin
  Result := not WizardNoIcons;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/NOICONS') = 0 then
      Result := False;
end;

{ What an exit code of the file-type flags means (for the setup and uninstall logs). }
function FileTypeResult(Code: Integer): String;
begin
  case Code of
    0: Result := 'done';
    {#ExitRegistryError}: Result := 'the registry refused the change (access denied?)';
    {#ExitRegisterFailed}: Result := 'unexpected error';
    -1: Result := 'PDFEditor.exe could not be started';
  else
    Result := 'unknown exit code';
  end;
end;

{ The "Open with" task: PDFEditor.exe --register-file-type writes the HKCU keys of
  core\file_assoc.py for the user who started Setup; exit code 0 = done. }
procedure RegisterFileType;
var
  ResultCode: Integer;
begin
  if not ExecAsOriginalUser(ExpandConstant('{app}\{#AppExeName}'), '{#RegisterFlag}', '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    ResultCode := -1;
  Log(Format('{#RegisterFlag} exit code: %d (%s)', [ResultCode, FileTypeResult(ResultCode)]));
  if ResultCode <> 0 then
    SuppressibleMsgBox(CustomMessage('RegisterFailed'), mbInformation, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('openwith') then
    RegisterFileType;
end;

{ Delete one of the user's data folders; say so when something stays behind. }
procedure DeleteUserFolder(const Dir: String);
begin
  if DirExists(Dir) and not DelTree(Dir, True, True, True) then
  begin
    Log(Format('could not delete %s', [Dir]));
    SuppressibleMsgBox(FmtMessage(CustomMessage('DeleteFailed'), [Dir]), mbError, MB_OK, IDOK);
  end;
end;

{ After an interactive per-user uninstall, offer (default No) to delete the user's data:
  settings and signatures in %APPDATA%\PDFEditor, logs in %LOCALAPPDATA%\PDFEditor.
  A silent uninstall never deletes them. An all-users uninstall runs elevated: there the
  userappdata and localappdata constants are the administrator's folders, not those of the
  people who used PDF Editor, so it deletes nothing and only says where the data lives. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Roaming, Local: String;
  ResultCode: Integer;
begin
  { Before the files go: PDFEditor.exe --unregister-file-type removes this copy's "Open
    with" registration (it keeps one that opens another copy, and writes no file). }
  if CurUninstallStep = usUninstall then
  begin
    if not Exec(ExpandConstant('{app}\{#AppExeName}'), '{#UnregisterFlag}', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode) then
      ResultCode := -1;
    Log(Format('{#UnregisterFlag} exit code: %d (%s)', [ResultCode, FileTypeResult(ResultCode)]));
  end;
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
  begin
    if IsAdminInstallMode then
    begin
      SuppressibleMsgBox(CustomMessage('UserDataKept'), mbInformation, MB_OK, IDOK);
      Exit;
    end;
    Roaming := ExpandConstant('{userappdata}\PDFEditor');
    Local := ExpandConstant('{localappdata}\PDFEditor');
    if DirExists(Roaming) or DirExists(Local) then
      if MsgBox(FmtMessage(CustomMessage('DeleteUserData'), [Roaming, Local]),
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DeleteUserFolder(Roaming);
        DeleteUserFolder(Local);
      end;
  end;
end;
