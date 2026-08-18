from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from app.version import APP_VERSION


# Путь к кэшу обновлений
UPDATE_CACHE_DIR = Path.home() / ".private_voicechat" / "updates"

# Время ожидания перед автооткатом (3 минуты)
ROLLBACK_TIMEOUT_SECONDS = 180

# Временная папка для распаковки
EXTRACT_TIMEOUT_SECONDS = 120


@dataclass
class UpdateProgress:
    """Текущий прогресс обновления."""
    step: str = "init"
    percentage: float = 0.0
    message: str = ""
    error: str = ""
    
    def is_complete(self) -> bool:
        return self.step in ("success", "error", "skipped", "cancelled")
    
    def is_running(self) -> bool:
        return self.step in ("checking", "downloading", "installing", "verifying", "restarting")


@dataclass
class UpdateResult:
    """Результат операции обновления."""
    success: bool
    step: str
    message: str
    new_version: str = ""
    old_version: str = ""
    rollback_needed: bool = False
    error: str = ""


def get_install_path() -> Path | None:
    """Получает путь установки из реестра или settings.json."""
    # Сначала проверяем реестр (установленная версия)
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\PrivateVoiceChat"
        )
        path, _ = winreg.QueryValueEx(key, "InstallPath")
        winreg.CloseKey(key)
        candidate = Path(path)
        if (candidate / "PrivateVoiceChat.exe").exists():
            return candidate
    except (OSError, FileNotFoundError):
        pass
    
    # Затем проверяем settings.json
    settings_path = Path.home() / ".private_voicechat" / "settings.json"
    if settings_path.exists():
        try:
            import json
            with settings_path.open("r") as f:
                settings = json.load(f)
            install_path = settings.get("install_path")
            if install_path:
                candidate = Path(install_path)
                if (candidate / "PrivateVoiceChat.exe").exists():
                    return candidate
        except (json.JSONDecodeError, OSError):
            pass
    
    return None


def get_installed_version() -> str | None:
    """Получает версию установленной программы."""
    install_path = get_install_path()
    if not install_path:
        return None
    
    # Проверяем version.txt
    version_file = install_path / "version.txt"
    if version_file.exists():
        try:
            return version_file.read_text().strip()
        except OSError:
            pass
    
    # Пробуем получить версию из exe
    exe_path = install_path / "PrivateVoiceChat.exe"
    if exe_path.exists():
        try:
            import subprocess
            result = subprocess.run(
                ["powershell", "-Command", 
                 "(Get-Item '" + str(exe_path) + "').VersionInfo.FileVersion"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            pass
    
    return None


def check_for_updates(
    server_url: str,
    current_version: str = APP_VERSION,
    progress_callback: Callable[[UpdateProgress], None] | None = None,
) -> UpdateResult:
    """Проверяет наличие обновлений на сервере."""
    progress = UpdateProgress("checking", 0, "Проверка обновлений...")
    if progress_callback:
        progress_callback(progress)
    
    try:
        url = f"{server_url.rstrip('/')}/client/update?version={current_version}"
        with httpx.Client(timeout=15) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
        
        latest_version = data.get("latest_version", current_version)
        update_available = data.get("update_available", False)
        
        if not update_available or latest_version == current_version:
            progress.step = "skipped"
            progress.percentage = 100
            progress.message = f"Установлена последняя версия {current_version}"
            if progress_callback:
                progress_callback(progress)
            return UpdateResult(
                success=True,
                step="skipped",
                message=f"Установлена последняя версия {current_version}",
                old_version=current_version,
            )
        
        progress.step = "ready"
        progress.percentage = 100
        progress.message = f"Доступна новая версия: {latest_version}"
        if progress_callback:
            progress_callback(progress)
        
        return UpdateResult(
            success=True,
            step="ready",
            message=f"Доступна новая версия {latest_version}",
            new_version=latest_version,
            old_version=current_version,
            download_url=data.get("download_url", ""),
            sha256=data.get("sha256", ""),
        )
    
    except httpx.HTTPError as exc:
        progress.step = "error"
        progress.error = f"Ошибка проверки: {exc}"
        if progress_callback:
            progress_callback(progress)
        return UpdateResult(
            success=False,
            step="error",
            message=f"Не удалось проверить обновления: {exc}",
            error=str(exc),
        )


def download_update_package(
    download_url: str,
    expected_sha256: str,
    progress_callback: Callable[[UpdateProgress], None] | None = None,
) -> UpdateResult:
    """Скачивает пакет обновления в кэш."""
    progress = UpdateProgress("downloading", 0, "Скачивание обновления...")
    if progress_callback:
        progress_callback(progress)
    
    try:
        UPDATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        
        with httpx.Client(timeout=120) as client:
            with client.stream("GET", download_url, follow_redirects=True) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length", 0))
                downloaded = 0
                sha256 = hashlib.sha256()
                
                target_file = UPDATE_CACHE_DIR / f"update-{int(time.time())}.zip"
                with target_file.open("wb") as f:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        sha256.update(chunk)
                        
                        if total > 0 and progress_callback:
                            progress.percentage = (downloaded / total) * 100
                            progress.message = f"Скачано {downloaded // 1024 // 1024} MB из {total // 1024 // 1024} MB"
        
        # Проверяем хэш
        actual_sha256 = sha256.hexdigest()
        if actual_sha256.lower() != expected_sha256.lower():
            target_file.unlink(missing_ok=True)
            return UpdateResult(
                success=False,
                step="error",
                message=f"Хэш не совпал! Ожидался: {expected_sha256[:16]}... Получен: {actual_sha256[:16]}...",
                error="SHA256 mismatch",
            )
        
        progress.step = "downloaded"
        progress.percentage = 100
        progress.message = "Обновление скачано"
        if progress_callback:
            progress_callback(progress)
        
        return UpdateResult(
            success=True,
            step="downloaded",
            message="Обновление скачано",
            download_path=target_file,
        )
    
    except httpx.HTTPError as exc:
        target_file.unlink(missing_ok=True)
        progress.step = "error"
        progress.error = f"Ошибка скачивания: {exc}"
        if progress_callback:
            progress_callback(progress)
        return UpdateResult(
            success=False,
            step="error",
            message=f"Не удалось скачать обновление: {exc}",
            error=str(exc),
        )


def install_update(
    download_path: Path,
    install_path: Path | None = None,
    progress_callback: Callable[[UpdateProgress], None] | None = None,
) -> UpdateResult:
    """Устанавливает обновление через setup.exe."""
    progress = UpdateProgress("installing", 0, "Подготовка установки...")
    if progress_callback:
        progress_callback(progress)
    
    try:
        # Определяем путь установки
        if not install_path:
            install_path = get_install_path() or (Path("C:") / "Program Files" / "Private VoiceChat")
        
        # Распаковываем zip в временную папку
        with tempfile.TemporaryDirectory(prefix="pvchat_setup_") as temp_dir:
            temp_path = Path(temp_dir)
            
            progress.message = "Распаковка файлов..."
            if progress_callback:
                progress_callback(progress)
            
            import zipfile
            with zipfile.ZipFile(download_path, 'r') as zf:
                zf.extractall(temp_path)
            
            # Ищем setup.exe
            setup_files = list(temp_path.glob("*setup*.exe")) + list(temp_path.glob("*.exe"))
            if not setup_files:
                return UpdateResult(
                    success=False,
                    step="error",
                    message="Не найден setup.exe в пакете обновления",
                    error="No setup.exe found",
                )
            
            setup_exe = setup_files[0]
            
            # Создаем бэкап текущей установки
            progress.message = "Создание резервной копии..."
            if progress_callback:
                progress_callback(progress)
            
            backup_dir = Path.home() / ".private_voicechat" / "backup"
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            if install_path.exists():
                backup_file = backup_dir / f"backup-{int(time.time())}.zip"
                with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for file in install_path.rglob("*"):
                        if file.is_file():
                            zf.write(file, file.relative_to(install_path))
            
            # Запускаем установщик
            progress.message = "Установка обновления..."
            if progress_callback:
                progress_callback(progress)
            
            result = subprocess.run(
                [str(setup_exe), "/DIR=" + str(install_path), "/VERYSILENT", "/NORESTART"],
                capture_output=True, text=True, timeout=300
            )
            
            if result.returncode != 0:
                return UpdateResult(
                    success=False,
                    step="error",
                    message=f"Установщик вернул ошибку: {result.stderr[:200]}",
                    error=f"Setup failed with code {result.returncode}",
                    rollback_path=backup_file if backup_file.exists() else None,
                )
        
        progress.step = "installed"
        progress.percentage = 100
        progress.message = "Обновление установлено"
        if progress_callback:
            progress_callback(progress)
        
        return UpdateResult(
            success=True,
            step="installed",
            message="Обновление успешно установлено",
            install_path=install_path,
            backup_path=backup_dir if backup_dir.exists() else None,
        )
    
    except subprocess.TimeoutExpired:
        return UpdateResult(
            success=False,
            step="error",
            message="Установщик завис",
            error="Setup timeout",
        )
    except Exception as exc:
        return UpdateResult(
            success=False,
            step="error",
            message=f"Ошибка установки: {exc}",
            error=str(exc),
        )


def verify_and_restart(
    install_path: Path,
    new_version: str,
    old_version: str,
    progress_callback: Callable[[UpdateProgress], None] | None = None,
) -> UpdateResult:
    """Проверяет новую версию и перезапускает программу."""
    progress = UpdateProgress("verifying", 0, "Проверка новой версии...")
    if progress_callback:
        progress_callback(progress)
    
    try:
        exe_path = install_path / "PrivateVoiceChat.exe"
        if not exe_path.exists():
            return UpdateResult(
                success=False,
                step="error",
                message=f"Не найден {exe_path.name} после установки",
                error="Exe not found after install",
            )
        
        # Запускаем новую версию в фоне и ждем
        progress.message = f"Запуск новой версии {new_version}..."
        if progress_callback:
            progress_callback(progress)
        
        proc = subprocess.Popen(
            [str(exe_path)],
            cwd=str(install_path),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
        
        # Ждем ROLLBACK_TIMEOUT_SECONDS перед откатом
        start_time = time.time()
        while time.time() - start_time < ROLLBACK_TIMEOUT_SECONDS:
            if proc.poll() is not None:
                # Процесс завершился — возможно ошибка
                break
            time.sleep(1)
        
        # Если все ок — очищаем кэш и бэкап
        progress.message = "Очистка временных файлов..."
        if progress_callback:
            progress_callback(progress)
        
        cleanup_update_cache()
        
        return UpdateResult(
            success=True,
            step="success",
            message=f"Успешно обновлено с {old_version} → {new_version}",
            new_version=new_version,
            old_version=old_version,
        )
    
    except Exception as exc:
        return UpdateResult(
            success=False,
            step="error",
            message=f"Ошибка при запуске новой версии: {exc}",
            error=str(exc),
            rollback_needed=True,
        )


def rollback_installation(
    backup_path: Path,
    install_path: Path,
    progress_callback: Callable[[UpdateProgress], None] | None = None,
) -> UpdateResult:
    """Откатывает установку из бэкапа."""
    progress = UpdateProgress("rolling_back", 0, "Откат установки...")
    if progress_callback:
        progress_callback(progress)
    
    try:
        # Удаляем текущую (сломанную) установку
        if install_path.exists():
            shutil.rmtree(install_path, ignore_errors=True)
        
        # Восстанавливаем из бэкапа
        latest_backup = max(backup_path.glob("backup-*.zip"), key=lambda p: p.stat().st_mtime)
        with zipfile.ZipFile(latest_backup, 'r') as zf:
            zf.extractall(install_path.parent)
        
        progress.step = "rolled_back"
        progress.percentage = 100
        progress.message = "Откат выполнен успешно"
        if progress_callback:
            progress_callback(progress)
        
        return UpdateResult(
            success=True,
            step="rolled_back",
            message="Установка откатана на предыдущую версию",
        )
    
    except Exception as exc:
        return UpdateResult(
            success=False,
            step="error",
            message=f"Ошибка отката: {exc}",
            error=str(exc),
        )


def cleanup_update_cache() -> None:
    """Очищает кэш обновлений."""
    if UPDATE_CACHE_DIR.exists():
        shutil.rmtree(UPDATE_CACHE_DIR, ignore_errors=True)
        UPDATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def cleanup_backup(backup_path: Path, keep_last: int = 1) -> None:
    """Оставляет только N последних бэкапов."""
    if not backup_path.exists():
        return
    
    backups = sorted(backup_path.glob("backup-*.zip"), key=lambda p: p.stat().st_mtime)
    while len(backups) > keep_last:
        oldest = backups.pop(0)
        oldest.unlink(missing_ok=True)