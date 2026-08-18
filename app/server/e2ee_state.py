from __future__ import annotations

import os
from dataclasses import dataclass, field
from threading import RLock
from time import monotonic


# TTL для sender keys (30 минут, после чего ключи автоматически инвалидируются)
SENDER_KEY_TTL_SECONDS = 1800
# Периодическая очистка старых записей (каждые 5 минут)
CLEANUP_INTERVAL_SECONDS = 300


@dataclass
class PublicIdentity:
    public_key: str
    fingerprint: str
    updated_at: float = field(default_factory=monotonic)


@dataclass
class SenderKeySet:
    key_id: str
    envelopes: dict[int, str]
    updated_at: float = field(default_factory=monotonic)

    def is_expired(self) -> bool:
        """Проверяет истечение срока жизни ключа."""
        return monotonic() - self.updated_at > SENDER_KEY_TTL_SECONDS


class E2EEState:
    def __init__(self) -> None:
        self._public_keys: dict[int, PublicIdentity] = {}
        self._sender_keys: dict[int, dict[int, SenderKeySet]] = {}
        self._lock = RLock()
        self._last_cleanup = monotonic()

    def set_public_key(self, user_id: int, public_key: str, fingerprint: str) -> None:
        with self._lock:
            self._public_keys[user_id] = PublicIdentity(public_key=public_key, fingerprint=fingerprint)

    def channel_state(self, channel_id: int, participant_ids: set[int], requester_id: int) -> dict[str, object]:
        with self._lock:
            self._maybe_cleanup()
            
            users = [
                {
                    "user_id": user_id,
                    "public_key": identity.public_key,
                    "fingerprint": identity.fingerprint,
                }
                for user_id, identity in self._public_keys.items()
                if user_id in participant_ids
            ]
            key_sets = []
            for sender_id, key_set in self._sender_keys.get(channel_id, {}).items():
                if sender_id not in participant_ids:
                    continue
                if key_set.is_expired():
                    continue
                envelope = key_set.envelopes.get(requester_id)
                if envelope:
                    key_sets.append(
                        {
                            "sender_id": sender_id,
                            "key_id": key_set.key_id,
                            "envelope": envelope,
                        }
                    )
            return {"users": users, "key_sets": key_sets}

    def set_sender_key(
        self,
        channel_id: int,
        sender_id: int,
        key_id: str,
        envelopes: dict[int, str],
        allowed_recipients: set[int],
    ) -> None:
        """Устанавливает sender key с автоматической очисткой старых записей."""
        clean_envelopes = {
            recipient_id: envelope
            for recipient_id, envelope in envelopes.items()
            if recipient_id in allowed_recipients
        }
        with self._lock:
            self._maybe_cleanup()
            channel_keys = self._sender_keys.setdefault(channel_id, {})

            # Проверяем старый ключ на истечение TTL
            existing = channel_keys.get(sender_id)
            if existing and not existing.is_expired():
                # Обновляем только envelopes, сохраняем key_id если он не старше
                existing.envelopes.update(clean_envelopes)
                existing.updated_at = monotonic()
            else:
                # Создаём новый ключ
                channel_keys[sender_id] = SenderKeySet(
                    key_id=key_id,
                    envelopes=clean_envelopes
                )

    def get_sender_key(
        self,
        channel_id: int,
        sender_id: int,
        requester_id: int,
    ) -> tuple[str, dict[int, str]] | None:
        """Получает активный sender key. Возвращает None если ключ истёк."""
        with self._lock:
            key_set = self._sender_keys.get(channel_id, {}).get(sender_id)
            if key_set is None:
                return None
            if key_set.is_expired():
                # Удаляем истёкший ключ
                self._sender_keys[channel_id].pop(sender_id, None)
                return None
            envelope = key_set.envelopes.get(requester_id)
            if envelope is None:
                return None
            return key_set.key_id, {requester_id: envelope}

    def invalidate_channel_keys(self, channel_id: int) -> list[int]:
        """Инвалидирует все sender keys для канала (при выходе участника)."""
        with self._lock:
            expired_senders = []
            for sender_id, key_set in self._sender_keys.get(channel_id, {}).items():
                key_set.updated_at = 0  # Принудительное истечение
                expired_senders.append(sender_id)
            return expired_senders

    def _maybe_cleanup(self) -> None:
        """Периодически очищает старые записи."""
        now = monotonic()
        if now - self._last_cleanup < CLEANUP_INTERVAL_SECONDS:
            return
        self._last_cleanup = now

        channels_to_remove = []
        for channel_id, sender_keys in list(self._sender_keys.items()):
            self._sender_keys[channel_id] = {
                sender_id: key_set
                for sender_id, key_set in sender_keys.items()
                if not key_set.is_expired()
            }
            if not self._sender_keys[channel_id]:
                channels_to_remove.append(channel_id)

        for channel_id in channels_to_remove:
            self._sender_keys.pop(channel_id, None)

    def clear_channel(self, channel_id: int) -> None:
        """Очищает все данные для канала включая sender keys."""
        with self._lock:
            self._sender_keys.pop(channel_id, None)

    def get_expired_senders(self, channel_id: int) -> list[int]:
        """Возвращает список sender_id с истёкшими ключами для мониторинга."""
        with self._lock:
            return [
                sender_id
                for sender_id, key_set in self._sender_keys.get(channel_id, {}).items()
                if key_set.is_expired()
            ]


e2ee_state = E2EEState()
