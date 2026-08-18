from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


SETTINGS_PATH = Path.home() / ".private_voicechat" / "settings.json"


def load_client_settings() -> dict[str, Any]:
    """Загружает настройки клиента.
    
    Поддерживает два формата:
    1. Обычный JSON (без шифрования)
    2. Зашифрованный JSON с мастер-паролем (если есть ключ '_encrypted')
    
    Для защиты приватных E2EE ключей рекомендуется установить переменную
    окружения PRIVATE_VOICECHAT_MASTER_KEY или указать master_password
    в client_config.json.
    """
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    
    if not isinstance(data, dict):
        return {}
    
    # Если настройки зашифрованы, расшифруем их
    if data.get("_encrypted"):
        master_key = _get_master_key()
        if master_key:
            try:
                return _decrypt_settings(data, master_key)
            except ValueError:
                return {}
    
    return data


def save_client_settings(settings: dict[str, Any]) -> None:
    """Сохраняет настройки клиента с защитой прав доступа.
    
    Если установлен master_key и есть E2EE ключи, настройки шифруются перед сохранением.
    Файл получает права 0o600 (только для владельца).
    """
    master_key = _get_master_key()
    
    # Шифруем если есть мастер-ключ и есть E2EE приватный ключ
    should_encrypt = False
    if master_key:
        # Проверяем есть ли приватный ключ в настройках
        should_encrypt = "e2ee_identity_private_key" in settings
    
    if should_encrypt:
        encrypted_data = _encrypt_settings(settings, master_key)
        data_to_save = encrypted_data
    else:
        data_to_save = settings
    
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS_PATH.open("w", encoding="utf-8") as file:
        json.dump(data_to_save, file, ensure_ascii=False, indent=2)
    
    try:
        os.chmod(SETTINGS_PATH, 0o600)
    except OSError:
        pass


def _get_master_key() -> str | None:
    """Получает мастер-ключ из окружения или настроек."""
    # Сначала проверяем переменную окружения
    env_key = os.environ.get("PRIVATE_VOICECHAT_MASTER_KEY")
    if env_key:
        return env_key
    
    # Затем проверяем packaged config
    try:
        config_path = Path(__file__).parent.parent.parent / "client_config.json"
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as f:
                config = json.load(f)
                return config.get("master_password")
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    
    return None


def _encrypt_settings(settings: dict[str, Any], master_key: str) -> dict[str, Any]:
    """Шифрует настройки с помощью AES-GCM и мастер-ключа."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    
    # Derive key из мастер-пароля
    salt = b"pvchat_settings_salt"  # 16 байт соли
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,  # OWASP рекомендует 480000 для PBKDF2-HMAC-SHA256
    )
    key = kdf.derive(master_key.encode())
    
    # Генерируем nonce
    nonce = os.urandom(12)
    
    # Шифруем настройки (без флага _encrypted)
    plain = json.dumps(settings, ensure_ascii=False, indent=2).encode()
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plain, salt)
    
    return {
        "_encrypted": True,
        "_salt": salt.hex(),
        "_nonce": nonce.hex(),
        "_data": ciphertext.hex(),
    }


def _decrypt_settings(encrypted: dict[str, Any], master_key: str) -> dict[str, Any]:
    """Расшифровывает настройки."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    
    salt = bytes.fromhex(encrypted["_salt"])
    nonce = bytes.fromhex(encrypted["_nonce"])
    ciphertext = bytes.fromhex(encrypted["_data"])
    
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    key = kdf.derive(master_key.encode())
    
    aesgcm = AESGCM(key)
    plain = aesgcm.decrypt(nonce, ciphertext, salt)
    
    return json.loads(plain.decode())