from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, status
from .config import get_settings


password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_token(user_id: int) -> str:
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + settings.access_token_minutes * 60,
        "jti": secrets.token_urlsafe(16),
    }
    encoded = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(settings.secret_key.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_b64(signature)}"


@dataclass(frozen=True)
class TokenPayload:
    user_id: int
    expires_at: int
    jti: str


def decode_token(token: str) -> TokenPayload:
    try:
        encoded, supplied_sig = token.split(".", 1)
        expected = _b64(hmac.new(get_settings().secret_key.encode(), encoded.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied_sig, expected):
            raise ValueError("bad signature")
        raw: dict[str, Any] = json.loads(_unb64(encoded))
        if int(raw["exp"]) < int(time.time()):
            raise ValueError("expired")
        return TokenPayload(user_id=int(raw["sub"]), expires_at=int(raw["exp"]), jti=str(raw["jti"]))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется повторный вход") from exc


def validate_password_strength(password: str) -> None:
    if len(password) < 10 or not any(c.isdigit() for c in password) or not any(c.isalpha() for c in password):
        raise HTTPException(status_code=400, detail="Пароль должен быть не короче 10 символов и содержать буквы и цифры")
