from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from app.version import APP_VERSION
from .auto_updater import (
    UpdateProgress,
    UpdateResult,
    check_for_updates as auto_check_updates,
    download_update_package as auto_download_update,
    install_update as auto_install_update,
    verify_and_restart as auto_verify_restart,
    rollback_installation as auto_rollback,
    cleanup_update_cache,
    get_install_path,
    get_installed_version,
)


UPDATE_DIR = Path.home() / ".private_voicechat" / "updates"


@dataclass(frozen=True)
class UpdateInfo:
    latest_version: str
    update_available: bool
    required: bool
    download_url: str = ""
    sha256: str = ""
    release_notes_url: str = ""


@dataclass
class UpdateWorkflow:
    """Полный цикл обновления с прогрессом и rollback."""
    progress_callback: Callable[[UpdateProgress], None] | None = None
    current_version: str = APP_VERSION
    install_path: Path | None = None
    
    def check(self, server_url: str) -> UpdateResult:
        """Проверяет наличие обновлений."""
        return auto_check_updates(server_url, self.current_version, self.progress_callback)
    
    def download(self, info: UpdateInfo) -> UpdateResult:
        """Скачивает обновление."""
        if not info.download_url or not info.sha256:
            return UpdateResult(
                success=False, step="error",
                message="URL или SHA-256 не заданы", error="Missing update info",
            )
        return auto_download_update(info.download_url, info.sha256, self.progress_callback)
    
    def install(self, download_path: Path) -> UpdateResult:
        """Устанавливает обновление."""
        return auto_install_update(download_path, self.install_path, self.progress_callback)
    
    def verify_and_restart(self, new_version: str, old_version: str) -> UpdateResult:
        """Запускает новую версию с таймаутом отката."""
        if not self.install_path:
            self.install_path = get_install_path() or Path("C:") / "Program Files" / "Private VoiceChat"
        return auto_verify_restart(self.install_path, new_version, old_version, self.progress_callback)
    
    def rollback(self, backup_path: Path) -> UpdateResult:
        """Откатывает установку."""
        if not self.install_path:
            self.install_path = get_install_path() or Path("C:") / "Program Files" / "Private VoiceChat"
        return auto_rollback(backup_path, self.install_path, self.progress_callback)


def update_file_name(url: str, version: str) -> str:
    name = Path(urlparse(url).path).name
    return name or f"PrivateVoiceChat-{version}.zip"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_update(info: UpdateInfo) -> Path:
    if not info.download_url:
        raise RuntimeError("URL обновления не задан")
    if not info.sha256:
        raise RuntimeError("SHA-256 обновления не задан")
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    target = UPDATE_DIR / update_file_name(info.download_url, info.latest_version)
    with httpx.stream("GET", info.download_url, follow_redirects=True, timeout=60) as response:
        response.raise_for_status()
        with target.open("wb") as file:
            for chunk in response.iter_bytes():
                file.write(chunk)
    if sha256_file(target).lower() != info.sha256.lower():
        target.unlink(missing_ok=True)
        raise RuntimeError("Хэш обновления не совпал. Файл удалён.")
    return target


def open_update_file(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
        return
    subprocess.Popen(["xdg-open", str(path)])
