@echo off
setlocal enabledelayedexpansion

set "ROOT=%~dp0.."
pushd "%ROOT%" || exit /b 1

for /f "usebackq delims=" %%a in (`python -c "from app.version import APP_VERSION; print(APP_VERSION)"`) do set "VERSION=%%a"
if "%VERSION%"=="" (
    echo Failed to read app version.
    popd
    exit /b 1
)

if "%VOICECHAT_RELEASE_SERVER_URL%"=="" set "VOICECHAT_RELEASE_SERVER_URL=http://72.35.246.230:8765"

set "ISCC="
where iscc >nul 2>&1
if not errorlevel 1 set "ISCC=iscc"
if "%ISCC%"=="" if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if "%ISCC%"=="" (
    echo Inno Setup compiler is not installed or is not in PATH.
    echo Install Inno Setup 6, then run this script again.
    popd
    exit /b 1
)

python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 python -m pip install pyinstaller

echo Building PrivateVoiceChat %VERSION%
python -m PyInstaller -y --clean PrivateVoiceChat.spec
if errorlevel 1 (
    echo PyInstaller failed.
    popd
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$cfg=[ordered]@{server_url=$env:VOICECHAT_RELEASE_SERVER_URL;lock_server_url=$true;allow_insecure_http=$true}|ConvertTo-Json; Set-Content -Encoding UTF8 -Path 'dist\PrivateVoiceChat\client_config.json' -Value $cfg"
if errorlevel 1 (
    echo Failed to write client_config.json.
    popd
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Set-Content -Encoding UTF8 -Path 'dist\PrivateVoiceChat\version.txt' -Value '%VERSION%'"

set "PORTABLE_ZIP=dist\PrivateVoiceChat-%VERSION%-ip.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; for ($i=1; $i -le 5; $i++) { try { Compress-Archive -Path 'dist\PrivateVoiceChat\*' -DestinationPath '%PORTABLE_ZIP%' -Force; exit 0 } catch { if ($i -eq 5) { throw }; Start-Sleep -Seconds 2 } }"
if errorlevel 1 (
    echo Failed to create portable zip.
    popd
    exit /b 1
)

"%ISCC%" /DMyAppVersion=%VERSION% build\setup.iss
if errorlevel 1 (
    echo Inno Setup failed.
    popd
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "(Get-FileHash '%PORTABLE_ZIP%' -Algorithm SHA256).Hash | Set-Content -Encoding ascii '%PORTABLE_ZIP%.sha256'"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "(Get-FileHash 'dist\PrivateVoiceChat-%VERSION%-setup.exe' -Algorithm SHA256).Hash | Set-Content -Encoding ascii 'dist\PrivateVoiceChat-%VERSION%-setup.exe.sha256'"

echo.
echo Build complete:
echo   dist\PrivateVoiceChat-%VERSION%-setup.exe
echo   dist\PrivateVoiceChat-%VERSION%-setup.exe.sha256
echo   %PORTABLE_ZIP%
echo   %PORTABLE_ZIP%.sha256

popd
