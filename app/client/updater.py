from __future__ import annotations

import hashlib
import os
import time
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from app.version import APP_VERSION
from app.update_trust import verify_release
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
    signature: str = ""
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
        return auto_download_update(info.download_url, info.sha256, self.progress_callback,
            version=getattr(info, 'latest_version', getattr(info, 'new_version', '')),
            signature=info.signature)
    
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


def remove_old_installers(keep: Path | None = None, keep_partial: Path | None = None) -> None:
    """Installers of earlier updates are useless once installed; they used to pile up (1+ GB)."""
    if not UPDATE_DIR.is_dir():
        return
    for path in UPDATE_DIR.iterdir():
        if not path.is_file() or path in (keep, keep_partial):
            continue
        if path.suffix.lower() == ".partial" and keep_partial is None and time.time() - path.stat().st_mtime < 86400:
            continue  # a recent half-finished download: the next start continues it
        if path.suffix.lower() in {".exe", ".partial", ".zip"}:
            try:
                path.unlink()
            except OSError:
                pass  # still running or locked: next time


def download_update(info: UpdateInfo, progress: Callable[[int, int], None] | None = None) -> Path:
    if not info.download_url:
        raise RuntimeError("URL обновления не задан")
    if not info.sha256:
        raise RuntimeError("SHA-256 обновления не задан")
    verify_release(info.latest_version, info.download_url, info.sha256, info.signature)
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    target = UPDATE_DIR / update_file_name(info.download_url, info.latest_version)
    if target.suffix.lower() != ".exe":
        raise RuntimeError("Обновление должно быть установщиком EXE")
    partial = target.with_suffix('.partial')
    # Keep this version's half-downloaded file: closing the app mid-download used to start it from zero.
    remove_old_installers(keep=target, keep_partial=partial)
    if target.exists() and sha256_file(target).lower() == info.sha256.lower():
        if progress:
            size = target.stat().st_size
            progress(size, size)
        return target  # already downloaded last time
    downloaded = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={downloaded}-"} if downloaded else {}
    with httpx.stream("GET", info.download_url, follow_redirects=True, timeout=60, headers=headers) as response:
        if response.status_code == 416:  # the partial file is already complete (or bigger than the file)
            downloaded, total, mode = 0, 0, None
        else:
            response.raise_for_status()
            resumed = response.status_code == 206 and downloaded > 0
            if not resumed:
                downloaded = 0  # the server sent the whole file
            total = downloaded + int(response.headers.get("content-length") or 0)
            mode = "ab" if resumed else "wb"
        if mode is not None:
            with partial.open(mode) as file:
                for chunk in response.iter_bytes():
                    downloaded += len(chunk)
                    if downloaded > 1024 * 1024 * 1024:
                        raise RuntimeError("Превышен максимальный размер обновления")
                    file.write(chunk)
                    if progress:
                        progress(downloaded, total)
    if sha256_file(partial).lower() != info.sha256.lower():
        partial.unlink(missing_ok=True)
        raise RuntimeError("Хэш обновления не совпал. Файл удалён.")
    os.replace(partial, target)
    return target


def open_update_file(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
        return
    subprocess.Popen(["xdg-open", str(path)])
