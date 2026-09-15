param([string]$ServerUrl = "http://72.35.246.230:8765")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
python -m PyInstaller --noconfirm PrivateVoiceChat.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$config = @{server_url=$ServerUrl; lock_server_url=$true; allow_insecure_http=$ServerUrl.StartsWith("http://")}
$target = Join-Path (Get-Location) "dist\PrivateVoiceChat\client_config.json"
[IO.File]::WriteAllText($target, ($config | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
$version = python -c "from app.version import APP_VERSION; print(APP_VERSION)"
$iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
& $iscc "/DMyAppVersion=$version" "build\setup.iss"
if ($LASTEXITCODE -ne 0) { throw "Installer build failed" }
$name = "PrivateVoiceChat-$version-setup.exe"
python deploy/sign_release.py --file "dist/$name" --version $version --url "$ServerUrl/downloads/$name"
if ($LASTEXITCODE -ne 0) { throw "Release signature failed" }
