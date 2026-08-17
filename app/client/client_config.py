from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_NAME = "client_config.json"


@dataclass(frozen=True)
class ClientConfig:
    server_url: str | None = None
    lock_server_url: bool = False


def config_paths() -> list[Path]:
    paths = [Path.cwd() / CONFIG_NAME]
    if getattr(sys, "frozen", False):
        paths.insert(0, Path(sys.executable).resolve().parent / CONFIG_NAME)
    else:
        paths.append(Path(__file__).resolve().parents[2] / CONFIG_NAME)
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def load_client_config() -> ClientConfig:
    env_server_url = os.getenv("VOICECHAT_SERVER_URL")
    if env_server_url:
        return ClientConfig(server_url=env_server_url.strip(), lock_server_url=os.getenv("VOICECHAT_LOCK_SERVER_URL") == "1")
    for path in config_paths():
        try:
            with path.open("r", encoding="utf-8") as file:
                data: dict[str, Any] = json.load(file)
        except (OSError, json.JSONDecodeError):
            continue
        server_url = data.get("server_url")
        return ClientConfig(
            server_url=server_url.strip() if isinstance(server_url, str) else None,
            lock_server_url=bool(data.get("lock_server_url", False)),
        )
    return ClientConfig()
