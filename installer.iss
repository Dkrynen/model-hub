; InnoSetup script for APT
; Build the .exe first with: pyinstaller build.spec
; Then compile: iscc installer.iss

#define MyAppName "APT"
#define MyAppVersion "2.2.0"
#define MyAppPublisher "Duan Krynen"
#define MyAppURL "https://github.com/Dkrynen/model-hub"
#define MyAppExeName "model-hub.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE
OutputDir=dist
OutputBaseFilename=APT-Setup-{#MyAppVersion}
SetupIconFile=
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: checkedonce
Name: "autostart"; Description: "Start {#MyAppName} when &Windows starts"; GroupDescription: "Startup options:"; Flags: unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/f /im {#MyAppExeName}"; Flags: runhidden

[Code]
var
  OpenResult: Integer;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\Ollama') then
    begin
      if MsgBox('Ollama was not detected. Download it?', mbConfirmation, MB_YESNO) = IDYES then
      begin
        Exec('rundll32.exe', 'url.dll,FileProtocolHandler https://ollama.com/download', '', SW_SHOW, ewNoWait, OpenResult);
      end;
    end;
  end;
end;
