"""Everything the QML interface talks to.

The UI thread never waits on the network: requests run in a small thread pool and their results are
posted back to the UI thread. Server events, call callbacks and media frames arrive on their own
threads and are handed over the same way. Problems become in-app toasts, never modal popups, so a game
in fullscreen is never minimised by this app.
"""
from __future__ import annotations

import html
import json
import logging
import os
import platform as py_platform
import re
import sys
import time
import wave
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import Property, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from app.client.api import ApiClient, ApiError, default_device_name
from app.client.client_config import load_client_config
from app.client.core.audio import AudioSettings, list_devices
from app.client.core.call import CONNECTED, IDLE, CallController, CallListener
from app.client.core.dsp import FRAME_BYTES, SAMPLE_RATE, RNNoise
from app.client.core.effects import SoundPlayer
from app.client.core.events import EventStream
from app.client.core.keys import KeyManager
from app.client.e2ee import E2EEIdentity
from app.client.settings_store import SETTINGS_PATH, load_client_settings, save_client_settings
from app.version import APP_VERSION

from . import platform as native
from .models import ListModel
from .screen import PRESETS, ScreenCapturer, list_monitors, list_windows, video_hub
from app.client.core.media import camera_key

logger = logging.getLogger("voicechat.ui")

LOG_DIR = SETTINGS_PATH.parent / "logs"
AVATAR_COLORS = ["#7c5cff", "#ff5c8a", "#22c3a6", "#ffb020", "#3ea6ff", "#ff7849", "#a66bff", "#2fd67b",
                 "#ef5da8", "#5ad1e6"]
TOKEN_RE = re.compile(r"(https?://[^\s<>\"']+)|@([A-Za-z0-9_.-]{3,64})")

UI_DEFAULTS: dict[str, Any] = {
    "key_ptt": "", "key_mute": "Ctrl+Shift+M", "key_deafen": "Ctrl+Shift+D", "key_overlay": "Ctrl+Shift+O",
    "overlay_enabled": False, "overlay_corner": "top-left", "overlay_scale": 1.0, "overlay_opacity": 0.5,
    "overlay_text_opacity": 1.0,
    "overlay_speakers_only": False,
    "sound_join_leave": True, "sound_messages": True, "notify_mentions": True, "voice_prompts": True,
    "overlay_x": 0.02, "overlay_y": 0.03,
    "accent": "#7c5cff", "compact": False, "close_to_tray": True, "autostart": False,
    "screen_preset": "1080p60", "screen_monitor": 1, "camera_id": "",
}


def avatar_color(user_id: int) -> str:
    return AVATAR_COLORS[(user_id * 7) % len(AVATAR_COLORS)]


def initials(name: str) -> str:
    words = [w for w in re.split(r"\s+", name.strip()) if w]
    if not words:
        return "?"
    return (words[0][0] + (words[1][0] if len(words) > 1 else "")).upper()


def render_message(body: str, my_username: str) -> tuple[str, bool]:
    """Escaped rich text with clickable links and highlighted mentions; also says if we are mentioned."""
    out: list[str] = []
    pos = 0
    mentioned = False
    for match in TOKEN_RE.finditer(body):
        out.append(html.escape(body[pos:match.start()]))
        if match.group(1):
            url = match.group(1).rstrip(".,);!?:")
            tail = match.group(1)[len(url):]
            out.append(f'<a href="{html.escape(url, quote=True)}" style="color:#6db3ff; text-decoration:none">'
                       f'{html.escape(url)}</a>{html.escape(tail)}')
        else:
            name = match.group(2)
            mine = name.lower() == my_username.lower()
            mentioned = mentioned or mine
            color = "#ffd166" if mine else "#b4a5ff"
            out.append(f'<b><font color="{color}">@{html.escape(name)}</font></b>')
        pos = match.end()
    out.append(html.escape(body[pos:]))
    return "".join(out).replace("\n", "<br>"), mentioned


def _parse_time(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone()


def _day_title(moment: datetime) -> str:
    today = datetime.now().astimezone().date()
    delta = (today - moment.date()).days
    if delta == 0:
        return "Сегодня"
    if delta == 1:
        return "Вчера"
    return moment.strftime("%d.%m.%Y")


def _chime(frequencies: tuple[float, ...], note_ms: int = 80, volume: float = 0.22) -> bytes:
    parts = []
    for freq in frequencies:
        t = np.arange(int(SAMPLE_RATE * note_ms / 1000)) / SAMPLE_RATE
        envelope = np.minimum(1.0, t * 200) * np.exp(-t * 18)
        tone = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t)
        parts.append(tone * envelope)
    pcm = (np.concatenate(parts) * volume * 32767).astype(np.int16).tobytes()
    pad = (-len(pcm)) % FRAME_BYTES
    return pcm + bytes(pad)


_LIMITS = {"cpu": "не хватает процессора", "bandwidth": "не хватает сети", "other": "ограничение кодека"}


def describe_sent(stats: dict) -> tuple[str, str]:
    """Our own share, as measured: what the encoder really produces and sends, and what feeds it."""
    source = f"{stats.get('source_width', 0)}×{stats.get('source_height', 0)}"
    size = f"{stats.get('sent_width') or stats.get('width', 0)}×{stats.get('sent_height') or stats.get('height', 0)}"
    if source != size and stats.get("source_width"):
        size = f"{source} → {size}"
    if "encoded_fps" not in stats:
        text = f"{size} · кодер запускается"
    elif stats.get("network_fps", 0) < 0.5 and stats.get("kbps", 0) < 50:
        # LiveKit pauses a stream nobody is watching (dynacast), so nothing leaves the computer.
        text = f"{size} · нет зрителей, отправка на паузе"
    else:
        text = f"{size} · {stats.get('network_fps', 0):.0f} к/с"
    detail = [f"захват {stats.get('capture_fps', 0):.0f} к/с"]
    if stats.get("sent_width") and stats.get("encoded_fps"):
        detail.append(f"кодер {stats['encoded_fps']:.0f} к/с")
    if stats.get("kbps"):
        detail.append(f"{stats['kbps'] / 1000:.1f} Мбит/с")
    if stats.get("limited_by"):
        detail.append(_LIMITS.get(stats["limited_by"], stats["limited_by"]))
    if stats.get("backend") == "mss":
        detail.append("медленный захват")
    return text, " · ".join(detail)


SOUNDS = {
    "join": _chime((587.0, 784.0, 1175.0), 90, 0.32),
    "leave": _chime((1175.0, 784.0, 523.0), 90, 0.32),
    "message": _chime((988.0,), 60, 0.16),
    "mute": _chime((523.0,), 45, 0.14),
    "unmute": _chime((784.0,), 45, 0.14),
}


def wake_tune() -> bytes:
    """About seven seconds of a bright, bouncy tune that is hard to sleep through (our own melody)."""
    notes = [784, 988, 1175, 988, 784, 988, 1319, 0, 1175, 988, 1175, 1568, 1319, 0, 1175, 988,
             784, 988, 1175, 1319, 1568, 1319, 1175, 0, 988, 1175, 988, 784, 988, 784, 1568, 0]
    beat = 0.21
    rate = SAMPLE_RATE
    parts = []
    for index, freq in enumerate(notes):
        t = np.arange(int(rate * beat)) / rate
        if not freq:
            parts.append(np.zeros_like(t))
            continue
        envelope = np.minimum(1.0, t * 300) * np.exp(-t * 7)
        tone = (np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(4 * np.pi * freq * t)
                + 0.15 * np.sin(6 * np.pi * freq * t))
        bass = 0.4 * np.sin(2 * np.pi * (freq / 4) * t) * np.exp(-t * 5) if index % 2 == 0 else 0
        parts.append((tone + bass) * envelope)
    pcm = (np.concatenate(parts) * 0.26 * 32767).clip(-32767, 32767).astype(np.int16).tobytes()
    return pcm + bytes((-len(pcm)) % FRAME_BYTES)


VOICE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3])) / "assets" / "voice"
# Spoken prompts for the mic and headphone buttons ("Микрофон выключен" ...); the chimes are the fallback.
PROMPTS = {"mic_on": "unmute", "mic_off": "mute", "sound_on": "unmute", "sound_off": "mute"}


def load_prompt(path: Path) -> bytes | None:
    """A WAV file as 48 kHz mono 16-bit PCM padded to whole 10 ms frames, or None if it can't be read."""
    try:
        with wave.open(str(path), "rb") as wav:
            channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
            raw = wav.readframes(wav.getnframes())
    except (OSError, EOFError, wave.Error):
        return None
    if width != 2 or not raw:
        return None
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        target = np.arange(0, len(samples) * SAMPLE_RATE / rate) * rate / SAMPLE_RATE
        samples = np.interp(target, np.arange(len(samples)), samples)
    pcm = np.clip(samples, -32768, 32767).astype(np.int16).tobytes()
    return pcm + bytes((-len(pcm)) % FRAME_BYTES)


# Recorded announcements that replace the join/leave chimes when the files are there.
ANNOUNCEMENTS = ("join", "leave")
SPOKEN: set[str] = set()  # sounds loaded from recordings (played louder than the synthetic chimes)


def _load_prompts() -> None:
    for name, fallback in PROMPTS.items():
        pcm = load_prompt(VOICE_DIR / f"{name}.wav")
        SOUNDS[name] = pcm or SOUNDS[fallback]
        if pcm:
            SPOKEN.add(name)
    for name in ANNOUNCEMENTS:
        pcm = load_prompt(VOICE_DIR / f"{name}.wav")
        if pcm:
            SOUNDS[name] = pcm
            SPOKEN.add(name)


_load_prompts()
SOUNDS["wake"] = wake_tune()


def _newer(latest: str, current: str) -> bool:
    def parts(text: str) -> tuple[int, ...]:
        return tuple(int(p) for p in re.findall(r"\d+", text)[:4])
    return parts(latest) > parts(current)


class Backend(QObject):
    # notifications to QML
    viewChanged = Signal()
    busyChanged = Signal()
    errorChanged = Signal()
    meChanged = Signal()
    spaceChanged = Signal()
    callChanged = Signal()
    localChanged = Signal()
    chatChanged = Signal()
    settingsChanged = Signal()
    devicesChanged = Signal()
    levelChanged = Signal()
    onlineChanged = Signal()
    sessionsChanged = Signal()
    safetyChanged = Signal()
    updateChanged = Signal()
    focusChanged = Signal()
    videoInfoChanged = Signal()
    showWindowRequested = Signal()
    inviteCreated = Signal(str)
    membersChanged = Signal()
    nicknameRequested = Signal(int)
    _post = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._post.connect(lambda fn: fn())
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="api")
        self.client_settings = load_client_settings()
        self.ui = {**UI_DEFAULTS, **self.client_settings.get("ui_settings", {})}
        self.audio_settings = self._load_audio_settings()
        config = load_client_config()
        self._server_locked = bool(config.lock_server_url and config.server_url)
        self._server_url = config.server_url or self.client_settings.get("remembered_server_url", "")
        self.api = ApiClient(on_refresh_token=self._persist_refresh)
        self.keys: KeyManager | None = None
        self.call: CallController | None = None
        self.events: EventStream | None = None
        # UI sounds on a stream of their own: the call reopening its devices no longer cuts them off.
        # One worker, so sounds keep their order and opening the device never blocks the window.
        self.sounds = SoundPlayer(lambda: self.audio_settings.output_device,
                                  lambda: self.audio_settings.output_volume, self._echo_reference)
        self._sound_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sounds")
        self._announced_channel = 0  # the call whose "joined" announcement already played

        self._view = "loading"
        self._busy = False
        self._error = ""
        self._me: dict[str, Any] = {}
        self._online = False
        self._space_id = 0
        self._space_name = ""
        self._spaces_list: list[dict] = []
        self._channels: list[dict] = []
        self._voice: dict[int, dict] = {}          # user_id -> voice state
        self._voice_generation = 0                 # bumped by every live presence event
        self._unread: dict[int, int] = {}
        self._call_state = IDLE
        self._call_channel = 0
        self._speakers: set[int] = set()
        self._streams: set[int] = set()
        self._watching: set[int] = set()
        self._quality: dict[int, int] = {}
        self._key_changed: set[int] = set()
        self._muted = False
        self._deafened = False
        self._sharing = False
        self._ping = 0
        self._chat_channel = 0
        self._text_channel = 0
        self._oldest_loaded = 0
        self._has_more = False
        self._level = 0.0
        self._transmitting = False
        self._mic_test = False
        self._focus_user = 0
        self._sessions: list[dict] = []
        self._safety: list[dict] = []
        self._update: dict[str, Any] = {}
        self._update_progress = -1.0
        self._inputs, self._outputs = [], []
        self._toast_seq = 0

        self.spaces = ListModel(["id", "name", "initials", "unread", "mine", "color", "avatar"])
        self.channels = ListModel(["id", "name", "type", "user_limit", "is_private", "unread", "members",
                                   "memberCount", "active", "selected"])
        self.stage = ListModel(["id", "name", "initials", "color", "speaking", "muted", "deafened",
                                "serverMuted", "streaming", "watching", "volume", "localMuted", "quality",
                                "isMe", "keyChanged", "videoInfo", "videoDetail", "avatar", "camera",
                                "cameraKey", "canWake"])
        self.messages = ListModel(["id", "authorId", "author", "initials", "color", "html", "raw", "time",
                                   "day", "showDay", "edited", "deleted", "mine", "mention", "grouped", "avatar"])
        self.toasts = ListModel(["id", "level", "text"])

        self._keys_poller = native.KeyPoller()
        self._apply_hotkeys()
        self._key_timer = QTimer(self, interval=15, timeout=self._poll_keys)
        self._level_timer = QTimer(self, interval=60, timeout=self._poll_level)
        self._ping_timer = QTimer(self, interval=10_000, timeout=self._measure_ping)
        # Real (measured) resolution and frame rate of streams, refreshed once a second.
        self._video_timer = QTimer(self, interval=1000, timeout=self._update_video_info)
        self._capturer: ScreenCapturer | None = None
        self._camera = None                        # CameraCapturer while our camera is on
        self._cameras: set[int] = set()            # who has a camera on in our call
        self._avatars: dict[int, int] = {}         # user id -> picture version (0: none)
        self._members: list[dict] = []             # people in the current space
        self._members_raw: list[dict] = []
        self._wake_until: dict[int, float] = {}    # user id -> when they can be woken again
        self._video_info: dict[int, tuple[str, str]] = {}
        QTimer.singleShot(0, self._startup)

    # =========================================================================================
    # helpers
    # =========================================================================================

    def _ui(self, fn: Callable[[], None]) -> None:
        """Run on the UI thread (safe from any thread)."""
        self._post.emit(fn)

    def _bg(self, work: Callable[[], Any], done: Callable[[Any], None] | None = None,
            failed: Callable[[Exception], None] | None = None) -> None:
        def run() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - shown to the user as a toast
                logger.info("Request failed: %s", exc)
                self._ui(lambda error=exc: (failed or self._toast_error)(error))
                return
            if done is not None:
                self._ui(lambda: done(result))
        self._pool.submit(run)

    def _toast_error(self, exc: Exception) -> None:
        self.toast("error", str(exc) or "Что-то пошло не так")

    @Slot(str, str)
    def toast(self, level: str, text: str) -> None:
        self._toast_seq += 1
        toast_id = self._toast_seq
        self.toasts.append({"id": toast_id, "level": level, "text": text})
        while self.toasts.count > 4:
            self.toasts.remove(self.toasts.rows[0]["id"])
        QTimer.singleShot(5200 if level == "error" else 3800, lambda: self.toasts.remove(toast_id))

    @Slot(int)
    def dismissToast(self, toast_id: int) -> None:
        self.toasts.remove(toast_id)

    # --- names: your own, and the ones you give other people ---------------------------------

    @property
    def _nicknames(self) -> dict[str, str]:
        """Names you gave other people. Only you see them; kept per server, because ids are per server."""
        every = self.client_settings.setdefault("nicknames", {})
        return every.setdefault(self._server_url or "", {})

    def _name(self, user_id: int, real: str) -> str:
        if user_id == self._me.get("id"):
            return real
        return self._nicknames.get(str(user_id)) or real

    @Slot(int)
    def askNickname(self, user_id: int) -> None:
        self.nicknameRequested.emit(user_id)

    @Slot(int, result=str)
    def nickname(self, user_id: int) -> str:
        return self._nicknames.get(str(user_id), "")

    @Slot(int, result=str)
    def realName(self, user_id: int) -> str:
        state = self._voice.get(user_id)
        if state:
            return state.get("display_name", "")
        member = next((m for m in self._members_raw if m["id"] == user_id), None)
        if member:
            return member.get("display_name", "")
        row = next((r for r in self.messages.rows if r["authorId"] == user_id), None)
        return row.get("authorReal", "") if row else ""

    @Slot(int, str)
    def setNickname(self, user_id: int, name: str) -> None:
        """Empty name: show the person's own name again."""
        name = " ".join((name or "").split())[:60]
        if name and name != self.realName(user_id):
            self._nicknames[str(user_id)] = name
        else:
            self._nicknames.pop(str(user_id), None)
        self._save()
        self._names_changed(user_id)

    def _names_changed(self, user_id: int) -> None:
        for row in self.messages.rows:
            if row["authorId"] == user_id:
                author = self._name(user_id, row.get("authorReal", row["author"]))
                self.messages.update(row["id"], author=author, initials=initials(author))
        self._set_members(self._members_raw)
        self._rebuild()

    @Slot(str)
    def setDisplayName(self, name: str) -> None:
        name = " ".join((name or "").split())
        if not name or name == self._me.get("display_name"):
            return

        def done(result):
            self._me = {**self._me, "display_name": result["display_name"],
                        "initials": initials(result["display_name"])}
            self.meChanged.emit()
            self.toast("success", "Имя изменено")
        self._bg(lambda: self.api.update_profile(name), done)

    def _save(self) -> None:
        self.client_settings["ui_settings"] = self.ui
        audio = asdict(self.audio_settings)
        audio["user_volumes"] = {str(k): v for k, v in self.audio_settings.user_volumes.items()}
        self.client_settings["audio_settings"] = audio
        try:
            save_client_settings(self.client_settings)
        except OSError:
            logger.exception("Settings were not saved")

    def _load_audio_settings(self) -> AudioSettings:
        stored = self.client_settings.get("audio_settings", {})
        names = {f.name for f in fields(AudioSettings)}
        values = {k: v for k, v in stored.items() if k in names}
        values["user_volumes"] = {int(k): float(v) for k, v in stored.get("user_volumes", {}).items()}
        try:
            return AudioSettings(**values)
        except TypeError:
            return AudioSettings()

    def _persist_refresh(self, token: str) -> None:
        if self.client_settings.get("remember_me"):
            try:
                from app.client.credential_store import protect_text

                self.client_settings["remembered_refresh_dpapi"] = protect_text(token)
                self.client_settings["remembered_server_url"] = self.api.base_url
                save_client_settings(self.client_settings)
            except Exception:
                logger.info("Refresh token not remembered on this system")

    def _set(self, name: str, value: Any, signal: Signal) -> None:
        if getattr(self, name) != value:
            setattr(self, name, value)
            if name == "_view" and value in ("login", "password", "main"):
                _startup_finished()
            signal.emit()

    # =========================================================================================
    # properties
    # =========================================================================================

    view = Property(str, lambda self: self._view, notify=viewChanged)
    busy = Property(bool, lambda self: self._busy, notify=busyChanged)
    error = Property(str, lambda self: self._error, notify=errorChanged)
    online = Property(bool, lambda self: self._online, notify=onlineChanged)
    version = Property(str, lambda self: APP_VERSION, constant=True)
    serverUrl = Property(str, lambda self: self._server_url, notify=viewChanged)
    serverLocked = Property(bool, lambda self: self._server_locked, constant=True)
    rememberedUsername = Property(str, lambda self: self.client_settings.get("remembered_username", ""),
                                  notify=viewChanged)
    rememberMe = Property(bool, lambda self: bool(self.client_settings.get("remember_me", True)), notify=viewChanged)

    me = Property("QVariantMap", lambda self: self._me, notify=meChanged)
    # The creator of a space manages it; the server owner manages everything.
    canModerate = Property(bool, lambda self: self._manages_space("voice.moderate"), notify=spaceChanged)
    # Moderators manage the space too, but only its creator (or the server owner) creates and deletes channels.
    canManageChannels = Property(bool, lambda self: self._space_admin(), notify=spaceChanged)
    canEditSpace = Property(bool, lambda self: self._manages_space("spaces.update"), notify=spaceChanged)
    spaceRole = Property(str, lambda self: self._current_space().get("role", "member"), notify=spaceChanged)
    spaceAvatar = Property(str, lambda self: self._space_avatar(self._current_space()), notify=spaceChanged)
    spaceShareQuality = Property(str, lambda self: self._current_space().get("max_share_quality") or "1440p60",
                                 notify=spaceChanged)
    cameraAllowed = Property(bool, lambda self: bool(self._current_space().get("camera_allowed", True)),
                             notify=spaceChanged)
    spaceMembers = Property("QVariantList", lambda self: self._members, notify=membersChanged)
    myAvatar = Property(str, lambda self: self._avatar_url(self._me.get("id", 0)), notify=meChanged)
    cameraOn = Property(bool, lambda self: self._camera is not None, notify=callChanged)
    canInvite = Property(bool, lambda self: bool(self._current_space().get("invite_code")), notify=spaceChanged)
    spaceInviteCode = Property(str, lambda self: self._current_space().get("invite_code") or "", notify=spaceChanged)
    spaceIsMine = Property(bool, lambda self: bool(self._current_space().get("is_mine")), notify=spaceChanged)
    spaceMemberCount = Property(int, lambda self: int(self._current_space().get("member_count", 0)),
                                notify=spaceChanged)
    isOwner = Property(bool, lambda self: bool(self._me.get("is_owner")), notify=meChanged)
    hasSpaces = Property(bool, lambda self: bool(self._spaces_list), notify=spaceChanged)
    canShare = Property(bool, lambda self: "screen_share.start" in self._me.get("permissions", []), notify=meChanged)

    spaceId = Property(int, lambda self: self._space_id, notify=spaceChanged)
    spaceName = Property(str, lambda self: self._space_name, notify=spaceChanged)

    callState = Property(str, lambda self: self._call_state, notify=callChanged)
    callChannelId = Property(int, lambda self: self._call_channel, notify=callChanged)
    callChannelName = Property(str, lambda self: self._channel_name(self._call_channel), notify=callChanged)
    inCall = Property(bool, lambda self: self._call_channel != 0, notify=callChanged)
    ping = Property(int, lambda self: self._ping, notify=callChanged)
    sharing = Property(bool, lambda self: self._sharing, notify=callChanged)
    myQuality = Property(int, lambda self: self._quality.get(self._me.get("id", 0), 2), notify=callChanged)

    muted = Property(bool, lambda self: self._muted, notify=localChanged)
    deafened = Property(bool, lambda self: self._deafened, notify=localChanged)
    serverMuted = Property(bool, lambda self: bool(self._voice.get(self._me.get("id", 0), {}).get("server_muted")),
                           notify=localChanged)
    transmitting = Property(bool, lambda self: self._transmitting, notify=levelChanged)
    micLevel = Property(float, lambda self: self._level, notify=levelChanged)

    chatChannelId = Property(int, lambda self: self._chat_channel, notify=chatChanged)
    chatChannelName = Property(str, lambda self: self._channel_name(self._chat_channel), notify=chatChanged)
    textChannelId = Property(int, lambda self: self._text_channel, notify=chatChanged)
    hasMoreMessages = Property(bool, lambda self: self._has_more, notify=chatChanged)

    focusUserId = Property(int, lambda self: self._focus_user, notify=focusChanged)
    focusVideoInfo = Property(str, lambda self: self._video_info.get(self._focus_user, ("", ""))[0],
                              notify=videoInfoChanged)
    focusVideoDetail = Property(str, lambda self: self._video_info.get(self._focus_user, ("", ""))[1],
                                notify=videoInfoChanged)
    settings = Property("QVariantMap", lambda self: {**self.ui, **{
        k: v for k, v in asdict(self.audio_settings).items() if k != "user_volumes"}}, notify=settingsChanged)
    inputDevices = Property("QVariantList", lambda self: ["", *self._inputs], notify=devicesChanged)
    outputDevices = Property("QVariantList", lambda self: ["", *self._outputs], notify=devicesChanged)
    screenPresets = Property("QVariantList", lambda self: [{"key": p.key, "title": p.title} for p in PRESETS],
                             constant=True)
    strongNoiseAvailable = Property(bool, lambda self: RNNoise.available(), constant=True)
    sessions = Property("QVariantList", lambda self: self._sessions, notify=sessionsChanged)
    safetyCodes = Property("QVariantList", lambda self: self._safety, notify=safetyChanged)
    myFingerprint = Property(str, lambda self: self.keys.identity.fingerprint if self.keys else "", notify=meChanged)
    updateInfo = Property("QVariantMap", lambda self: self._update, notify=updateChanged)
    updateProgress = Property(float, lambda self: self._update_progress, notify=updateChanged)
    updateSizes = Property(str, lambda self: getattr(self, "_update_sizes", ""), notify=updateChanged)

    spacesModel = Property(QObject, lambda self: self.spaces, constant=True)
    channelsModel = Property(QObject, lambda self: self.channels, constant=True)
    stageModel = Property(QObject, lambda self: self.stage, constant=True)
    messagesModel = Property(QObject, lambda self: self.messages, constant=True)
    toastsModel = Property(QObject, lambda self: self.toasts, constant=True)

    def _channel_name(self, channel_id: int) -> str:
        return next((c["name"] for c in self._channels if c["id"] == channel_id), "")

    # =========================================================================================
    # sign in
    # =========================================================================================

    def _startup(self) -> None:
        """The installed app is its own launcher: before anything else it brings itself up to date."""
        if not self._can_self_update():
            self._continue_startup()
            return
        try:
            self.api.set_base_url(self._server_url)
        except Exception:
            self._continue_startup()
            return
        self._set("_view", "updating", self.viewChanged)
        self._watch_update()

        def got(info) -> None:
            if info.update_available and _newer(info.latest_version, APP_VERSION) and info.download_url:
                self._update = {"version": info.latest_version, "required": info.required,
                                "notes": info.release_notes_url, "_info": info}
                self.updateChanged.emit()
                self._install_update(on_failure=self._continue_startup)
            else:
                self._continue_startup()
        # Offline or the server is down: start normally, the update waits for the next launch.
        self._bg(self.api.check_update, got, lambda exc: self._continue_startup())

    def _watch_update(self) -> None:
        """Never stay stuck on the launcher: if the check or the download makes no progress for two minutes,
        start the installed version (the update is tried again next time)."""
        seen = [self._update_progress, time.monotonic()]

        def check() -> None:
            if self._view != "updating":
                timer.stop()
                return
            if self._update_progress != seen[0]:
                seen[0], seen[1] = self._update_progress, time.monotonic()
                return
            if time.monotonic() - seen[1] > 120:
                timer.stop()
                logger.warning("Update stalled at %.0f%%, starting the installed version",
                               max(0.0, self._update_progress) * 100)
                self._update_progress = -1.0
                self.updateChanged.emit()
                self._continue_startup()
        timer = QTimer(self, interval=10_000, timeout=check)
        timer.start()

    def _can_self_update(self) -> bool:
        return (bool(getattr(sys, "frozen", False)) and sys.platform == "win32" and bool(self._server_url)
                and os.environ.get("VOICECHAT_NO_AUTOUPDATE") != "1" and "--no-update" not in sys.argv)

    def _continue_startup(self) -> None:
        if self._can_self_update():
            from app.client.updater import remove_old_installers

            self._pool.submit(remove_old_installers)
        stored = self.client_settings.get("remembered_refresh_dpapi")
        if stored and self._server_url and self.client_settings.get("remember_me"):
            try:
                from app.client.credential_store import unprotect_text

                refresh = unprotect_text(stored)
                self.api.set_base_url(self._server_url)
            except Exception:
                refresh = ""
            if refresh:
                self._set_busy(True)
                self._bg(lambda: self.api.refresh_session(refresh), lambda _: self._signed_in(),
                         lambda exc: self._show_login("" if getattr(exc, "status_code", None) == 401
                                                      else str(exc)))
                return
        self._show_login("")

    def _show_login(self, error: str) -> None:
        self._set_busy(False)
        self._set("_error", error, self.errorChanged)
        self._set("_view", "login", self.viewChanged)

    def _set_busy(self, busy: bool) -> None:
        self._set("_busy", busy, self.busyChanged)

    def _prepare(self, server: str) -> bool:
        try:
            self.api.set_base_url(server if not self._server_locked else self._server_url)
        except ApiError as exc:
            self._set("_error", str(exc), self.errorChanged)
            return False
        self._server_url = self.api.base_url
        self._set("_error", "", self.errorChanged)
        self._set_busy(True)
        return True

    def _remember(self, remember: bool, username: str) -> None:
        self.client_settings["remember_me"] = remember
        self.client_settings["remembered_server_url"] = self.api.base_url
        if remember:
            self.client_settings["remembered_username"] = username
        else:
            for key in ("remembered_username", "remembered_refresh_dpapi"):
                self.client_settings.pop(key, None)
        self.client_settings.pop("remembered_password_dpapi", None)
        self._save()

    @Slot(str, str, str, bool)
    def login(self, server: str, username: str, password: str, remember: bool) -> None:
        if not username.strip() or not password:
            self._set("_error", "Введите логин и пароль", self.errorChanged)
            return
        if not self._prepare(server):
            return
        self._remember(remember, username.strip())
        self._bg(lambda: self.api.login(username.strip(), password), lambda _: self._signed_in(),
                 lambda exc: self._show_login(str(exc)))

    @Slot(str, str, str, str, str, bool, bool)
    def register(self, server: str, code: str, username: str, display_name: str, password: str,
                 remember: bool, accept_terms: bool = False) -> None:
        if not accept_terms:
            self._set("_error", "Чтобы создать аккаунт, примите соглашение и политику конфиденциальности",
                      self.errorChanged)
            return
        if not self._prepare(server):
            return
        self._remember(remember, username.strip())
        self._bg(lambda: self.api.register(code.strip(), username.strip(), display_name.strip(), password, True),
                 lambda _: self._signed_in(), lambda exc: self._show_login(str(exc)))

    def _signed_in(self) -> None:
        if self.client_settings.get("remember_me"):
            self._persist_refresh(self.api.refresh_token)

        def load():
            me = self.api.me()
            # A temporary password must be changed first; the server refuses everything else until then.
            spaces = [] if me.get("must_change_password") else self.api.spaces()
            return me, spaces
        self._bg(load, self._loaded, lambda exc: self._show_login(str(exc)))

    def _loaded(self, result) -> None:
        me, spaces = result
        me["initials"] = initials(me["display_name"])
        me["color"] = avatar_color(me["id"])
        self._avatars[me["id"]] = me.get("avatar_version", 0)
        self._me = me
        self.meChanged.emit()
        self._set_busy(False)
        if me.get("must_change_password"):
            self._set("_view", "password", self.viewChanged)
            return
        self._start_session()
        self._set("_view", "main", self.viewChanged)
        self._set_spaces(spaces)
        self._bg(self.api.check_update, self._got_update, lambda exc: None)

    def _current_space(self) -> dict:
        return next((s for s in self._spaces_list if s["id"] == self._space_id), {})

    def _space_admin(self) -> bool:
        return (self._current_space().get("role") == "admin" or bool(self._me.get("is_owner"))
                or "channels.create" in self._me.get("permissions", []))

    def _avatar_url(self, user_id: int) -> str:
        version = self._avatars.get(user_id, 0)
        return f"image://avatar/user/{user_id}/{version}" if version else ""

    @staticmethod
    def _space_avatar(space: dict) -> str:
        version = space.get("avatar_version", 0)
        return f"image://avatar/space/{space['id']}/{version}" if version and space.get("id") else ""

    def _manages_space(self, permission: str) -> bool:
        return (bool(self._current_space().get("can_manage")) or bool(self._me.get("is_owner"))
                or permission in self._me.get("permissions", []))

    def _set_spaces(self, spaces: list[dict], select: int = 0) -> None:
        self._spaces_list = spaces
        self.spaces.set_items([{"id": s["id"], "name": s["name"], "initials": initials(s["name"]),
                                "color": avatar_color(s["id"] + 3), "avatar": self._space_avatar(s),
                                "unread": 0, "mine": bool(s.get("is_mine"))} for s in spaces])
        ids = {s["id"] for s in spaces}
        wanted = select or self._space_id or self.client_settings.get("last_space_id")
        target = wanted if wanted in ids else (spaces[0]["id"] if spaces else 0)
        if target != self._space_id or not target:
            if not target:
                self._space_id = 0
                self._space_name = ""
                self._channels = []
                self._voice.clear()
                self._text_channel = 0
                self._set_chat(0)
                self._rebuild()
                self.spaceChanged.emit()
            else:
                self.selectSpace(target)
        else:
            self._space_name = next((s["name"] for s in spaces if s["id"] == target), "")
            self.spaceChanged.emit()

    def _reload_spaces(self, select: int = 0) -> None:
        self._bg(self.api.spaces, lambda spaces: self._set_spaces(spaces, select))

    @Slot(str)
    def createSpace(self, name: str) -> None:
        def done(space):
            self.toast("success", f"Пространство «{space['name']}» создано. Код для друзей: {space['invite_code']}")
            self._reload_spaces(space["id"])
        self._bg(lambda: self.api.create_space(name.strip()), done)

    @Slot(str)
    def joinSpace(self, code: str) -> None:
        def done(space):
            self.toast("success", f"Вы вступили в «{space['name']}»")
            self._reload_spaces(space["id"])
        self._bg(lambda: self.api.join_space(code.strip()), done)

    @Slot()
    def rotateInviteCode(self) -> None:
        space_id = self._space_id
        self._bg(lambda: self.api.rotate_invite_code(space_id), lambda _: self._reload_spaces(space_id))

    @Slot()
    def leaveSpace(self) -> None:
        space_id = self._space_id
        if self._call_channel and any(c["id"] == self._call_channel for c in self._channels):
            self.leaveCall()
        self._bg(lambda: self.api.leave_space(space_id), lambda _: self._reload_spaces())

    @Slot()
    def deleteSpace(self) -> None:
        space_id = self._space_id
        if self._call_channel and any(c["id"] == self._call_channel for c in self._channels):
            self.leaveCall()
        self._bg(lambda: self.api.delete_space(space_id), lambda _: self._reload_spaces())

    def _start_session(self) -> None:
        if self.call is not None:
            return
        self.keys = KeyManager(self.api, E2EEIdentity(self.client_settings), self.client_settings,
                               my_id=self._me["id"], save_settings=self._save,
                               on_identity_changed=lambda uid, old, new: self._ui(lambda: self._identity_changed(uid)))
        listener = CallListener(
            on_state=lambda state, channel: self._ui(lambda: self._on_call_state(state, channel)),
            on_speakers=lambda speakers: self._ui(lambda: self._on_speakers(speakers)),
            on_quality=lambda uid, q: self._ui(lambda: self._on_quality(uid, q)),
            on_streams=lambda users: self._ui(lambda: self._on_streams(users)),
            on_video_frame=video_hub().push,
            on_cameras=lambda users: self._ui(lambda: self._on_cameras(users)),
            on_notice=lambda level, text: self._ui(lambda: self.toast(level, text)),
            on_local=lambda flags: None,
        )
        self.call = CallController(self.api, self.keys, self.audio_settings, listener)
        self.events = EventStream(self.api.base_url, lambda: self.api.token or "",
                                  on_event=lambda event: self._ui(lambda: self._on_event(event)),
                                  on_status=lambda online: self._ui(lambda: self._set("_online", online,
                                                                                    self.onlineChanged)),
                                  refresh_token=lambda: self.api.refresh_session(self.api.refresh_token))
        self.events.start()
        self.refreshDevices()

    @Slot(str, str)
    def changePassword(self, old: str, new: str) -> None:
        self._set_busy(True)

        def done(_):
            self._set_busy(False)
            self.toast("success", "Пароль изменён. Войдите с новым паролем")
            self._end_session()
            self._show_login("")
        self._bg(lambda: self.api.change_password(old, new), done,
                 lambda exc: (self._set_busy(False), self._set("_error", str(exc), self.errorChanged)))

    @Slot()
    def acceptConsent(self) -> None:
        version = int(self._me.get("consent_required") or 1)

        def done(_):
            self._me = {**self._me, "consent_version": version}
            self.meChanged.emit()
        self._bg(lambda: self.api.accept_consent(version), done,
                 lambda exc: self.toast("error", f"Не удалось сохранить согласие: {exc}"))

    @Slot(str)
    def deleteAccount(self, password: str) -> None:
        self._set_busy(True)

        def done(_):
            self._set_busy(False)
            self.client_settings.pop("remembered_refresh_dpapi", None)
            self._save()
            self._end_session()
            self._show_login("")
            self.toast("success", "Аккаунт удалён")
        self._bg(lambda: self.api.delete_account(password), done,
                 lambda exc: (self._set_busy(False), self.toast("error", str(exc))))

    @Slot(str, str, result=str)
    def legalUrl(self, server: str, page: str) -> str:
        """Address of /privacy, /terms, /consent or /licenses on `server` (the login screen's field) or,
        when it is empty, on the server we are signed in to."""
        from app.client.api import normalize_base_url

        try:
            base = normalize_base_url(server, self.api.base_url) if server.strip() else self.api.base_url
        except Exception:
            base = self.api.base_url
        return base.rstrip("/") + "/" + page

    @Slot()
    def logout(self) -> None:
        self.client_settings.pop("remembered_refresh_dpapi", None)
        self._save()
        self._end_session()
        self._pool.submit(lambda: self.api.logout())
        self._show_login("")

    def _end_session(self) -> None:
        if self.call is not None:
            self.call.shutdown(timeout=3)
            self.call = None
        if self.events is not None:
            self.events.stop()
            self.events = None
        self._voice.clear()
        self._channels = []
        self._spaces_list = []
        self._space_id = 0
        self._call_channel = 0
        self._call_state = IDLE
        self.callChanged.emit()
        for model in (self.spaces, self.channels, self.stage, self.messages):
            model.clear()

    def shutdown(self) -> None:
        """App exit: leave the call so friends see it immediately."""
        logger.info("Closing VoiceChat")
        native.set_call_active(False)
        self._end_session()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._sound_worker.shutdown(wait=False, cancel_futures=True)
        self.sounds.stop()

    # =========================================================================================
    # spaces, channels, presence
    # =========================================================================================

    @Slot(int)
    def selectSpace(self, space_id: int) -> None:
        self._space_id = space_id
        self._space_name = next((s["name"] for s in self._spaces_list if s["id"] == space_id), "")
        self.client_settings["last_space_id"] = space_id
        self.spaceChanged.emit()
        self._refresh_space()

    def _refresh_space(self) -> None:
        space_id = self._space_id
        if not space_id:
            return

        generation = self._voice_generation

        def load():
            try:
                members = self.api.space_members(space_id)
            except ApiError:
                members = None  # an older server: no member list, no pictures
            return (self.api.channels(space_id), self.api.space_voice(space_id), self.api.unread(space_id),
                    members)

        def done(result):
            if space_id != self._space_id:
                return
            if generation != self._voice_generation:
                # A live event arrived while this snapshot was in flight; it may be older than the event.
                self._refresh_space()
                return
            channels, voice, unread, members = result
            if members is not None:
                self._set_members(members)
            for state in voice:
                self._avatars[state["user_id"]] = state.get("avatar_version", 0)
            my_id = self._me.get("id", 0)
            if my_id in {v["user_id"] for v in voice} and not self._call_channel:
                # Left over from a crash or a killed process: we are not in a call, so leave at once.
                stale = next(v["channel_id"] for v in voice if v["user_id"] == my_id)
                voice = [v for v in voice if v["user_id"] != my_id]
                self._bg(lambda: self.api.disconnect(stale), lambda _: None, lambda exc: None)
            self._channels = sorted(channels, key=lambda c: (c["type"] != "text", c.get("position", 0), c["id"]))
            self._voice = {v["user_id"]: v for v in voice}
            self._unread = {u["channel_id"]: u["count"] for u in unread}
            if not self._text_channel or not any(c["id"] == self._text_channel for c in self._channels):
                if not self._call_channel:
                    first_text = next((c["id"] for c in self._channels if c["type"] == "text"), 0)
                    if first_text:
                        self.openChannel(first_text)
            self._rebuild()
            if self._chat_channel:
                self._load_messages(self._chat_channel)
        self._bg(load, done)

    def _rebuild(self) -> None:
        my_id = self._me.get("id", 0)
        rows = []
        for channel in self._channels:
            members = []
            for state in sorted((v for v in self._voice.values() if v["channel_id"] == channel["id"]),
                                key=lambda v: self._name(v["user_id"], v["display_name"]).lower()):
                uid = state["user_id"]
                name = self._name(uid, state["display_name"])
                in_my_call = channel["id"] == self._call_channel
                members.append({
                    "id": uid, "name": name, "initials": initials(name),
                    "color": avatar_color(uid), "muted": state["muted"] or state.get("server_muted", False),
                    "avatar": self._avatar_url(uid),
                    "deafened": state["deafened"], "speaking": in_my_call and uid in self._speakers,
                    "streaming": in_my_call and uid in self._streams,
                    "reconnecting": state.get("status") == "reconnecting", "isMe": uid == my_id,
                })
            rows.append({
                "id": channel["id"], "name": channel["name"], "type": channel["type"],
                "user_limit": channel.get("user_limit", 0), "is_private": channel.get("is_private", False),
                "unread": self._unread.get(channel["id"], 0), "members": members, "memberCount": len(members),
                "active": channel["id"] == self._call_channel,
                "selected": channel["id"] == (self._text_channel if channel["type"] == "text" else self._call_channel),
            })
        self.channels.set_items(rows)
        self.spaces.update(self._space_id, unread=sum(self._unread.values()))
        self._rebuild_stage()
        self.localChanged.emit()

    def _rebuild_stage(self) -> None:
        if not self._call_channel:
            self.stage.clear()
            return
        my_id = self._me.get("id", 0)
        states = [v for v in self._voice.values() if v["channel_id"] == self._call_channel]
        if not any(v["user_id"] == my_id for v in states) and self._me:
            states.append({"user_id": my_id, "display_name": self._me["display_name"], "muted": self._muted,
                           "deafened": self._deafened, "server_muted": False})
        rows = []
        mixer = self.call.audio.mixer if self.call else None
        for state in sorted(states, key=lambda v: (v["user_id"] != my_id,
                                                   self._name(v["user_id"], v["display_name"]).lower())):
            uid = state["user_id"]
            is_me = uid == my_id
            name = self._name(uid, state["display_name"])
            rows.append({
                "id": uid, "name": name, "initials": initials(name),
                "color": avatar_color(uid),
                "speaking": (self._transmitting and not self._muted) if is_me else uid in self._speakers,
                "muted": self._muted if is_me else state["muted"], "deafened": self._deafened if is_me else
                state["deafened"], "serverMuted": bool(state.get("server_muted")),
                # Our own share is always shown to us as a local preview.
                "streaming": uid in self._streams,
                "watching": (self._sharing and is_me) or uid in self._watching,
                "volume": self.audio_settings.user_volumes.get(uid, 1.0),
                "localMuted": bool(mixer and uid in mixer.muted_users), "quality": self._quality.get(uid, 2),
                "isMe": is_me, "keyChanged": uid in self._key_changed,
                "videoInfo": self._video_info.get(uid, ("", ""))[0],
                "videoDetail": self._video_info.get(uid, ("", ""))[1],
                "avatar": self._avatar_url(uid), "camera": uid in self._cameras, "cameraKey": camera_key(uid),
                "canWake": not is_me and bool(state["muted"] or state["deafened"] or state.get("server_muted")),
            })
        self.stage.set_items(rows)
        if self._focus_user and self._focus_user not in self._streams:
            self._set("_focus_user", 0, self.focusChanged)

    def _on_event(self, event: dict) -> None:
        kind = event.get("type", "")
        if kind in ("voice.state", "voice.left"):
            self._voice_generation += 1
        if self.call is not None:
            self.call.handle_event(event)
        if kind == "connected":
            self._refresh_space()
        elif kind == "voice.state":
            state = event["state"]
            uid = state["user_id"]
            self._avatars[uid] = state.get("avatar_version", self._avatars.get(uid, 0))
            previous = self._voice.get(uid)
            self._voice[uid] = state
            if (self._call_channel and state["channel_id"] == self._call_channel and uid != self._me.get("id")
                    and (previous is None or previous["channel_id"] != state["channel_id"])):
                self._sound("join", "sound_join_leave")
            if uid == self._me.get("id") and previous and previous.get("server_muted") != state.get("server_muted"):
                self.toast("warning" if state.get("server_muted") else "info",
                           "Модератор выключил вам микрофон" if state.get("server_muted")
                           else "Модератор снова разрешил вам говорить")
            self._rebuild()
        elif kind == "voice.left":
            uid = event["user_id"]
            state = self._voice.get(uid)
            if state and state["channel_id"] == event["channel_id"]:
                del self._voice[uid]
                if event["channel_id"] == self._call_channel and uid != self._me.get("id"):
                    self._sound("leave", "sound_join_leave")
                    self._streams.discard(uid)
                    self._watching.discard(uid)
                self._rebuild()
        elif kind in ("channel.updated", "channel.created"):
            channel = event.get("channel", {})
            if channel.get("space_id") == self._space_id:
                self._channels = [c for c in self._channels if c["id"] != channel["id"]] + [channel]
                self._channels.sort(key=lambda c: (c["type"] != "text", c.get("position", 0), c["id"]))
                self._rebuild()
        elif kind == "space.deleted":
            if event.get("space_id") == self._space_id:
                self.toast("warning", f"Пространство «{self._space_name}» удалено")
            self._reload_spaces()
        elif kind == "space.members":
            self._reload_spaces()
            if event.get("space_id") == self._space_id:
                self.loadMembers()
        elif kind == "space.updated":
            self._reload_spaces()
            if event.get("space_id") == self._space_id:
                self.loadMembers()
        elif kind == "user.avatar":
            uid = event.get("user_id", 0)
            self._avatars[uid] = event.get("avatar_version", 0)
            if uid == self._me.get("id"):
                self._me = {**self._me, "avatar_version": self._avatars[uid]}
                self.meChanged.emit()
            self._members = [{**m, "avatar": self._avatar_url(m["id"])} for m in self._members]
            self.membersChanged.emit()
            for row in self.messages.rows:
                if row["authorId"] == uid:
                    self.messages.update(row["id"], avatar=self._avatar_url(uid))
            self._rebuild()
        elif kind == "user.renamed":
            uid, name = event.get("user_id", 0), event.get("display_name", "")
            if name:
                if uid in self._voice:
                    self._voice[uid] = {**self._voice[uid], "display_name": name}
                self._members_raw = [{**m, "display_name": name} if m["id"] == uid else m for m in self._members_raw]
                for row in self.messages.rows:
                    if row["authorId"] == uid:
                        row["authorReal"] = name
                if uid == self._me.get("id"):
                    self._me = {**self._me, "display_name": name, "initials": initials(name)}
                    self.meChanged.emit()
                self._names_changed(uid)
        elif kind == "voice.wake":
            self._on_wake(event)
        elif kind == "channel.deleted":
            channel_id = event.get("channel_id")
            self._channels = [c for c in self._channels if c["id"] != channel_id]
            if self._text_channel == channel_id:
                self._text_channel = 0
                self._set_chat(self._call_channel)
            self._rebuild()
        elif kind == "message.created":
            self._on_message(event["message"])
        elif kind == "message.updated":
            self._on_message(event["message"], updated=True)
        elif kind == "message.deleted":
            self.messages.update(event["message_id"], deleted=True, html="", raw="")
        elif kind in ("session.ended", "signed_out"):
            self._end_session()
            self._show_login("Сеанс завершён. Войдите снова")

    def _identity_changed(self, user_id: int) -> None:
        self._key_changed.add(user_id)
        name = self._name(user_id, self._voice.get(user_id, {}).get("display_name", f"Пользователь {user_id}"))
        self.toast("warning", f"У {name} сменился ключ шифрования. Сверьте код безопасности")
        self._rebuild_stage()

    # =========================================================================================
    # call
    # =========================================================================================

    @Slot(int)
    def joinChannel(self, channel_id: int) -> None:
        if self.call is None:
            return
        channel = next((c for c in self._channels if c["id"] == channel_id), None)
        if channel and channel.get("user_limit"):
            inside = sum(1 for v in self._voice.values() if v["channel_id"] == channel_id)
            if inside >= channel["user_limit"] and self._call_channel != channel_id:
                self.toast("warning", "Канал заполнен")
                return
        if self._call_channel == channel_id:
            self._text_channel = 0
            self._set_chat(channel_id)
            self._rebuild()
            return
        self._call_channel = channel_id
        self._call_state = "connecting"
        # Our own "joined" plays on the click, not a second later when the media server answers.
        self._announced_channel = channel_id
        self._sound("join", "sound_join_leave")
        self._text_channel = 0
        self._set_chat(channel_id)
        self.callChanged.emit()
        self._rebuild()
        self.call.join(channel_id)

    @Slot()
    def leaveCall(self) -> None:
        if self.call is not None:
            # The sound player has its own stream, so the call closing its audio does not cut this off.
            self._sound("leave", "sound_join_leave")
            self.call.leave()

    def _on_call_state(self, state: str, channel_id: int | None) -> None:
        was = self._call_channel
        self._call_state = state
        if state == IDLE:
            self._call_channel = 0
            self._announced_channel = 0
            self.sounds.keep_open = False  # closes itself once the last sound has played
            self._speakers.clear()
            self._streams.clear()
            self._watching.clear()
            self._sharing = False
            self._capturer = None
            self._stop_camera_device()
            self._cameras.clear()
            self._set("_focus_user", 0, self.focusChanged)
            self._ping_timer.stop()
            native.set_call_active(False)
            if self._mic_test and self.call is not None:
                # Settings are open on the voice tab: keep the level meter alive after hanging up.
                try:
                    self.call.audio.start()
                except Exception:
                    logger.info("Mic test could not reopen the microphone", exc_info=True)
            if self._chat_channel == was and not self._text_channel:
                first_text = next((c["id"] for c in self._channels if c["type"] == "text"), 0)
                self._text_channel = first_text
                self._set_chat(first_text)
        else:
            if channel_id:
                self._call_channel = channel_id
            if state == CONNECTED and was != 0:
                if not self.sounds.keep_open:
                    self.sounds.keep_open = True
                    self._sound_worker.submit(self.sounds.warm)
                if self._announced_channel != self._call_channel:
                    # Moved by a moderator or rejoined on our own: no click announced this channel.
                    self._announced_channel = self._call_channel
                    self._sound("join", "sound_join_leave")
                if not self._ping_timer.isActive():
                    self._ping_timer.start()
                    self._measure_ping()
                native.set_call_active(True)
        self._update_timers()
        self.callChanged.emit()
        self._rebuild()

    def _on_speakers(self, speakers: set[int]) -> None:
        if speakers != self._speakers:
            self._speakers = set(speakers)
            self._rebuild()

    def _on_quality(self, user_id: int, quality: int) -> None:
        self._quality[user_id] = quality
        self.stage.update(user_id, quality=quality)
        if user_id == self._me.get("id"):
            self.callChanged.emit()

    def _on_streams(self, users: set[int]) -> None:
        my_id = self._me.get("id", 0)
        self._sharing = my_id in users
        for uid in self._streams - users:
            video_hub().drop(uid)
        self._streams = set(users)
        self._watching &= self._streams
        self.callChanged.emit()
        self._rebuild()

    def _on_cameras(self, users: set[int]) -> None:
        for uid in self._cameras - users:
            video_hub().drop(camera_key(uid))
        self._cameras = set(users)
        self._rebuild_stage()

    def _measure_ping(self) -> None:
        self._bg(self.api.ping_ms, lambda ms: self._set("_ping", ms, self.callChanged), lambda exc: None)

    @Slot()
    def toggleMute(self) -> None:
        if self.call is None:
            return
        self._muted = not self._muted
        if not self._muted:
            self._deafened = False
        self.call.set_muted(self._muted)
        self._prompt("mic_off" if self._muted else "mic_on")
        self.localChanged.emit()
        self._rebuild_stage()

    @Slot()
    def toggleDeafen(self) -> None:
        if self.call is None:
            return
        self._deafened = not self._deafened
        self._muted = self._deafened
        self.call.set_muted(self._muted)
        self.call.set_deafened(self._deafened)
        self._prompt("sound_off" if self._deafened else "sound_on")
        self.localChanged.emit()
        self._rebuild_stage()

    def _sound(self, name: str, setting: str = "") -> None:
        """Joins, leaves and message chimes. Joins and leaves queue up and repeat neither each other nor
        themselves: five friends joining at once, or one joining during the announcement, is one sound."""
        if self.call is None or self._deafened or (setting and not self.ui.get(setting, True)):
            return
        lane = "announce" if name in ANNOUNCEMENTS else "ui"
        self._play(SOUNDS[name], lane=lane, key=name, gain=0.9 if name in SPOKEN else 0.6)

    def _prompt(self, name: str) -> None:
        """Mic/headphone button feedback. Spoken even right after deafening, so you hear "Звук выключен".
        A new click cuts the previous prompt short instead of waiting for it."""
        if self.call is None:
            return
        spoken = self.ui.get("voice_prompts", True)
        pcm = SOUNDS[name] if spoken else SOUNDS[PROMPTS[name]]
        self._play(pcm, lane="prompt", replace=True, gain=0.9 if spoken and name in SPOKEN else 0.6)

    def _play(self, pcm: bytes, **options) -> None:
        def play() -> None:
            try:
                self.sounds.play(pcm, **options)
            except Exception:
                logger.debug("UI sound not played", exc_info=True)
        try:
            self._sound_worker.submit(play)
        except RuntimeError:  # shutting down
            pass

    def _echo_reference(self, frame: bytes) -> None:
        call = self.call
        if call is not None:
            call.audio.add_reverse(frame)

    @Slot(int, float)
    def setUserVolume(self, user_id: int, volume: float) -> None:
        volume = max(0.0, min(3.0, volume))
        self.audio_settings.user_volumes[user_id] = volume
        if self.call is not None:
            self.call.set_user_volume(user_id, volume)
        self.stage.update(user_id, volume=volume)
        self._save()

    @Slot(int, bool)
    def setUserLocallyMuted(self, user_id: int, muted: bool) -> None:
        if self.call is not None:
            self.call.set_user_muted(user_id, muted)
        self.stage.update(user_id, localMuted=muted)

    @Slot(int, bool)
    def watch(self, user_id: int, watching: bool) -> None:
        if self.call is None:
            return
        (self._watching.add if watching else self._watching.discard)(user_id)
        if not watching:
            video_hub().drop(user_id)
            if self._focus_user == user_id:
                self._set("_focus_user", 0, self.focusChanged)
        else:
            self._set("_focus_user", user_id, self.focusChanged)
        self.call.watch(user_id, watching)
        self.stage.update(user_id, watching=watching)
        if watching and not self._video_timer.isActive():
            self._video_timer.start()

    @Slot(int)
    def focusUser(self, user_id: int) -> None:
        self._set("_focus_user", user_id, self.focusChanged)

    @Slot(result="QVariantList")
    def monitors(self) -> list[dict]:
        return list_monitors()

    @Slot(result="QVariantList")
    def windows(self) -> list[dict]:
        return list_windows()

    def _allowed_presets(self) -> list:
        limit = self._current_space().get("max_share_quality") or "1440p60"
        keys = [p.key for p in PRESETS]
        cap = keys.index(limit) if limit in keys else len(keys) - 1
        return PRESETS[:cap + 1]

    @Slot(result="QVariantList")
    def sharePresets(self) -> list[dict]:
        """Qualities this space allows (its settings set the best one)."""
        return [{"key": p.key, "title": p.title} for p in self._allowed_presets()]

    @Slot(int, str)
    def startScreen(self, monitor: int, preset_key: str) -> None:
        self.startShare("monitor", str(monitor), preset_key)

    @Slot(str, str, str)
    def startShare(self, kind: str, ident_text: str, preset_key: str) -> None:
        """Share a whole monitor (kind "monitor", its number) or one window (kind "window", its handle).
        The id comes as text: a window handle can be larger than a QML int."""
        if self.call is None or not self._call_channel:
            return
        try:
            ident = int(float(ident_text))
        except ValueError:
            return
        allowed = self._allowed_presets()
        preset = next((p for p in allowed if p.key == preset_key), allowed[-1])
        self.ui["screen_preset"] = preset.key
        if kind == "monitor":
            self.ui["screen_monitor"] = ident
        self._save()
        try:
            my_id = self._me.get("id", 0)
            capturer = ScreenCapturer(ident, preset, preview=lambda image: video_hub().push_image(my_id, image),
                                      kind=kind)
        except Exception as exc:  # noqa: BLE001
            self.toast("error", str(exc))
            return
        self.call.start_screen(capturer)
        self._capturer = capturer
        if not self._video_timer.isActive():
            self._video_timer.start()
        self.toast("success", f"Демонстрация {'окна' if kind == 'window' else 'экрана'}: {preset.title}")

    @Slot()
    def stopScreen(self) -> None:
        if self.call is not None:
            self.call.stop_screen()
        self._capturer = None
        video_hub().drop(self._me.get("id", 0))

    # --- camera -------------------------------------------------------------------------------

    @Slot(result="QVariantList")
    def cameras(self) -> list[dict]:
        from .camera import list_cameras

        return list_cameras()

    @Slot(str)
    def startCamera(self, camera_id: str) -> None:
        if self.call is None or not self._call_channel:
            return
        if self._call_in_current_space() and not self._current_space().get("camera_allowed", True):
            self.toast("warning", "В этом пространстве камеры выключены в настройках")
            return
        from .camera import CameraCapturer

        self._stop_camera_device()
        my_id = self._me.get("id", 0)
        try:
            capturer = CameraCapturer(
                camera_id, preview=lambda image: video_hub().push_image(camera_key(my_id), image),
                on_error=lambda text: self._ui(lambda: (self.toast("error", f"Камера: {text}"), self.stopCamera())))
            capturer.start()
        except Exception as exc:  # noqa: BLE001
            self.toast("error", str(exc))
            return
        self._camera = capturer
        self.ui["camera_id"] = camera_id
        self._save()
        self.call.start_camera(capturer)
        self.callChanged.emit()

    def _call_in_current_space(self) -> bool:
        return any(c["id"] == self._call_channel for c in self._channels)

    @Slot()
    def stopCamera(self) -> None:
        if self.call is not None:
            self.call.stop_camera()
        self._stop_camera_device()
        self.callChanged.emit()

    def _stop_camera_device(self) -> None:
        camera, self._camera = self._camera, None
        if camera is not None:
            camera.stop()
            camera.deleteLater()
            video_hub().drop(camera_key(self._me.get("id", 0)))

    # --- waking someone up -------------------------------------------------------------------

    @Slot(int)
    def wakeUser(self, user_id: int) -> None:
        name = self._name(user_id, self._voice.get(user_id, {}).get("display_name", ""))

        def done(result):
            self._wake_until[user_id] = float(result.get("until", time.time() + 60))
            self.toast("success", f"Будим {name}…" if name else "Будим…")
        self._bg(lambda: self.api.wake_user(user_id), done)

    @Slot(int, result=int)
    def wakeWait(self, user_id: int) -> int:
        """Seconds until anyone may wake this person again (0: now)."""
        return max(0, int(self._wake_until.get(user_id, 0) - time.time() + 0.99))

    def _on_wake(self, event: dict) -> None:
        uid = event.get("user_id", 0)
        self._wake_until[uid] = float(event.get("until", time.time() + 60))
        if uid != self._me.get("id"):
            return
        self.toast("warning", f"{event.get('by', 'Кто-то')} будит вас! Включите микрофон или звук")
        # Played even with the sound off: that is the whole point.
        self._play(SOUNDS["wake"], lane="wake", replace=True, gain=1.0)

    # --- space settings, members, pictures ----------------------------------------------------

    def _set_members(self, members: list[dict]) -> None:
        for member in members:
            self._avatars[member["id"]] = member.get("avatar_version", 0)
        self._members_raw = members
        self._members = [{**m, "display_name": self._name(m["id"], m["display_name"]),
                          "realName": m["display_name"], "nickname": self._nicknames.get(str(m["id"]), ""),
                          "initials": initials(self._name(m["id"], m["display_name"])), "color": avatar_color(m["id"]),
                          "avatar": self._avatar_url(m["id"]), "isMe": m["id"] == self._me.get("id")}
                         for m in members]
        self.membersChanged.emit()

    @Slot()
    def loadMembers(self) -> None:
        space_id = self._space_id
        if not space_id:
            return

        def done(members):
            if space_id == self._space_id:
                self._set_members(members)
                self._rebuild()
        self._bg(lambda: self.api.space_members(space_id), done, lambda exc: None)

    @Slot(str, str, bool)
    def updateSpace(self, name: str, share_quality: str, camera_allowed: bool) -> None:
        space_id = self._space_id
        changes: dict[str, Any] = {"max_share_quality": share_quality, "camera_allowed": camera_allowed}
        if name.strip() and name.strip() != self._space_name:
            changes["name"] = name.strip()

        def done(_):
            self.toast("success", "Настройки пространства сохранены")
            self._reload_spaces(space_id)
        self._bg(lambda: self.api.update_space(space_id, **changes), done)

    @Slot(int, bool)
    def setModerator(self, user_id: int, moderator: bool) -> None:
        space_id = self._space_id

        def done(members):
            if space_id == self._space_id:
                self._set_members(members)
        self._bg(lambda: self.api.set_moderator(space_id, user_id, moderator), done)

    @staticmethod
    def _local_path(url: str) -> str:
        return QUrl(url).toLocalFile() if url.startswith("file:") else url

    @Slot(str)
    def setMyAvatar(self, url: str) -> None:
        from .images import prepare_avatar

        path = self._local_path(url)

        def work():
            return self.api.set_my_avatar(prepare_avatar(path) if path else b"")

        def done(result):
            uid = self._me.get("id", 0)
            self._avatars[uid] = result.get("avatar_version", 0)
            self._me = {**self._me, "avatar_version": self._avatars[uid]}
            self.meChanged.emit()
            self.toast("success", "Аватарка обновлена" if path else "Аватарка убрана")
            self._rebuild()
        self._bg(work, done)

    @Slot(str)
    def setSpaceAvatar(self, url: str) -> None:
        from .images import prepare_avatar

        path = self._local_path(url)
        space_id = self._space_id

        def done(_):
            self.toast("success", "Картинка пространства обновлена" if path else "Картинка пространства убрана")
            self._reload_spaces(space_id)
        self._bg(lambda: self.api.set_space_avatar(space_id, prepare_avatar(path) if path else b""), done)

    # --- moderation ---------------------------------------------------------------------------

    @Slot(int, int)
    def moveUser(self, user_id: int, channel_id: int) -> None:
        self._bg(lambda: self.api.move_user(user_id, channel_id))

    @Slot(int)
    def kickUser(self, user_id: int) -> None:
        self._bg(lambda: self.api.kick_user(user_id), lambda _: self.toast("info", "Пользователь отключён"))

    @Slot(int, bool)
    def serverMute(self, user_id: int, muted: bool) -> None:
        self._bg(lambda: self.api.server_mute(user_id, muted))

    @Slot(result="QVariantList")
    def voiceChannels(self) -> list[dict]:
        return [{"id": c["id"], "name": c["name"]} for c in self._channels if c["type"] == "voice"]

    # --- channel management -------------------------------------------------------------------

    @Slot(str, str, bool, int)
    def createChannel(self, name: str, channel_type: str, is_private: bool, user_limit: int) -> None:
        space_id = self._space_id

        def done(channel):
            if not any(c["id"] == channel["id"] for c in self._channels):
                self._channels.append(channel)
            self._rebuild()
            self.toast("success", f"Канал «{channel['name']}» создан")
        self._bg(lambda: self.api.create_channel(space_id, name.strip(), channel_type, is_private, user_limit), done)

    @Slot(int, str, int)
    def updateChannel(self, channel_id: int, name: str, user_limit: int) -> None:
        self._bg(lambda: self.api.update_channel(channel_id, name=name.strip(), user_limit=user_limit))

    @Slot(int, int)
    def moveChannel(self, channel_id: int, step: int) -> None:
        """Move a channel up (-1) or down (+1) among channels of its kind; everyone sees the new order."""
        channel = next((c for c in self._channels if c["id"] == channel_id), None)
        if channel is None:
            return
        same = [c for c in self._channels if c["type"] == channel["type"]]
        index = same.index(channel)
        target = index + step
        if not 0 <= target < len(same):
            return
        same[index], same[target] = same[target], same[index]
        others = [c for c in self._channels if c["type"] != channel["type"]]
        order = [c["id"] for c in same] + [c["id"] for c in others]
        for position, item in enumerate(same, start=1):
            item["position"] = position
        self._channels = sorted(self._channels, key=lambda c: (c["type"] != "text", c.get("position", 0), c["id"]))
        self._rebuild()
        space_id = self._space_id
        self._bg(lambda: self.api.reorder_channels(space_id, order), lambda _: None,
                 lambda exc: (self._toast_error(exc), self._refresh_space()))

    @Slot(int)
    def deleteChannel(self, channel_id: int) -> None:
        self._bg(lambda: self.api.delete_channel(channel_id))

    @Slot(bool)
    def createInvite(self, as_admin: bool) -> None:
        self._bg(lambda: self.api.create_invite(is_admin=as_admin), lambda data: self.inviteCreated.emit(data["code"]))

    # =========================================================================================
    # chat
    # =========================================================================================

    @Slot(int)
    def openChannel(self, channel_id: int) -> None:
        """Text channel: show its chat in the main area."""
        self._text_channel = channel_id
        self._set_chat(channel_id)
        self._rebuild()

    @Slot()
    def showCall(self) -> None:
        if self._call_channel:
            self._text_channel = 0
            self._set_chat(self._call_channel)
            self._rebuild()

    def _set_chat(self, channel_id: int) -> None:
        if channel_id == self._chat_channel:
            self.chatChanged.emit()
            return
        self._chat_channel = channel_id
        self.messages.clear()
        self._has_more = False
        self.chatChanged.emit()
        if channel_id:
            self._load_messages(channel_id)

    def _decorate(self, raw: list[dict], previous: dict | None = None) -> list[dict]:
        rows = []
        my_id = self._me.get("id", 0)
        username = self._me.get("username", "")
        for message in raw:
            moment = _parse_time(message["created_at"])
            body_html, mentioned = render_message(message["body"], username)
            day = _day_title(moment)
            grouped = bool(previous and previous["authorId"] == message["author_id"] and not previous["deleted"]
                           and previous["day"] == day and moment.timestamp() - previous["_ts"] < 300)
            author = self._name(message["author_id"], message["author_name"])
            row = {
                "id": message["id"], "authorId": message["author_id"], "author": author,
                "authorReal": message["author_name"],
                "initials": initials(author), "color": avatar_color(message["author_id"]),
                "html": body_html, "raw": message["body"], "time": moment.strftime("%H:%M"), "day": day,
                "showDay": not previous or previous["day"] != day, "edited": bool(message.get("edited_at")),
                "deleted": bool(message.get("deleted")), "mine": message["author_id"] == my_id,
                "mention": mentioned or my_id in message.get("mentions", []), "grouped": grouped,
                "avatar": self._avatar_url(message["author_id"]),
                "_ts": moment.timestamp(),
            }
            rows.append(row)
            previous = row
        return rows

    def _load_messages(self, channel_id: int) -> None:
        def done(raw):
            if channel_id != self._chat_channel:
                return
            rows = self._decorate(raw)
            self.messages.set_items(rows)
            self._oldest_loaded = raw[0]["id"] if raw else 0
            self._has_more = len(raw) >= 50
            self.chatChanged.emit()
            self._mark_read()
        self._bg(lambda: self.api.messages(channel_id), done)

    @Slot()
    def loadOlder(self) -> None:
        channel_id, before = self._chat_channel, self._oldest_loaded
        if not channel_id or not before or not self._has_more:
            return
        self._has_more = False

        def done(raw):
            if channel_id != self._chat_channel or not raw:
                self.chatChanged.emit()
                return
            older = self._decorate(raw)
            # The first message already shown may now continue a group or the same day.
            if self.messages.rows:
                first, last_older = self.messages.rows[0], older[-1]
                same_day = first["day"] == last_older["day"]
                self.messages.update(first["id"], showDay=not same_day, grouped=bool(
                    same_day and first["authorId"] == last_older["authorId"] and not last_older["deleted"]
                    and first["_ts"] - last_older["_ts"] < 300))
            self.messages.prepend_many(older)
            self._oldest_loaded = raw[0]["id"]
            self._has_more = len(raw) >= 50
            self.chatChanged.emit()
        self._bg(lambda: self.api.messages(channel_id, before=before), done)

    def _on_message(self, message: dict, updated: bool = False) -> None:
        channel_id = message["channel_id"]
        if updated:
            if channel_id == self._chat_channel:
                body_html, mentioned = render_message(message["body"], self._me.get("username", ""))
                self.messages.update(message["id"], html=body_html, raw=message["body"], edited=True,
                                     mention=mentioned)
            return
        mine = message["author_id"] == self._me.get("id")
        if channel_id == self._chat_channel:
            if self.messages.index_of(message["id"]) == -1:
                previous = self.messages.rows[-1] if self.messages.rows else None
                self.messages.append(self._decorate([message], previous)[0])
            if not mine:
                if QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive:
                    self._mark_read()
                else:
                    self._unread[channel_id] = self._unread.get(channel_id, 0) + 1
                    self._rebuild()
        elif not mine and any(c["id"] == channel_id for c in self._channels):
            self._unread[channel_id] = min(99, self._unread.get(channel_id, 0) + 1)
            self._rebuild()
        if not mine:
            _, mentioned = render_message(message["body"], self._me.get("username", ""))
            if mentioned and self.ui.get("notify_mentions", True):
                self.toast("mention", f"{self._name(message['author_id'], message['author_name'])} упомянул вас: {message['body'][:80]}")
                self._sound("message", "sound_messages")
            elif channel_id == self._call_channel:
                self._sound("message", "sound_messages")

    @Slot()
    def markChatRead(self) -> None:
        self._mark_read()

    def _mark_read(self) -> None:
        channel_id = self._chat_channel
        if not channel_id or not self.messages.rows:
            return
        last = self.messages.rows[-1]["id"]
        if self._unread.pop(channel_id, None):
            self._rebuild()
        self._bg(lambda: self.api.mark_read(channel_id, last), lambda _: None, lambda exc: None)

    @Slot(str)
    def sendMessage(self, text: str) -> None:
        channel_id = self._chat_channel
        text = text.strip()
        if not channel_id or not text:
            return
        self._bg(lambda: self.api.send_message(channel_id, text), lambda message: self._on_message(message))

    @Slot(int, str)
    def editMessage(self, message_id: int, text: str) -> None:
        if text.strip():
            self._bg(lambda: self.api.edit_message(message_id, text.strip()),
                     lambda message: self._on_message(message, updated=True))

    @Slot(int)
    def deleteMessage(self, message_id: int) -> None:
        self._bg(lambda: self.api.delete_message(message_id),
                 lambda _: self.messages.update(message_id, deleted=True, html="", raw=""))

    @Slot(str)
    def openLink(self, url: str) -> None:
        if url.startswith(("https://", "http://")):
            QDesktopServices.openUrl(QUrl(url))

    @Slot(str)
    def copyText(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.toast("success", "Скопировано")

    # =========================================================================================
    # settings, devices, hotkeys
    # =========================================================================================

    @Slot(str, "QVariant")
    def setSetting(self, key: str, value: Any) -> None:
        audio_names = {f.name for f in fields(AudioSettings)}
        if key in audio_names:
            current = getattr(self.audio_settings, key)
            if isinstance(current, bool):
                value = bool(value)
            elif isinstance(current, float):
                value = float(value)
            setattr(self.audio_settings, key, value)
            if self.call is not None:
                self.call.apply_audio_settings(self.audio_settings)
        else:
            self.ui[key] = value
            if key.startswith("key_"):
                self._apply_hotkeys()
            if key == "autostart":
                try:
                    native.set_autostart(bool(value), f'"{sys.executable}" --tray')
                except OSError:
                    self.toast("error", "Не удалось изменить автозапуск")
        self._save()
        self.settingsChanged.emit()

    @Slot()
    def refreshDevices(self) -> None:
        def done(result):
            self._inputs, self._outputs = result
            self.devicesChanged.emit()
        self._bg(list_devices, done, lambda exc: None)

    @Slot(bool)
    def setMicTest(self, enabled: bool) -> None:
        """Settings screen meter: opens the microphone while the voice tab is visible."""
        self._mic_test = enabled
        if self.call is not None and not self._call_channel:
            try:
                (self.call.audio.start if enabled else self.call.audio.stop)()
            except Exception as exc:  # noqa: BLE001
                self.toast("error", str(exc))
        self._update_timers()

    def _apply_hotkeys(self) -> None:
        self._keys_poller.set_combos({name: self.ui.get(f"key_{name}", "")
                                      for name in ("ptt", "mute", "deafen", "overlay")})
        if hasattr(self, "_level_timer"):  # not while __init__ is still creating the timers
            self._update_timers()

    @Slot(int, str, result=str)
    def keyName(self, key: int, text: str) -> str:
        return native.qt_key_name(key, text)

    def _update_video_info(self) -> None:
        my_id = self._me.get("id", 0)
        info: dict[int, tuple[str, str]] = {}
        if self._capturer is not None and self._sharing:
            info[my_id] = describe_sent(self._capturer.stats())
        for uid in self._watching:
            received = video_hub().received(uid)
            if received:
                info[uid] = (f"{received['width']}×{received['height']} · {received['fps']:.0f} к/с", "")
        if info != self._video_info:
            self._video_info = info
            for uid in set(info) | {row["id"] for row in self.stage.rows if row.get("videoInfo")}:
                text, detail = info.get(uid, ("", ""))
                self.stage.update(uid, videoInfo=text, videoDetail=detail)
            self.videoInfoChanged.emit()
        if not info and not self._watching and self._capturer is None:
            self._video_timer.stop()

    def _update_timers(self) -> None:
        active = bool(self._call_channel) or self._mic_test
        # The keyboard is read only during a call and only for the shortcuts that are set (antivirus
        # heuristics rightly dislike programs that watch the keyboard all the time).
        keys = bool(self._call_channel) and self._keys_poller.has_combos
        for timer, wanted in ((self._level_timer, active), (self._key_timer, keys)):
            if wanted and not timer.isActive():
                timer.start()
            elif not wanted and timer.isActive():
                timer.stop()
        if not active and self.call is not None:
            self.call.set_push_to_talk(False)

    def _poll_keys(self) -> None:
        if not self._call_channel or self.call is None:
            return
        for name, down in self._keys_poller.poll():
            if name == "ptt":
                self.call.set_push_to_talk(down)
            elif down and name == "mute":
                self.toggleMute()
            elif down and name == "deafen":
                self.toggleDeafen()
            elif down and name == "overlay":
                self.setSetting("overlay_enabled", not self.ui.get("overlay_enabled"))

    def _poll_level(self) -> None:
        if self.call is None:
            return
        audio = self.call.audio
        level = max(0.0, min(1.0, (getattr(audio, "input_level_db", -96.0) + 60.0) / 60.0))
        transmitting = bool(audio.transmitting) and not self._muted
        if abs(level - self._level) > 0.02 or transmitting != self._transmitting:
            changed = transmitting != self._transmitting
            self._level, self._transmitting = level, transmitting
            self.levelChanged.emit()
            if changed and self._call_channel:
                self.stage.update(self._me.get("id"), speaking=transmitting)
                my_row = self.channels.find(self._call_channel)
                if my_row:
                    members = [{**m, "speaking": transmitting} if m["isMe"] else m for m in my_row["members"]]
                    self.channels.update(self._call_channel, members=members)

    # =========================================================================================
    # account, devices, security
    # =========================================================================================

    @Slot()
    def loadSessions(self) -> None:
        def done(rows):
            self._sessions = rows
            self.sessionsChanged.emit()
        self._bg(self.api.sessions, done)

    @Slot(int)
    def revokeSession(self, session_id: int) -> None:
        self._bg(lambda: self.api.revoke_session(session_id), lambda _: self.loadSessions())

    @Slot()
    def revokeOtherSessions(self) -> None:
        self._bg(self.api.revoke_other_sessions,
                 lambda _: (self.toast("success", "Остальные устройства отключены"), self.loadSessions()))

    @Slot()
    def loadSafetyCodes(self) -> None:
        if self.keys is None:
            return
        channel_id = self._call_channel
        keys = self.keys

        def load():
            if channel_id:
                keys.sync(channel_id)
            rows = []
            for state in self._voice.values():
                uid = state["user_id"]
                if uid == self._me.get("id") or (channel_id and state["channel_id"] != channel_id):
                    continue
                code = keys.safety_code_with(uid)
                if code:
                    rows.append({"id": uid, "name": self._name(uid, state["display_name"]), "code": code,
                                 "changed": uid in self._key_changed})
            return rows

        def done(rows):
            self._safety = rows
            self.safetyChanged.emit()
        self._bg(load, done)

    @Slot(int)
    def confirmSafety(self, user_id: int) -> None:
        self._key_changed.discard(user_id)
        self._rebuild_stage()
        self.loadSafetyCodes()

    # --- updates and diagnostics --------------------------------------------------------------

    def _got_update(self, info) -> None:
        if info.update_available and _newer(info.latest_version, APP_VERSION):
            self._update = {"version": info.latest_version, "required": info.required,
                            "notes": info.release_notes_url, "_info": info}
            self.updateChanged.emit()

    @Slot()
    def installUpdate(self) -> None:
        self._install_update()

    def _install_update(self, on_failure: Callable[[], None] | None = None) -> None:
        info = self._update.get("_info")
        if info is None or self._update_progress >= 0:
            if on_failure:
                on_failure()
            return
        from app.client.updater import download_update

        last_report = [0.0]

        def progress(done: int, total: int) -> None:
            # A few updates a second: one per received chunk flooded the UI thread with tens of thousands
            # of events for a 200 MB installer.
            now = time.monotonic()
            if now - last_report[0] < 0.2 and done < total:
                return
            last_report[0] = now
            value = done / total if total else 0.0
            sizes = f"{done / 1048576:.0f} из {total / 1048576:.0f} МБ" if total else f"{done / 1048576:.0f} МБ"
            self._ui(lambda: (setattr(self, "_update_progress", value), setattr(self, "_update_sizes", sizes),
                              self.updateChanged.emit()))

        def work():
            path = download_update(info, progress)
            logger.info("Update %s downloaded: %s", info.latest_version, path)
            return path

        def finished(path):
            import subprocess

            if on_failure is not None and self._view != "updating":
                logger.info("Update %s arrived after the app had started; it installs next launch",
                            info.latest_version)
                return
            logger.info("Installing update %s", info.latest_version)
            try:
                # Silent install closes this app, replaces the files and starts the new version again.
                subprocess.Popen([str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], close_fds=True)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Update installer did not start")
                failed(exc)
                return
            self.shutdown()
            QGuiApplication.quit()

        def failed(exc):
            logger.warning("Update not installed: %s", exc)
            self._update_progress = -1.0
            self.updateChanged.emit()
            if on_failure:
                self.toast("warning", f"Обновление не установилось, запускаю текущую версию: {exc}")
                on_failure()
            else:
                self.toast("error", f"Обновление не установлено: {exc}")
        self._update_progress = 0.0
        self.updateChanged.emit()
        self._bg(work, finished, failed)

    @Slot()
    def saveReport(self) -> None:
        """Zip of logs and environment info (no passwords, tokens or keys) for troubleshooting."""
        def build() -> Path:
            target_dir = Path.home() / "Desktop"
            if not target_dir.exists():
                target_dir = Path.home()
            target = target_dir / f"voicechat-report-{datetime.now():%Y%m%d-%H%M%S}.zip"
            info = {
                "version": APP_VERSION, "python": sys.version, "system": py_platform.platform(),
                "device": default_device_name(), "server": self.api.base_url, "online": self._online,
                "call_state": self._call_state, "ping_ms": self._ping,
                "audio": {k: v for k, v in asdict(self.audio_settings).items() if k != "user_volumes"},
                "inputs": self._inputs, "outputs": self._outputs,
            }
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("info.json", json.dumps(info, ensure_ascii=False, indent=2))
                for log in sorted(LOG_DIR.glob("*.log*")):
                    archive.write(log, log.name)
            return target

        def done(path: Path):
            self.toast("success", f"Отчёт сохранён: {path.name}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        self._bg(build, done)

    @Slot()
    def requestShow(self) -> None:
        self.showWindowRequested.emit()

    @Slot(result=bool)
    def gameInFullscreen(self) -> bool:
        return native.foreground_is_fullscreen()


_crash_file = None
STARTUP_HANG_SECONDS = 150  # longer than the update watchdog (2 minutes), which starts the app anyway


def _startup_finished() -> None:
    import faulthandler

    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass


def _enable_crash_log() -> None:
    """A crash inside native code (LiveKit, audio drivers, Qt) kills the process without a Python
    traceback, so the normal log just stops. faulthandler writes every thread's stack to crash.log
    then, and the report includes it."""
    global _crash_file
    import faulthandler

    try:
        path = LOG_DIR / "crash.log"
        if path.exists() and path.stat().st_size > 1_000_000:
            path.unlink()
        _crash_file = open(path, "a", encoding="utf-8")
        _crash_file.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} start {APP_VERSION} ---\n")
        _crash_file.flush()
        faulthandler.enable(_crash_file, all_threads=True)
        # A start-up that hangs (it happened: frozen for 40 minutes, nothing in the log) writes where
        # every thread is stuck. Cancelled once the login or main screen is reached.
        faulthandler.dump_traceback_later(STARTUP_HANG_SECONDS, repeat=False, file=_crash_file)
    except Exception:
        logging.getLogger("voicechat.ui").info("Crash log unavailable", exc_info=True)


def configure_logging() -> None:
    from logging.handlers import RotatingFileHandler

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _enable_crash_log()
    handler = RotatingFileHandler(LOG_DIR / "client.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if os.environ.get("VOICECHAT_DEBUG"):
        root.addHandler(logging.StreamHandler())
    # A start line without a "closing" line before it means the previous run was killed or crashed.
    logging.getLogger("voicechat.ui").info("Starting VoiceChat %s on %s", APP_VERSION, py_platform.platform())
    _ = time  # keep import for profiling hooks


__all__ = ["Backend", "configure_logging", "render_message", "UI_DEFAULTS"]
