from __future__ import annotations

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


ENVELOPE_NONCE_BYTES = 12
MEDIA_KEY_BYTES = 32


def b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def generate_private_key_b64() -> str:
    private_key = x25519.X25519PrivateKey.generate()
    raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return b64encode(raw)


def public_key_b64(private_key_b64: str) -> str:
    private_key = x25519.X25519PrivateKey.from_private_bytes(b64decode(private_key_b64))
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return b64encode(raw)


def public_key_fingerprint(public_key: str) -> str:
    digest = hashlib.sha256(b64decode(public_key)).digest()
    return ":".join(f"{byte:02X}" for byte in digest[:8])


def generate_media_key() -> bytes:
    return os.urandom(MEDIA_KEY_BYTES)


def media_key_id(media_key: bytes) -> str:
    return b64encode(hashlib.sha256(media_key).digest())[:16]


def _identity_private(private_key_b64: str) -> x25519.X25519PrivateKey:
    raw = b64decode(private_key_b64)
    if len(raw) != 32:
        raise ValueError("bad private key")
    return x25519.X25519PrivateKey.from_private_bytes(raw)


def _identity_public(public_key: str) -> x25519.X25519PublicKey:
    raw = b64decode(public_key)
    if len(raw) != 32:
        raise ValueError("bad public key")
    return x25519.X25519PublicKey.from_public_bytes(raw)


def _envelope_aad(channel_id: int, sender_id: int, recipient_id: int, key_id: str) -> bytes:
    return f"private-voicechat:e2ee-envelope:v1:{channel_id}:{sender_id}:{recipient_id}:{key_id}".encode()


def _derive_envelope_key(private_key_b64: str, peer_public_key: str, channel_id: int) -> bytes:
    shared = _identity_private(private_key_b64).exchange(_identity_public(peer_public_key))
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=f"private-voicechat:e2ee-channel:{channel_id}".encode(),
        info=b"media-sender-key-envelope:v1",
    ).derive(shared)


def encrypt_sender_media_key(
    *,
    private_key_b64: str,
    recipient_public_key: str,
    media_key: bytes,
    channel_id: int,
    sender_id: int,
    recipient_id: int,
    key_id: str,
) -> str:
    if len(media_key) != MEDIA_KEY_BYTES:
        raise ValueError("bad media key")
    nonce = os.urandom(ENVELOPE_NONCE_BYTES)
    key = _derive_envelope_key(private_key_b64, recipient_public_key, channel_id)
    ciphertext = AESGCM(key).encrypt(nonce, media_key, _envelope_aad(channel_id, sender_id, recipient_id, key_id))
    return b64encode(nonce + ciphertext)


def decrypt_sender_media_key(
    *,
    private_key_b64: str,
    sender_public_key: str,
    envelope: str,
    channel_id: int,
    sender_id: int,
    recipient_id: int,
    key_id: str,
) -> bytes:
    data = b64decode(envelope)
    nonce = data[:ENVELOPE_NONCE_BYTES]
    ciphertext = data[ENVELOPE_NONCE_BYTES:]
    if len(nonce) != ENVELOPE_NONCE_BYTES or not ciphertext:
        raise ValueError("bad envelope")
    key = _derive_envelope_key(private_key_b64, sender_public_key, channel_id)
    try:
        media_key = AESGCM(key).decrypt(nonce, ciphertext, _envelope_aad(channel_id, sender_id, recipient_id, key_id))
    except InvalidTag as exc:
        raise ValueError("bad envelope") from exc
    if len(media_key) != MEDIA_KEY_BYTES:
        raise ValueError("bad media key")
    return media_key
