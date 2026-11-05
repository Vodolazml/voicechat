"""End-to-end media keys for one call.

Each participant encrypts their media with their own random key and hands it to every other channel
member as an X25519/HKDF/AES-GCM envelope via the server; the server never sees a usable key.

Trust: the first time we see a friend's device key we remember its fingerprint (like SSH/Signal).
If it later changes, `on_identity_changed` fires so the UI can warn. When someone leaves, our key
rotates, so a person who left cannot decrypt what is said afterwards.
"""
from __future__ import annotations

import hashlib
import threading
from typing import Any, Callable

from app.client.e2ee import ChannelE2EE, E2EEIdentity


def safety_code(fingerprint_a: str, fingerprint_b: str) -> str:
    """Same 20-digit code on both devices; read it aloud to verify nobody is in the middle."""
    first, second = sorted([fingerprint_a, fingerprint_b])
    digest = hashlib.sha256(f"voicechat-safety|{first}|{second}".encode()).digest()
    number = int.from_bytes(digest[:10], "big") % 10**20
    text = f"{number:020d}"
    return " ".join(text[i:i + 5] for i in range(0, 20, 5))


class KeyManager:
    def __init__(self, api, identity: E2EEIdentity, settings: dict[str, Any], my_id: int,
                 save_settings: Callable[[], None],
                 on_identity_changed: Callable[[int, str, str], None] = lambda uid, old, new: None) -> None:
        self.api = api
        self.identity = identity
        self.settings = settings
        self.my_id = my_id
        self.save_settings = save_settings
        self.on_identity_changed = on_identity_changed
        self._lock = threading.RLock()
        self._channels: dict[int, ChannelE2EE] = {}
        self.fingerprints: dict[int, str] = {}
        self.registered = False

    @property
    def known(self) -> dict[str, str]:
        return self.settings.setdefault("known_device_keys", {})

    def register_device(self) -> None:
        self.api.set_device_key(self.identity.public_key, self.identity.fingerprint)
        self.registered = True

    def channel(self, channel_id: int) -> ChannelE2EE:
        with self._lock:
            return self._channels.setdefault(channel_id, ChannelE2EE())

    def outgoing_key(self, channel_id: int) -> bytes:
        return self.channel(channel_id).ensure_outgoing_key()[1]

    def key_for(self, channel_id: int, user_id: int) -> bytes | None:
        return self.channel(channel_id).sender_keys.get(user_id)

    def outgoing(self, channel_id: int) -> tuple[str, bytes]:
        return self.channel(channel_id).ensure_outgoing_key()

    def incoming(self, channel_id: int, user_id: int) -> tuple[str, bytes] | None:
        state = self.channel(channel_id)
        key = state.sender_keys.get(user_id)
        return (state.sender_key_ids[user_id], key) if key else None

    def rotate(self, channel_id: int) -> bytes:
        """New outgoing key, e.g. after someone left. Publish with `sync` afterwards."""
        from .media import key_index

        with self._lock:
            state = self.channel(channel_id)
            previous = key_index(state.outgoing_key_id) if state.outgoing_key is not None else None
            while True:
                state.outgoing_key = None
                key_id, key = state.ensure_outgoing_key()
                # A new key in the old slot would replace the old key at once on both sides, and friends
                # who have not fetched it yet would hear nothing until they do.
                if key_index(key_id) != previous:
                    break
            state.published_recipients.clear()
            state.published_identities.clear()
            return key

    def forget(self, channel_id: int) -> None:
        with self._lock:
            self._channels.pop(channel_id, None)

    def _check_trust(self, users: list[dict[str, Any]]) -> None:
        changed = False
        for user in users:
            user_id = int(user["user_id"])
            fingerprint = str(user.get("fingerprint", ""))
            self.fingerprints[user_id] = fingerprint
            if user_id == self.my_id or not fingerprint:
                continue
            previous = self.known.get(str(user_id))
            if previous is None:
                self.known[str(user_id)] = fingerprint
                changed = True
            elif previous != fingerprint:
                self.known[str(user_id)] = fingerprint
                changed = True
                self.on_identity_changed(user_id, previous, fingerprint)
        if changed:
            self.save_settings()

    def sync(self, channel_id: int) -> bool:
        """Fetch keys from others and publish ours to anyone missing it. Returns True if keys changed."""
        with self._lock:
            if not self.registered:
                self.register_device()
            state = self.channel(channel_id)
            before = dict(state.sender_key_ids)
            data = self.api.e2ee_state(channel_id)
            users = list(data.get("users", []))
            self._check_trust(users)
            users_by_id = {int(user["user_id"]): user for user in users}
            state.load_sender_keys(identity=self.identity, channel_id=channel_id, recipient_id=self.my_id,
                                   users_by_id=users_by_id, key_sets=list(data.get("key_sets", [])))
            envelopes = state.envelopes_for(identity=self.identity, channel_id=channel_id, sender_id=self.my_id,
                                            users=users)
            if envelopes:
                key_id, _ = state.ensure_outgoing_key()
                self.api.publish_sender_key(channel_id, key_id, envelopes)
                state.mark_published(set(envelopes))
            return state.sender_key_ids != before

    def safety_code_with(self, user_id: int) -> str | None:
        other = self.fingerprints.get(user_id) or self.known.get(str(user_id))
        return safety_code(self.identity.fingerprint, other) if other else None
