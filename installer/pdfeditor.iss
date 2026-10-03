; PDF Editor setup script (Inno Setup 7; also compiles with Inno Setup 6).
;
; Built by scripts\build_exe.ps1 -Installer:
;   ISCC.exe -q -dAppVersion=<version> -dSourceDir=<dist\PDFEditor> -dOutputDir=<dist> installer\pdfeditor.iss
; Per-user install by default (no administrator rights), x64 only, English and French.
; The installer never writes file-association keys itself: the "Open with" task runs
; PDFEditor.exe --register-file-type and the uninstaller --unregister-file-type, which
; call core\file_assoc.py (docs\ARCHITECTURE.md, "M8 design record (installer)").

#ifndef AppVersion
  #error Define AppVersion: ISCC -dAppVersion=<version> installer\pdfeditor.iss
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
VersionInfoVersion={#AppVersion}
VersionInfoTextVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
ShowLanguageDialog=auto
CloseApplications=yes
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

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "openwith"; Description: "{cm:OpenWithTask}"; Flags: unchecked

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

[UninstallRun]
Filename: "{app}\{#AppExeName}"; Parameters: "{#UnregisterFlag}"; Flags: runhidden waituntilterminated; RunOnceId: "UnregisterFileType"

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

{ The "Open with" task: PDFEditor.exe --register-file-type writes the HKCU keys of
  core\file_assoc.py for the user who started Setup; exit code 0 = done. }
procedure RegisterFileType;
var
  ResultCode: Integer;
begin
  if not ExecAsOriginalUser(ExpandConstant('{app}\{#AppExeName}'), '{#RegisterFlag}', '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    ResultCode := -1;
  Log(Format('{#RegisterFlag} exit code: %d', [ResultCode]));
  if ResultCode <> 0 then
    SuppressibleMsgBox(CustomMessage('RegisterFailed'), mbInformation, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('openwith') then
    RegisterFileType;
end;

{ After an interactive uninstall, offer (default No) to delete the user's data:
  settings and signatures in %APPDATA%\PDFEditor, logs in %LOCALAPPDATA%\PDFEditor.
  A silent uninstall never deletes them. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Roaming, Local: String;
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
  begin
    Roaming := ExpandConstant('{userappdata}\PDFEditor');
    Local := ExpandConstant('{localappdata}\PDFEditor');
    if DirExists(Roaming) or DirExists(Local) then
      if MsgBox(FmtMessage(CustomMessage('DeleteUserData'), [Roaming, Local]),
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(Roaming, True, True, True);
        DelTree(Local, True, True, True);
      end;
  end;
end;
