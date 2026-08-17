from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ENCRYPTED_FRAME_PREFIX = b"PVCM1"
NONCE_BYTES = 12
KEY_BYTES = 32


def encrypt_frame(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    if len(key) != KEY_BYTES:
        raise ValueError("media key must be 32 bytes")
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return ENCRYPTED_FRAME_PREFIX + nonce + ciphertext


def decrypt_frame(key: bytes, packet: bytes, aad: bytes) -> bytes:
    if len(key) != KEY_BYTES:
        raise ValueError("media key must be 32 bytes")
    if not packet.startswith(ENCRYPTED_FRAME_PREFIX):
        raise ValueError("unencrypted media frame")
    offset = len(ENCRYPTED_FRAME_PREFIX)
    nonce = packet[offset : offset + NONCE_BYTES]
    ciphertext = packet[offset + NONCE_BYTES :]
    if len(nonce) != NONCE_BYTES or not ciphertext:
        raise ValueError("bad encrypted media frame")
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise ValueError("bad encrypted media frame") from exc
