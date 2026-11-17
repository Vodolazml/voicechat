"""The native voice engine (vcvoice): microphone, processing, the voice connection, mixing and speakers in
one native process. Python never touches an audio frame; it sends commands and gets events, one JSON
object per line over the process's stdin/stdout.

`NativeVoice` keeps the surface of `AudioEngine` that the call and the window use (settings, start/stop,
mute, push-to-talk, the mixer's volumes, stats, level), and adds the voice connection (connect, keys).
If the engine process dies it is started again and told everything it knew.
"""
from __future__ import annotations

import base64
import itertools
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .audio import AudioSettings

logger = logging.getLogger("voicechat.voice")
engine_log = logging.getLogger("voicechat.vcvoice")

EXE = "vcvoice.exe" if os.name == "nt" else "vcvoice"


def engine_path() -> Path | None:
    """Where the engine is: next to the app when installed, in the build folder when developing."""
    if os.environ.get("VOICECHAT_NATIVE_VOICE") == "0":
        return None
    candidates = []
    if os.environ.get("VOICECHAT_VCVOICE"):
        candidates.append(Path(os.environ["VOICECHAT_VCVOICE"]))
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates += [base / EXE, base / "vcvoice" / EXE, Path(sys.executable).parent / EXE]
    root = Path(__file__).resolve().parents[3]
    candidates += [root / "native" / "vcvoice" / "target" / "release" / EXE]
    if os.environ.get("VOICECHAT_NATIVE_VOICE") != "1" and not getattr(sys, "frozen", False):
        # Developing: the Python engine unless the native one is asked for (tests run without it).
        candidates = candidates[:1] if os.environ.get("VOICECHAT_VCVOICE") else []
    for path in candidates:
        if path.is_file():
            return path
    return None


def available() -> bool:
    return engine_path() is not None


class _MixerProxy:
    """What the call and the window set on `audio.mixer`, forwarded to the engine."""

    def __init__(self, voice: "NativeVoice") -> None:
        self._voice = voice
        self._deafened = False
        self.user_volume = _VolumeDict(voice)
        self.muted_users = _MutedSet(voice)

    @property
    def deafened(self) -> bool:
        return self._deafened

    @deafened.setter
    def deafened(self, value: bool) -> None:
        self._deafened = bool(value)
        self._voice._send(cmd="deafen", on=self._deafened)

    def clear(self) -> None:
        pass  # the engine drops everyone's buffers itself when the connection changes

    def remove(self, user_id: int) -> None:
        pass

    def stats(self) -> dict[int, int]:
        return dict(self._voice._last_stats.get("heard", {}))

    def underrun_stats(self) -> dict[int, int]:
        return dict(self._voice._last_stats.get("underruns", {}))

    def play_effect(self, pcm: bytes, always: bool = False, gain: float = 1.0) -> None:
        self._voice.play(pcm, gain=gain, always=always)


class _VolumeDict(dict):
    def __init__(self, voice: "NativeVoice") -> None:
        super().__init__()
        self._voice = voice

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self._voice.settings.user_volumes[int(key)] = float(value)
        self._voice._send_settings()


class _MutedSet(set):
    def __init__(self, voice: "NativeVoice") -> None:
        super().__init__()
        self._voice = voice

    def add(self, user_id) -> None:
        super().add(user_id)
        self._voice._send(cmd="mute_user", user_id=int(user_id), on=True)

    def discard(self, user_id) -> None:
        super().discard(user_id)
        self._voice._send(cmd="mute_user", user_id=int(user_id), on=False)


class NativeVoice:
    native = True

    def __init__(self, settings: AudioSettings, on_frame: Callable[[bytes], None] | None = None,
                 on_problem: Callable[[str], None] = lambda problem: None, path: Path | None = None) -> None:
        self.settings = settings
        self.on_problem = on_problem
        # Voice connection events, set by the call.
        self.on_voice_state: Callable[[str, str], None] = lambda state, reason: None
        self.on_speakers: Callable[[set[int]], None] = lambda ids: None
        self.on_quality: Callable[[int, int], None] = lambda user_id, quality: None
        self.on_key_problem: Callable[[int], None] = lambda user_id: None
        self.on_transmitting: Callable[[bool], None] = lambda on: None
        # Shortcut keys read by the engine (push-to-talk is applied there; the rest comes here).
        self.on_key: Callable[[str, bool], None] = lambda name, down: None
        # Who else shares a screen / a camera in the voice room (the call opens video only then).
        self.on_video: Callable[[set[int], set[int]], None] = lambda screen, camera: None
        self._keys: dict[str, list[int]] = {}
        self.path = path or engine_path()
        if self.path is None:
            raise RuntimeError("The voice engine (vcvoice) is not installed")
        self.mixer = _MixerProxy(self)
        self._muted = False
        self._ptt = False
        self.transmitting = False
        self.input_level_db = -96.0
        self._running = False
        self._connection: dict[str, Any] | None = None
        self._local_key: tuple[str, bytes] | None = None
        self._remote_keys: dict[int, tuple[str, bytes]] = {}
        self._last_stats: dict[str, Any] = {}
        self._replies: dict[int, threading.Event] = {}
        self._reply_data: dict[int, dict] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._stopping = False
        self._restarts = 0
        self._spawn()
        for user_id, volume in settings.user_volumes.items():
            dict.__setitem__(self.mixer.user_volume, user_id, volume)

    # --- process ------------------------------------------------------------------------------------

    def _spawn(self) -> None:
        flags = 0
        if os.name == "nt":
            flags = 0x08000000  # CREATE_NO_WINDOW
        self._proc = subprocess.Popen([str(self.path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, creationflags=flags, bufsize=0)
        proc = self._proc
        threading.Thread(target=self._read_events, args=(proc,), name="vcvoice-events", daemon=True).start()
        threading.Thread(target=self._read_log, args=(proc,), name="vcvoice-log", daemon=True).start()
        self._send_settings()
        if self._muted:
            self._send(cmd="mute", on=True)
        if self.mixer.deafened:
            self._send(cmd="deafen", on=True)
        for user_id in self.mixer.muted_users:
            self._send(cmd="mute_user", user_id=int(user_id), on=True)
        if self._keys:
            self._send(cmd="keys", combos=self._keys)
        if self._running:
            self._send(cmd="start")
        if self._local_key:
            self._send_local_key()
        for user_id in list(self._remote_keys):
            self._send_remote_key(user_id)
        if self._connection:
            self._send(cmd="connect", **self._connection)

    def _read_log(self, proc: subprocess.Popen) -> None:
        for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            level, _, rest = line.partition(" ")
            if level not in ("ERROR", "WARN", "INFO"):
                # WebRTC's own C++ logging (codec collisions, socket errors...): noise unless debugging.
                engine_log.debug("%s", line)
                continue
            engine_log.log({"ERROR": logging.WARNING, "WARN": logging.WARNING}.get(level, logging.INFO), "%s", rest)

    def _read_events(self, proc: subprocess.Popen) -> None:
        for raw in proc.stdout:
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            try:
                self._handle(event)
            except Exception:
                logger.exception("Voice engine event failed: %s", event)
        code = proc.wait()
        if self._stopping or proc is not self._proc:
            return
        self._restarts += 1
        logger.warning("The voice engine exited (code %s): starting it again (%d)", code, self._restarts)
        if self._restarts <= 5:
            time.sleep(min(5, self._restarts))
            self._spawn()
            self.on_problem("engine_restarted")
        else:
            self.on_problem("engine_failed")

    def _send(self, **message) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        data = (json.dumps(message) + "\n").encode()
        with self._lock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (OSError, ValueError):
                pass  # the reader notices the exit and starts it again

    def _request(self, cmd: str, timeout: float = 2.0) -> dict:
        request_id = next(self._ids)
        done = threading.Event()
        self._replies[request_id] = done
        self._send(cmd=cmd, id=request_id)
        done.wait(timeout)
        self._replies.pop(request_id, None)
        return self._reply_data.pop(request_id, {})

    def _handle(self, event: dict) -> None:
        kind = event.get("ev")
        if kind in ("stats", "devices") and isinstance(event.get("id"), int):
            self._reply_data[event["id"]] = event
            done = self._replies.get(event["id"])
            if done:
                done.set()
        elif kind == "tx":
            self.transmitting = bool(event.get("on"))
            self.on_transmitting(self.transmitting)
        elif kind == "video":
            self.on_video({int(i) for i in event.get("screen", [])}, {int(i) for i in event.get("camera", [])})
        elif kind == "key":
            name, down = str(event.get("name")), bool(event.get("down"))
            if name == "ptt":
                self._ptt = down  # the engine has already applied it
            self.on_key(name, down)
        elif kind == "level":
            self.input_level_db = float(event.get("db", -96.0))
            self.transmitting = bool(event.get("tx", self.transmitting))
        elif kind == "speakers":
            self.on_speakers({int(i) for i in event.get("ids", [])})
        elif kind == "state":
            if event.get("state") == "connected":
                self._restarts = 0
            self.on_voice_state(str(event.get("state")), str(event.get("reason", "")))
        elif kind == "quality":
            self.on_quality(int(event["user_id"]), int(event["quality"]))
        elif kind == "key_problem":
            self.on_key_problem(int(event["user_id"]))
        elif kind == "problem":
            self.on_problem(str(event.get("kind")))
        elif kind == "ready":
            logger.info("Voice engine %s ready (%s)", event.get("version"), self.path)

    def close(self) -> None:
        self._stopping = True
        self._send(cmd="quit")
        proc = self._proc
        if proc is not None:
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()

    # --- AudioEngine surface ------------------------------------------------------------------------

    def _send_settings(self) -> None:
        data = asdict(self.settings)
        data["user_volumes"] = {str(k): float(v) for k, v in self.settings.user_volumes.items()}
        self._send(cmd="settings", settings=data)

    def apply_settings(self, settings: AudioSettings) -> None:
        self.settings = settings
        self._send_settings()

    def start(self) -> None:
        self._running = True
        self._send(cmd="start")

    def stop(self) -> None:
        self._running = False
        self._send(cmd="stop")
        self.transmitting = False

    def restart(self, reinit: bool = True) -> None:
        self._send(cmd="stop")
        self._send(cmd="start")
        self._running = True

    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, value: bool) -> None:
        self._muted = bool(value)
        self._send(cmd="mute", on=self._muted)

    @property
    def ptt_pressed(self) -> bool:
        return self._ptt

    @ptt_pressed.setter
    def ptt_pressed(self, value: bool) -> None:
        if bool(value) != self._ptt:
            self._ptt = bool(value)
            self._send(cmd="ptt", down=self._ptt)

    def set_keys(self, combos: dict[str, tuple[int, ...]]) -> None:
        """Shortcuts to watch ({} stops reading the keyboard)."""
        keys = {name: [int(k) for k in codes] for name, codes in combos.items() if codes}
        if keys != self._keys:
            self._keys = keys
            self._send(cmd="keys", combos=keys)

    def set_level_reports(self, on: bool) -> None:
        self._send(cmd="level", on=bool(on))

    def stats(self) -> dict:
        stats = self._request("stats")
        stats.pop("ev", None)
        stats.pop("id", None)
        stats["heard"] = {int(k): v for k, v in stats.get("heard", {}).items()}
        stats["underruns"] = {int(k): v for k, v in stats.get("underruns", {}).items()}
        stats.setdefault("running", self._running)
        stats.setdefault("mic_frames", 0)
        stats.setdefault("sent_frames", 0)
        stats.setdefault("mic_peak_db", -96.0)
        stats.setdefault("threshold_db", self.settings.threshold_db)
        self._last_stats = stats
        return stats

    def devices(self) -> tuple[list[str], list[str]]:
        reply = self._request("devices", timeout=5.0)
        return list(reply.get("inputs", [])), list(reply.get("outputs", []))

    def play(self, pcm: bytes, gain: float = 1.0, always: bool = False) -> None:
        self._send(cmd="sound", pcm=base64.b64encode(pcm).decode(), gain=gain, always=always)

    def add_reverse(self, frame: bytes) -> None:
        pass  # UI sounds are mixed inside the engine, so the echo canceller hears them already

    # --- voice connection -----------------------------------------------------------------------------

    def connect(self, url: str, token: str, my_id: int) -> None:
        self._connection = {"url": url, "token": token, "my_id": int(my_id)}
        self._send(cmd="connect", **self._connection)

    def disconnect(self) -> None:
        self._connection = None
        self._remote_keys.clear()
        self._send(cmd="disconnect")

    def _send_local_key(self) -> None:
        key_id, key = self._local_key
        self._send(cmd="local_key", id=key_id, key=base64.b64encode(key).decode())

    def _send_remote_key(self, user_id: int) -> None:
        key_id, key = self._remote_keys[user_id]
        self._send(cmd="remote_key", user_id=int(user_id), id=key_id, key=base64.b64encode(key).decode())

    def set_local_key(self, key_id: str, key: bytes) -> None:
        if self._local_key != (key_id, key):
            self._local_key = (key_id, key)
            self._send_local_key()

    def set_remote_key(self, user_id: int, key_id: str, key: bytes) -> None:
        if self._remote_keys.get(user_id) != (key_id, key):
            self._remote_keys[user_id] = (key_id, key)
            self._send_remote_key(user_id)
