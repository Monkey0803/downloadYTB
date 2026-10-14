; VideoDownloader Windows installer script (Inno Setup 6)
;
; Called from CI (this cannot be verified on macOS, so the test only checks that the
; script and the produced paths agree):
;   ISCC.exe /DAppVersion=1.12.0 build\windows_installer.iss
;
; Three decisions:
; - The whole file is ASCII, comments included. Inno versions disagree on how they read
;   a .iss file, and a non-ASCII string read as ANSI comes out as mojibake. This path can
;   only ever run in CI, so it is better to ship an English-only installer than a possibly
;   garbled one. test_installers.py enforces the ASCII rule.
; - Per-user install (PrivilegesRequired=lowest): an unsigned installer that writes into
;   the user's own directory avoids a UAC prompt and avoids leaving an admin-only uninstall.
; - The source is exactly the PyInstaller onedir output dist\VideoDownloader, packaged whole.

#define AppName "VideoDownloader"
#define AppExeName "VideoDownloader.exe"
#define AppPublisher "Monkey0803"
#define AppUrl "https://github.com/Monkey0803/downloadYTB"
#define SourceDir "..\dist\VideoDownloader"

; Version comes from the CI /DAppVersion= define; the fallback keeps the script openable
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; AppId identifies "the same application" across upgrades and uninstall; never change it
AppId={{7C4B0E52-6D2A-4C8B-9E31-5F0A9C2D7B14}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
; CI renames this to include the release tag, matching the other two platforms
OutputBaseFilename=VideoDownloader-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
; recursesubdirs: the onedir build has _internal and friends; without it the installed
; copy would be a directory that cannot start
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
