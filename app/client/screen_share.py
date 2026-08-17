from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Union

from PySide6.QtCore import QObject, Signal
import websockets


STOP_FRAME = b"__STOP__"
SCREEN_WS_MAX_BYTES = 3_200_000


class ScreenShareClient(QObject):
    frame_received = Signal(int, bytes)
    stopped_received = Signal(int)
    status_changed = Signal(str)

    def __init__(self, ws_url: str) -> None:
        super().__init__()
        self.ws_url = ws_url
        self._send_queue: queue.Queue[Union[bytes, str]] = queue.Queue(maxsize=4)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="screen-share", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.send_frame(STOP_FRAME)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def send_frame(self, frame: bytes) -> None:
        if not frame:
            return
        self._put_latest(frame)

    def send_viewer_settings(self, fps_interval_ms: int, quality_key: str) -> None:
        self._put_latest(
            json.dumps(
                {
                    "type": "viewer_settings",
                    "fps_interval_ms": fps_interval_ms,
                    "quality_key": quality_key,
                },
                ensure_ascii=False,
            )
        )

    def _put_latest(self, item: Union[bytes, str]) -> None:
        try:
            self._send_queue.put_nowait(item)
        except queue.Full:
            try:
                self._send_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._send_queue.put_nowait(item)
            except queue.Full:
                pass

    def _run_loop(self) -> None:
        try:
            asyncio.run(self._socket_loop())
        except Exception as exc:
            if not self._stop.is_set():
                self.status_changed.emit(f"screen error: {exc}")

    async def _socket_loop(self) -> None:
        async with websockets.connect(self.ws_url, max_size=SCREEN_WS_MAX_BYTES) as websocket:
            self.status_changed.emit("screen connected")
            sender = asyncio.create_task(self._send_loop(websocket))
            receiver = asyncio.create_task(self._receive_loop(websocket))
            done, pending = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                task.result()

    async def _send_loop(self, websocket) -> None:
        while not self._stop.is_set():
            try:
                frame = self._send_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.03)
                continue
            await websocket.send(frame)

    async def _receive_loop(self, websocket) -> None:
        async for message in websocket:
            if self._stop.is_set():
                break
            if not isinstance(message, bytes) or len(message) < 4:
                continue
            user_id = int.from_bytes(message[:4], "big")
            frame = message[4:]
            if frame == STOP_FRAME:
                self.stopped_received.emit(user_id)
            else:
                self.frame_received.emit(user_id, frame)
