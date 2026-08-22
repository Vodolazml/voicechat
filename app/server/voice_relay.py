from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import WebSocket


class VoiceRelay:
    def __init__(self) -> None:
        self._channels: dict[int, dict[int, WebSocket]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def join(self, channel_id: int, user_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            old = self._channels[channel_id].get(user_id)
            if old and old is not websocket:
                await old.close(code=1000)
            self._channels[channel_id][user_id] = websocket

    async def leave(self, channel_id: int, user_id: int, websocket: WebSocket | None = None) -> None:
        async with self._lock:
            users = self._channels.get(channel_id)
            if not users:
                return
            current = users.get(user_id)
            if websocket is not None and current is not websocket:
                return
            users.pop(user_id, None)
            if not users:
                self._channels.pop(channel_id, None)

    async def broadcast_audio(self, channel_id: int, sender_id: int, pcm: bytes) -> None:
        packet = sender_id.to_bytes(4, "big") + pcm
        async with self._lock:
            recipients = [
                (user_id, ws)
                for user_id, ws in self._channels.get(channel_id, {}).items()
                if user_id != sender_id
            ]
        stale: list[int] = []
        for user_id, ws in recipients:
            try:
                await ws.send_bytes(packet)
            except RuntimeError:
                stale.append(user_id)
        if stale:
            async with self._lock:
                users = self._channels.get(channel_id, {})
                for user_id in stale:
                    users.pop(user_id, None)


voice_relay = VoiceRelay()
