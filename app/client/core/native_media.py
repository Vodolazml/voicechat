"""Video through the native engine (vcvoice): screen shares and cameras, both ways.

`NativeMediaSession` has the surface of `MediaSession` that the call uses (start/stop, watch, screen, camera),
but the engine does the work: it opens the video connection ("u<id>", the same token as before), captures,
encodes (on the graphics card when it can), decrypts and decodes. Pictures reach the window through shared
memory: the engine names a slot, `ShmFrame` describes it, and the window's video hub copies it straight
into a frame the graphics card draws. Python never touches a video frame otherwise, and no LiveKit Python
library is loaded.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from .media import MediaCallbacks

logger = logging.getLogger("voicechat.media")

RETRY_SECONDS = (1, 2, 4, 8, 10)


@dataclass(frozen=True)
class ShmFrame:
    """One picture in the engine's shared memory: packed I420 in slot `slot` of block `name`."""
    name: str
    width: int
    height: int
    slot: int
    size: int


class NativeMediaSession:
    def __init__(self, *, credentials: Callable[[], dict], my_id: int,
                 outgoing_key: Callable[[], tuple[str, bytes]],
                 incoming_key: Callable[[int], tuple[str, bytes] | None],
                 mixer, callbacks: MediaCallbacks, engine, voice: bool = False) -> None:
        self.credentials = credentials
        self.my_id = my_id
        self.cb = callbacks
        self.engine = engine
        self.connected = False
        self._stopped = False
        self._attempt = 0
        self._retry: threading.Timer | None = None
        self._screen = None
        self._camera = None
        self._lock = threading.Lock()

    # --- lifecycle ---------------------------------------------------------------------------------

    def start(self) -> None:
        self.engine.video_listener = self
        threading.Thread(target=self._connect, name="video-token", daemon=True).start()

    def stop(self, timeout: float = 0.0) -> None:
        self._stopped = True
        if self._retry is not None:
            self._retry.cancel()
        if self.engine.video_listener is self:
            self.engine.video_listener = None
        self.engine.video_command("vdisconnect")
        self.connected = False
        self._screen = self._camera = None
        self.cb.on_streams(set())
        self.cb.on_cameras(set())

    def _connect(self) -> None:
        if self._stopped:
            return
        try:
            creds = self.credentials()
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status_code", None) == 503:
                self.cb.on_state("unavailable")
                return
            logger.info("Video token request failed: %s", exc)
            self._schedule_retry()
            return
        if not self._stopped:
            self.engine.video_command("vconnect", url=creds["url"], token=creds["token"], my_id=int(self.my_id))

    def _schedule_retry(self) -> None:
        if self._stopped:
            return
        delay = RETRY_SECONDS[min(self._attempt, len(RETRY_SECONDS) - 1)]
        self._attempt += 1
        timer = threading.Timer(delay, self._connect)
        timer.daemon = True
        self._retry = timer
        timer.start()

    # --- the call's commands -----------------------------------------------------------------------

    def set_muted(self, muted: bool) -> None:
        pass  # the voice is not on this connection

    def refresh_keys(self) -> None:
        pass  # the engine shares the voice connection's keys

    def push_mic_frame(self, frame: bytes) -> None:
        pass

    def watch(self, user_id: int, watching: bool) -> None:
        self.engine.video_command("watch", user_id=int(user_id), on=bool(watching))

    def start_screen(self, capturer) -> None:
        self._screen = capturer
        self.engine.video_command("share", **capturer.command())

    def stop_screen(self) -> None:
        self._screen = None
        self.engine.video_command("share_stop")

    def start_camera(self, capturer) -> None:
        self._camera = capturer
        self.engine.video_command("camera", **capturer.command())

    def stop_camera(self) -> None:
        self._camera = None
        self.engine.video_command("camera_stop")

    # --- engine events (engine reader thread) ------------------------------------------------------

    def on_vstate(self, state: str, reason: str) -> None:
        if self._stopped:
            return
        if state == "connected":
            self.connected = True
            self._attempt = 0
            logger.info("Video connected (engine)")
        elif state == "closed":
            self.connected = False
            if reason != "requested":
                logger.info("Video connection closed (%s): reconnecting", reason)
                self.cb.on_state("reconnecting")
                self._schedule_retry()
                return
        elif state == "reconnecting":
            self.connected = False
        self.cb.on_state(state)

    def on_vtracks(self, screen: set[int], camera: set[int]) -> None:
        if not self._stopped:
            self.cb.on_streams(set(screen))
            self.cb.on_cameras(set(camera))

    def on_vframe(self, key: int, frame: ShmFrame | None) -> None:
        self.cb.on_video_frame(key, frame)

    def on_vstats(self, kind: str, stats: dict[str, Any]) -> None:
        if kind == "screen" and self._screen is not None:
            self._screen.engine_stats = stats

    def on_vproblem(self, kind: str, text: str) -> None:
        logger.warning("Video problem (%s): %s", kind, text)
        if kind == "screen":
            self._screen = None
        elif kind == "camera":
            self._camera = None
        self.cb.on_video_problem(kind, text)


def engine_supports_video(engine) -> bool:
    return bool(getattr(engine, "native", False)) and hasattr(engine, "video_command")


__all__ = ["NativeMediaSession", "ShmFrame", "engine_supports_video"]

