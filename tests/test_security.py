import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.server import models
from app.server.database import Base
from app.server.deps import user_from_token
from app.server.security import create_token, decode_token, hash_password, validate_password_strength, verify_password
from app.media_crypto import decrypt_frame, encrypt_frame


def test_password_hash_is_not_plaintext() -> None:
    password = "StrongPass123!"
    password_hash = hash_password(password)

    assert password_hash != password
    assert verify_password(password, password_hash)
    assert not verify_password("WrongPass123", password_hash)


def test_rejects_weak_password() -> None:
    with pytest.raises(HTTPException):
        validate_password_strength("short")
    with pytest.raises(HTTPException):
        validate_password_strength("NoSpecialPass123")


def test_token_roundtrip() -> None:
    password_hash = hash_password("StrongPass123!")
    token = create_token(42, password_hash)
    payload = decode_token(token)

    assert payload.user_id == 42
    assert payload.jti
    assert payload.password_fingerprint


def test_token_is_bound_to_current_password_hash() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as db:
        old_hash = hash_password("StrongPass123!")
        user = models.User(username="admin", display_name="Администратор", password_hash=old_hash)
        db.add(user)
        db.flush()
        token = create_token(user.id, old_hash)

        assert user_from_token(db, token).id == user.id

        user.password_hash = hash_password("NewStrongPass123!")
        db.commit()

        with pytest.raises(HTTPException):
            user_from_token(db, token)


def test_media_frames_are_encrypted_and_authenticated() -> None:
    key = b"k" * 32
    plaintext = b"raw voice or screen bytes"
    packet = encrypt_frame(key, plaintext, b"voice")

    assert plaintext not in packet
    assert decrypt_frame(key, packet, b"voice") == plaintext

    with pytest.raises(ValueError):
        decrypt_frame(key, packet, b"screen")
    with pytest.raises(ValueError):
        decrypt_frame(key, plaintext, b"voice")
