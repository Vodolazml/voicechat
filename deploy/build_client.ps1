# Builds the installer, signs it and (with -Server) publishes it: the download page and every installed
# app pick it up; clients update themselves at their next start.
#   deploy\build_client.ps1 -ServerUrl https://chat.example.org -Server root@203.0.113.10
param([Parameter(Mandatory = $true)][string]$ServerUrl, [string]$Server = "")
$ErrorActionPreference = "Stop"
if (-not $ServerUrl.StartsWith("https://")) { throw "ServerUrl must be https://chat.<your-domain>" }
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
python -m PyInstaller --noconfirm PrivateVoiceChat.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$config = @{server_url=$ServerUrl; lock_server_url=$true}
$target = Join-Path (Get-Location) "dist\PrivateVoiceChat\client_config.json"
[IO.File]::WriteAllText($target, ($config | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
$version = python -c "from app.version import APP_VERSION; print(APP_VERSION)"
$iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
& $iscc "/DMyAppVersion=$version" "build\setup.iss"
if ($LASTEXITCODE -ne 0) { throw "Installer build failed" }
$name = "PrivateVoiceChat-$version-setup.exe"
python deploy/sign_release.py --file "dist/$name" --version $version --url "$ServerUrl/downloads/$name"
if ($LASTEXITCODE -ne 0) { throw "Release signature failed" }

if ($Server) {
    $domain = ([Uri]$ServerUrl).Host -replace '^chat\.', ''
    scp "dist/$name" "dist/PrivateVoiceChat-$version-setup.release.json" "${Server}:/opt/voicechat/downloads/"
    if ($LASTEXITCODE -ne 0) { throw "Upload failed" }
    ssh $Server "cd /opt/voicechat && python3 deploy/configure.py $domain --installer $name && docker compose -f docker-compose.prod.yml up -d api"
    if ($LASTEXITCODE -ne 0) { throw "Publishing on the server failed" }
    Write-Host "Published $version. Download page: $ServerUrl"
}
