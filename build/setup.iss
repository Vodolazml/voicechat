#ifndef MyAppVersion
#define MyAppVersion "0.0.0"
#endif

#define MyAppName "Private VoiceChat"
#define MyAppPublisher "Private VoiceChat Team"
#define MyAppURL "https://github.com/Vodolazml/voicechat"
#define MyAppExeName "PrivateVoiceChat.exe"

[Setup]
AppId={{A3F7B2C1-4D5E-6F7A-8B9C-0D1E2F3A4B5C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright=Copyright (C) 2026 {#MyAppPublisher}
; The installer's own version resource: an installer that names nobody looks anonymous to antivirus heuristics.
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=VoiceChat Setup
VersionInfoProductName=VoiceChat
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Private VoiceChat
DefaultGroupName={#MyAppName}
SetupIconFile=..\assets\app_icon.ico
AllowNoIcons=yes
OutputDir=..\dist
OutputBaseFilename=PrivateVoiceChat-{#MyAppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}
; Tell Explorer to refresh icons after an update, so the existing shortcut shows the new logo.
ChangesAssociations=yes

[Languages]
Name: "rus"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "startwithwindows"; Description: "Запускать при старте Windows"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\PrivateVoiceChat\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Older installers named the desktop shortcut after the app ("Private VoiceChat"); keep only "VoiceChat".
Type: files; Name: "{userdesktop}\{#MyAppName}.lnk"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
; The desktop shortcut is created once and never rewritten: rewriting it on every update made Windows
; forget where the user had placed it. Its picture comes from the exe, so it still updates.
Name: "{userdesktop}\VoiceChat"; Filename: "{app}\{#MyAppExeName}"; Check: not DesktopShortcutExists
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Registry]
Root: HKCU; Subkey: "Software\PrivateVoiceChat"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\PrivateVoiceChat"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PrivateVoiceChat"; ValueData: """{app}\{#MyAppExeName}"" --tray"; Tasks: startwithwindows

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
; Silent in-app update: start the new version again right away.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait runasoriginaluser; Check: WizardSilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function DesktopShortcutExists: Boolean;
begin
  Result := FileExists(ExpandConstant('{userdesktop}\VoiceChat.lnk'));
end;

procedure SHChangeNotify(wEventId: Integer; uFlags: Cardinal; dwItem1, dwItem2: Cardinal);
  external 'SHChangeNotify@shell32.dll stdcall';

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SHChangeNotify($08000000, 0, 0, 0);  { SHCNE_ASSOCCHANGED: drop cached icons }
end;
