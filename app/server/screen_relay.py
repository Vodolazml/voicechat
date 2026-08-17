from __future__ import annotations

import asyncio
from collections import defaultdict
from time import monotonic

from fastapi import WebSocket


class ScreenRelay:
    def __init__(self) -> None:
        self._channels: dict[int, dict[int, WebSocket]] = defaultdict(dict)
        self._viewer_intervals: dict[int, dict[int, float]] = defaultdict(dict)
        self._last_sent: dict[int, dict[int, float]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def join(self, channel_id: int, user_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            old = self._channels[channel_id].get(user_id)
            if old and old is not websocket:
                await old.close(code=1000)
            self._channels[channel_id][user_id] = websocket

    async def leave(self, channel_id: int, user_id: int) -> None:
        async with self._lock:
            users = self._channels.get(channel_id)
            if not users:
                return
            users.pop(user_id, None)
            self._viewer_intervals[channel_id].pop(user_id, None)
            self._last_sent[channel_id].pop(user_id, None)
            if not users:
                self._channels.pop(channel_id, None)
                self._viewer_intervals.pop(channel_id, None)
                self._last_sent.pop(channel_id, None)

    async def set_viewer_interval(self, channel_id: int, user_id: int, interval_ms: int) -> None:
        interval = max(0.0, min(2.0, interval_ms / 1000))
        async with self._lock:
            self._viewer_intervals[channel_id][user_id] = interval

    async def broadcast_frame(self, channel_id: int, sender_id: int, frame: bytes) -> None:
        packet = sender_id.to_bytes(4, "big") + frame
        now = monotonic()
        async with self._lock:
            recipients = []
            for user_id, ws in self._channels.get(channel_id, {}).items():
                if user_id == sender_id:
                    continue
                interval = self._viewer_intervals.get(channel_id, {}).get(user_id, 0)
                last_sent = self._last_sent[channel_id].get(user_id, 0)
                if interval and now - last_sent < interval:
                    continue
                self._last_sent[channel_id][user_id] = now
                recipients.append((user_id, ws))
        stale: list[int] = []
        for user_id, ws in recipients:
            try:
                await ws.send_bytes(packet)
            except Exception:
                stale.append(user_id)
        if stale:
            async with self._lock:
                users = self._channels.get(channel_id, {})
                for user_id in stale:
                    users.pop(user_id, None)


screen_relay = ScreenRelay()
