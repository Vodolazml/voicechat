; Inno Setup скрипт для PrivateVoiceChat
; Скомпилировать: iscc build\setup.iss

#define MyAppName "Private VoiceChat"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Private VoiceChat Team"
#define MyAppURL "https://github.com/Vodolazml/voicechat.git"
#define MyAppExeName "PrivateVoiceChat.exe"

[Setup]
; NOTE: Значение AppId не менять! Оно определяет папку установки
AppId={{A3F7B2C1-4D5E-6F7A-8B9C-0D1E2F3A4B5C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName="{autopf}\{#MyAppName}"
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\dist
OutputBaseFilename=PrivateVoiceChat-{#MyAppVersion}-setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64Bit=x64
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "rus"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startwithwindows"; Description: "Запускать при старте Windows"; GroupDescription: "{cm:AdditionalIcons}"
Name: "associatezip"; Description: "Открывать .zip обновления по умолчанию"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Исходные файлы из папки portable (собирается через build.bat)
Source: "..\dist\portable\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Примечание: {app}\version.txt используется для проверки установки

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\client.ico"
Name: "{group}\{#MyAppName} (Администратор)"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--admin"; IconFilename: "{app}\client.ico"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Registry]
; Сохраняем путь установки для автообновления
Root: "HKCU"; Subkey: "Software\PrivateVoiceChat"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
Root: "HKCU"; Subkey: "Software\PrivateVoiceChat"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"
; Автозапуск через реестр
Root: "HKCU"; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PrivateVoiceChat"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: startwithwindows

[Code]
var
  InstallPathMemo: String;

function InitializeSetup(): Boolean;
begin
  Result := True;
  // Проверяем есть ли уже установленная версия
  if RegKeyExists(HKCU, 'Software\PrivateVoiceChat') then
  begin
    RegQueryStringValue(HKCU, 'Software\PrivateVoiceChat', 'InstallPath', InstallPathMemo);
    if InstallPathMemo <> '' then
    begin
      if MsgBox(
        'Обнаружена установленная версия Private VoiceChat.' + #13#10 + #13#10 + 
        'Текущая версия: ' + InstallPathMemo + #13#10 + #13#10 +
        'Хотите обновить её?' + #13#10 + #13#10 +
        'Нажмите "Да" для обновления в текущую папку.' + #13#10 +
        'Нажмите "Отмена" для установки в новую папку.',
        mbInformation, MB_YESNO or MB_CANCEL) = idYes then
      begin
        // Используем существующий путь
        Wizard.DirName := InstallPathMemo;
      end;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  VersionFile: String;
begin
  if CurStep = ssPostInstall then
  begin
    // Создаем файл версии для программной проверки
    VersionFile := ExpandConstant('{app}\version.txt');
    SaveStringToFile(VersionFile, '{#MyAppVersion}', False);
    
    // Сохраняем путь в settings.json для автообновления
    // (будет использовано клиентом)
  end;
end;

function UninstallOldVersion(): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  // Проверяем есть ли старая portable версия и удаляем её
  if RegKeyExists(HKCU, 'Software\PrivateVoiceChat') then
  begin
    if RegQueryStringValue(HKCU, 'Software\PrivateVoiceChat', 'InstallPath', InstallPathMemo) then
    begin
      // Удаляем старую версию через её uninstaller если есть
      if FileExists(ExpandConstant('{app}\unins000.exe')) then
      begin
        Exec(ExpandConstant('{app}\unins000.exe'), '/silent', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      end;
    end;
  end;
  Result := True;
end;

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{group}"