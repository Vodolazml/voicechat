from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from time import monotonic


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


class E2EEState:
    def __init__(self) -> None:
        self._public_keys: dict[int, PublicIdentity] = {}
        self._sender_keys: dict[int, dict[int, SenderKeySet]] = {}
        self._lock = RLock()

    def set_public_key(self, user_id: int, public_key: str, fingerprint: str) -> None:
        with self._lock:
            self._public_keys[user_id] = PublicIdentity(public_key=public_key, fingerprint=fingerprint)

    def channel_state(self, channel_id: int, participant_ids: set[int], requester_id: int) -> dict[str, object]:
        with self._lock:
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
        clean_envelopes = {
            recipient_id: envelope
            for recipient_id, envelope in envelopes.items()
            if recipient_id in allowed_recipients
        }
        with self._lock:
            channel_keys = self._sender_keys.setdefault(channel_id, {})
            channel_keys[sender_id] = SenderKeySet(key_id=key_id, envelopes=clean_envelopes)

    def clear_channel(self, channel_id: int) -> None:
        with self._lock:
            self._sender_keys.pop(channel_id, None)


e2ee_state = E2EEState()
