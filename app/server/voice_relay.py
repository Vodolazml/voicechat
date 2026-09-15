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
            self._channels[channel_id][user_id] = websocket
        if old and old is not websocket:
            try:
                await asyncio.wait_for(old.close(code=1000), timeout=1)
            except Exception:
                pass

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
        async def send(user_id, ws):
            try:
                await asyncio.wait_for(ws.send_bytes(packet), timeout=0.2)
            except Exception:
                await self.leave(channel_id, user_id, ws)
                try:
                    await asyncio.wait_for(ws.close(code=1013), timeout=0.2)
                except Exception:
                    pass
        await asyncio.gather(*(send(user_id, ws) for user_id, ws in recipients))


voice_relay = VoiceRelay()
