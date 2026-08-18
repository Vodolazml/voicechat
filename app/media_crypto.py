from __future__ import annotations

import os
import struct
from threading import Lock

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ENCRYPTED_FRAME_PREFIX = b"PVCM1"
NONCE_BYTES = 12
KEY_BYTES = 32
SEQUENCE_NUM_BYTES = 8


class ReplayProtectedCrypto:
    """Шифрование с защитой от replay-атак через отслеживание sequence numbers.
    
    Использует monotonic counter для предотвращения повторного использования
    зашифрованных пакетов. Каждый ключ имеет свой независимый счётчик.
    """
    
    def __init__(self, key: bytes, direction: str = "recv"):
        if len(key) != KEY_BYTES:
            raise ValueError("media key must be 32 bytes")
        self.key = key
        self.direction = direction  # "send" или "recv"
        self._last_seq: int = 0
        self._lock = Lock()
    
    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes:
        """Шифрует кадр с sequence number в nonce."""
        with self._lock:
            seq = self._next_send_seq()
        nonce = self._make_nonce(seq)
        ciphertext = AESGCM(self.key).encrypt(nonce, plaintext, aad)
        return ENCRYPTED_FRAME_PREFIX + nonce + ciphertext
    
    def decrypt(self, packet: bytes, aad: bytes) -> bytes:
        """Дешифрует кадр с проверкой на replay."""
        if not packet.startswith(ENCRYPTED_FRAME_PREFIX):
            raise ValueError("unencrypted media frame")
        offset = len(ENCRYPTED_FRAME_PREFIX)
        nonce = packet[offset : offset + NONCE_BYTES]
        ciphertext = packet[offset + NONCE_BYTES :]
        if len(nonce) != NONCE_BYTES or not ciphertext:
            raise ValueError("bad encrypted media frame")
        
        seq = self._extract_seq(nonce)
        if not self._check_recv_seq(seq):
            raise ValueError("replay detected")
        
        try:
            return AESGCM(self.key).decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise ValueError("bad encrypted media frame") from exc
    
    def _next_send_seq(self) -> int:
        """Генерирует следующий sequence number для отправки."""
        self._last_seq += 1
        return self._last_seq
    
    def _check_recv_seq(self, seq: int) -> bool:
        """Проверяет sequence number для получения. Отбрасывает старые пакеты."""
        if self.direction != "recv":
            return True
        
        with self._lock:
            if seq <= self._last_seq:
                return False
            self._last_seq = seq
            return True
    
    def _make_nonce(self, seq: int) -> bytes:
        """Создаёт nonce из случайных 8 байт и sequence number (4 байта). Итого 12 байт."""
        return os.urandom(8) + struct.pack(">I", seq)
    
    def _extract_seq(self, nonce: bytes) -> int:
        """Извлекает sequence number из последних 4 байт nonce."""
        return struct.unpack(">I", nonce[-4:])[0]


def encrypt_frame(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    if len(key) != KEY_BYTES:
        raise ValueError("media key must be 32 bytes")
    crypto = ReplayProtectedCrypto(key, direction="send")
    return crypto.encrypt(plaintext, aad)


def decrypt_frame(key: bytes, packet: bytes, aad: bytes) -> bytes:
    if len(key) != KEY_BYTES:
        raise ValueError("media key must be 32 bytes")
    crypto = ReplayProtectedCrypto(key, direction="recv")
    return crypto.decrypt(packet, aad)
