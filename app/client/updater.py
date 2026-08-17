from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.version import APP_VERSION


UPDATE_DIR = Path.home() / ".private_voicechat" / "updates"


@dataclass(frozen=True)
class UpdateInfo:
    latest_version: str
    update_available: bool
    required: bool
    download_url: str = ""
    sha256: str = ""
    release_notes_url: str = ""


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
