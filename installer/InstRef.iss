; Inno Setup script for InstRef.
; Built by GitHub Actions on tag push; see .github/workflows/release.yml
;
; The app is installed per-user (no admin prompt) because everything it needs
; lives under the user's own profile anyway: settings and the database go to
; %APPDATA%\InstRef, downloads wherever the user points them.

#define AppName    "InstRef"
#define AppPublish "Amriel"
#define AppURL     "https://github.com/Amriel/InstRef"
#define AppExe     "InstRef.exe"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7C4E1B22-9A1F-4C6E-9D0B-2F8E5A31C7D4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublish}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=InstRef-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-user install: no UAC prompt, and the uninstaller stays available.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\InstRef\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove the scheduled task so it does not fire after the app is gone.
Filename: "schtasks"; Parameters: "/Delete /F /TN ""InstRef Sync"""; Flags: runhidden; RunOnceId: "DropTask"

[UninstallDelete]
; Settings and the database survive an uninstall on purpose — reinstalling
; should not mean re-downloading a library the user already has. Removing
; %APPDATA%\InstRef by hand is the documented way to start clean.
Type: dirifempty; Name: "{app}"
