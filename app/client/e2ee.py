from __future__ import annotations

from typing import Any

from app.e2ee_crypto import (
    decrypt_sender_media_key,
    encrypt_sender_media_key,
    generate_media_key,
    generate_private_key_b64,
    media_key_id,
    public_key_b64,
    public_key_fingerprint,
)


class E2EEIdentity:
    def __init__(self, settings: dict[str, Any]) -> None:
        private_key = settings.get("e2ee_identity_private_key")
        if not isinstance(private_key, str):
            private_key = generate_private_key_b64()
            settings["e2ee_identity_private_key"] = private_key
        self.private_key = private_key
        self.public_key = public_key_b64(private_key)
        self.fingerprint = public_key_fingerprint(self.public_key)


class ChannelE2EE:
    def __init__(self) -> None:
        self.outgoing_key: bytes | None = None
        self.outgoing_key_id = ""
        self.published_recipients: set[int] = set()
        self.sender_keys: dict[int, bytes] = {}
        self.sender_key_ids: dict[int, str] = {}

    def ensure_outgoing_key(self) -> tuple[str, bytes]:
        if self.outgoing_key is None:
            self.outgoing_key = generate_media_key()
            self.outgoing_key_id = media_key_id(self.outgoing_key)
            self.published_recipients.clear()
        return self.outgoing_key_id, self.outgoing_key

    def envelopes_for(
        self,
        *,
        identity: E2EEIdentity,
        channel_id: int,
        sender_id: int,
        users: list[dict[str, object]],
    ) -> dict[int, str]:
        key_id, media_key = self.ensure_outgoing_key()
        envelopes: dict[int, str] = {}
        for user in users:
            recipient_id = int(user["user_id"])
            public_key = str(user["public_key"])
            if recipient_id in self.published_recipients:
                continue
            envelopes[recipient_id] = encrypt_sender_media_key(
                private_key_b64=identity.private_key,
                recipient_public_key=public_key,
                media_key=media_key,
                channel_id=channel_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                key_id=key_id,
            )
        return envelopes

    def mark_published(self, recipient_ids: set[int]) -> None:
        self.published_recipients.update(recipient_ids)

    def load_sender_keys(
        self,
        *,
        identity: E2EEIdentity,
        channel_id: int,
        recipient_id: int,
        users_by_id: dict[int, dict[str, object]],
        key_sets: list[dict[str, object]],
    ) -> None:
        for key_set in key_sets:
            sender_id = int(key_set["sender_id"])
            key_id = str(key_set["key_id"])
            if self.sender_key_ids.get(sender_id) == key_id:
                continue
            sender = users_by_id.get(sender_id)
            if not sender:
                continue
            try:
                media_key = decrypt_sender_media_key(
                    private_key_b64=identity.private_key,
                    sender_public_key=str(sender["public_key"]),
                    envelope=str(key_set["envelope"]),
                    channel_id=channel_id,
                    sender_id=sender_id,
                    recipient_id=recipient_id,
                    key_id=key_id,
                )
            except ValueError:
                continue
            self.sender_keys[sender_id] = media_key
            self.sender_key_ids[sender_id] = key_id
