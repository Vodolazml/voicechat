"""Live event stream from the server (/events) with automatic reconnect.

Events arrive on a background thread and are passed to `on_event`. After every (re)connect the
caller gets a synthetic {"type": "connected"} so it can refetch state it may have missed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import ssl
import threading
from typing import Any, Callable
from urllib.parse import urlparse

logger = logging.getLogger("voicechat.events")


def events_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/events"


class EventStream:
    def __init__(self, base_url: str, token_provider: Callable[[], str],
                 on_event: Callable[[dict[str, Any]], None],
                 on_status: Callable[[bool], None] = lambda online: None,
                 refresh_token: Callable[[], None] = lambda: None) -> None:
        self.url = events_url(base_url)
        self.token_provider = token_provider
        self.refresh_token = refresh_token
        self.on_event = on_event
        self.on_status = on_status
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._socket = None
        self.online = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="events", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        loop, socket = self._loop, self._socket
        if loop and socket:
            try:
                asyncio.run_coroutine_threadsafe(socket.close(), loop)
            except RuntimeError:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self._main())
        finally:
            loop.close()

    def _set_online(self, online: bool) -> None:
        if online != self.online:
            self.online = online
            self.on_status(online)

    async def _main(self) -> None:
        import websockets

        delay = 1.0
        while not self._stop.is_set():
            reason = ""
            try:
                ssl_context = ssl.create_default_context() if self.url.startswith("wss://") else None
                async with websockets.connect(self.url, ssl=ssl_context, open_timeout=10, ping_interval=20,
                                              ping_timeout=20, max_size=1 << 20) as socket:
                    self._socket = socket
                    await socket.send(json.dumps({"type": "auth", "token": self.token_provider()}))
                    hello = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
                    if hello.get("type") != "hello":
                        raise ConnectionError("no hello")
                    delay = 1.0
                    self._set_online(True)
                    self.on_event({"type": "connected"})
                    async for raw in socket:
                        event = json.loads(raw)
                        kind = event.get("type")
                        if kind == "ping":
                            continue
                        if kind in {"reauth", "resync"}:
                            reason = kind
                            break
                        self.on_event(event)
                        if kind == "session.ended":
                            reason = kind
                            break
            except Exception as exc:
                code = getattr(getattr(exc, "rcvd", None), "code", None)
                reason = "auth" if code == 4401 else type(exc).__name__
                logger.info("Event stream dropped: %s", reason)
            finally:
                self._socket = None
            self._set_online(False)
            if self._stop.is_set():
                break
            if reason in {"auth", "reauth", "session.ended"}:
                try:
                    await asyncio.to_thread(self.refresh_token)
                except Exception as exc:
                    logger.info("Token refresh for events failed")
                    if getattr(exc, "status_code", None) == 401:
                        self.on_event({"type": "signed_out"})
                        return
            if reason in {"reauth", "resync"}:
                continue
            await asyncio.sleep(delay + random.random() * 0.5)
            delay = min(delay * 2, 20.0)
