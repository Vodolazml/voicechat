@echo off
REM Скрипт сборки PrivateVoiceChat установщика
REM Требования: Python 3.12+, Inno Setup 6+ (https://jrsoftware.org/isdl.php)
REM                PyInstaller (pip install pyinstaller)

setlocal enabledelayedexpansion

echo ========================================
echo PrivateVoiceChat - Сборка установщика
echo ========================================
echo.

REM Проверка Inno Setup
where iscc >nul 2>&1
if %errorlevel% neq 0 (
    echo [ОШИБКА] Inno Setup не найден!
    echo Установите Inno Setup с https://jrsoftware.org/isdl.php
    pause
    exit /b 1
)

REM Проверка PyInstaller
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo [ИНФО] Установка PyInstaller...
    pip install pyinstaller
)

REM Получаем версию из app/version.py
for /f "tokens=*" %%a in ('python -c "from app.version import APP_VERSION; print(APP_VERSION)"') do set VERSION=%%a

if "!VERSION!" == "" (
    echo [ОШИБКА] Не удалось получить версию из app/version.py
    pause
    exit /b 1
)

echo [ИНФО] Версия приложения: !VERSION!
echo.

REM Создаем временную папку для сборки
set BUILD_DIR=build\temp-build-!VERSION!
if exist "!BUILD_DIR!" rmdir /s /q "!BUILD_DIR!"
mkdir "!BUILD_DIR!"

echo [1/6] Создание portable версии...
pyinstaller --onefile ^
    --name PrivateVoiceChat ^
    --add-data "app\client\client_config.example.json;app\client" ^
    --add-data "app\version.py;app" ^
    --icon=NONE ^
    --noconsole ^
    --distdir "!BUILD_DIR!\portable" ^
    run_client.py

if %errorlevel% neq 0 (
    echo [ОШИБКА] PyInstaller завершился с ошибкой
    pause
    exit /b 1
)

echo [2/6] Копирование файлов для установщика...
mkdir "!BUILD_DIR!\portable-files"
xcopy "!BUILD_DIR!\portable\*" "!BUILD_DIR!\portable-files\" /E /I /Y

REM Создаем version.txt
echo !VERSION! > "!BUILD_DIR!\portable-files\version.txt"

echo [3/6] Создание ZIP пакета для автообновления...
set ZIP_FILE=dist\PrivateVoiceChat-!VERSION!.zip
if exist "!ZIP_FILE!" del /q "!ZIP_FILE!"

cd /d "!BUILD_DIR!\portable-files"
powershell -Command "Compress-Archive -Path * -DestinationPath '!ZIP_FILE!' -Force"
cd /d %~dp0

echo [4/6] Создание установщика...
set SETUP_FILE=dist\PrivateVoiceChat-!VERSION!-setup.exe
if exist "!SETUP_FILE!" del /q "!SETUP_FILE!"

REM Модифицируем setup.iss для текущей версии
set ISS_FILE=build\setup-temp.iss
powershell -Command "(Get-Content 'build\setup.iss') -replace '1\.0\.0', '!VERSION!' | Set-Content '!ISS_FILE!'"

iscc "!ISS_FILE!" /Odist /F"PrivateVoiceChat-!VERSION!-setup"

if %errorlevel% neq 0 (
    echo [ОШИБКА] Inno Setup завершился с ошибкой
    pause
    exit /b 1
)

echo [5/6] Генерация SHA256 хэшей...
powershell -Command "Get-Hash -Algorithm SHA256 -FilePath 'dist\PrivateVoiceChat-!VERSION!.zip' | Format-Hex | Select-Object -First 1 | ForEach-Object { $_.Hash }" > dist\PrivateVoiceChat-!VERSION!.zip.sha256

powershell -Command "Get-Hash -Algorithm SHA256 -FilePath 'dist\PrivateVoiceChat-!VERSION!-setup.exe' | Format-Hex | Select-Object -First 1 | ForEach-Object { $_.Hash }" > dist\PrivateVoiceChat-!VERSION!-setup.exe.sha256

echo [6/6] Очистка временных файлов...
rmdir /s /q "!BUILD_DIR!"
del /q build\setup-temp.iss >nul 2>&1

echo.
echo ========================================
echo Сборка завершена успешно!
echo ========================================
echo.
echo Файлы в dist\:
dir /b dist\*
echo.
echo Для деплоя загрузите в папку /downloads на сервере:
echo   - PrivateVoiceChat-!VERSION!.zip
echo   - PrivateVoiceChat-!VERSION!-setup.exe
echo   - *.sha256 файлы (для верификации)
echo.
pause