import pytest
from fastapi import HTTPException

from app.server.security import create_token, decode_token, hash_password, validate_password_strength, verify_password


def test_password_hash_is_not_plaintext() -> None:
    password = "StrongPass123"
    password_hash = hash_password(password)

    assert password_hash != password
    assert verify_password(password, password_hash)
    assert not verify_password("WrongPass123", password_hash)


def test_rejects_weak_password() -> None:
    with pytest.raises(HTTPException):
        validate_password_strength("short")


def test_token_roundtrip() -> None:
    token = create_token(42)
    payload = decode_token(token)

    assert payload.user_id == 42
    assert payload.jti
