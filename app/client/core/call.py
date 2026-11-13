"""The call: joining, leaving, switching, mute/deafen, keys and reconnection.

All network work for the call runs on one worker thread in order, so "join A, then join B" can never
interleave. The UI only calls methods and listens; nothing here blocks the interface or opens dialogs.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .audio import AudioEngine, AudioSettings
from .keys import KeyManager
from .media import MediaCallbacks, MediaSession

logger = logging.getLogger("voicechat.call")

HEARTBEAT_SECONDS = 15
AUDIO_LOG_EVERY = 4  # heartbeats: one line about the microphone and speakers per minute
# Fallback only (keys normally arrive with the e2ee.updated event at once): a missed event used to leave
# someone silent for up to five minutes.
KEY_REFRESH_SECONDS = 60

IDLE, CONNECTING, CONNECTED, RECONNECTING = "idle", "connecting", "connected", "reconnecting"
UNAVAILABLE = "unavailable"  # we are in the channel, but the server's media server is not reachable


@dataclass
class CallListener:
    on_state: Callable[[str, int | None], None] = lambda state, channel_id: None
    on_speakers: Callable[[set[int]], None] = lambda speakers: None
    on_quality: Callable[[int, int], None] = lambda user_id, quality: None
    on_streams: Callable[[set[int]], None] = lambda users: None
    on_video_frame: Callable[[int, Any], None] = lambda user_id, frame: None
    on_cameras: Callable[[set[int]], None] = lambda users: None
    on_notice: Callable[[str, str], None] = lambda level, text: None   # in-app toast, never a popup
    on_identity_changed: Callable[[int], None] = lambda user_id: None
    on_local: Callable[[dict], None] = lambda flags: None              # muted/deafened/transmitting


@dataclass
class _Job:
    func: Callable[[], None]
    name: str = ""


class CallController:
    def __init__(self, api, keys: KeyManager, audio_settings: AudioSettings, listener: CallListener,
                 media_factory: Callable[..., MediaSession] = MediaSession,
                 audio_factory: Callable[..., AudioEngine] = AudioEngine) -> None:
        self.api = api
        self.keys = keys
        self.listener = listener
        self.media_factory = media_factory
        self.audio = audio_factory(audio_settings, on_frame=self._on_mic_frame, on_problem=self._on_audio_problem)
        self.state = IDLE
        self.channel_id: int | None = None
        self.muted = False
        self.deafened = False
        self.media: MediaSession | None = None
        self.speakers: set[int] = set()
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._worker = threading.Thread(target=self._work, name="call-worker", daemon=True)
        self._worker.start()
        self._heartbeat_stop = threading.Event()
        self._heartbeat: threading.Thread | None = None
        self._last_key_refresh = 0.0
        self._last_audio_notice = 0.0
        self._recovering = False
        self._silence_noticed = False  # the "mic gives perfect silence" hint is shown once per call

    # --- public API (UI thread) -----------------------------------------------------------------

    def join(self, channel_id: int) -> None:
        self._submit(lambda: self._join(channel_id), "join")

    def leave(self) -> None:
        self._submit(self._leave, "leave")

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if not muted and self.deafened:
            self.deafened = False
            self.audio.mixer.deafened = False
        self._apply_local()

    def set_deafened(self, deafened: bool) -> None:
        self.deafened = deafened
        self.audio.mixer.deafened = deafened
        self._apply_local()

    def set_push_to_talk(self, pressed: bool) -> None:
        self.audio.ptt_pressed = pressed

    def apply_audio_settings(self, settings: AudioSettings) -> None:
        self.audio.apply_settings(settings)

    def set_user_volume(self, user_id: int, volume: float) -> None:
        self.audio.mixer.user_volume[user_id] = max(0.0, min(3.0, volume))

    def set_user_muted(self, user_id: int, muted: bool) -> None:
        (self.audio.mixer.muted_users.add if muted else self.audio.mixer.muted_users.discard)(user_id)

    def watch(self, user_id: int, watching: bool) -> None:
        if self.media:
            self.media.watch(user_id, watching)

    def start_screen(self, capturer) -> None:
        if self.media:
            self.media.start_screen(capturer)

    def stop_screen(self) -> None:
        if self.media:
            self.media.stop_screen()

    def start_camera(self, capturer) -> None:
        if self.media:
            self.media.start_camera(capturer)

    def stop_camera(self) -> None:
        if self.media:
            self.media.stop_camera()

    def handle_event(self, event: dict) -> None:
        """Server events relevant to the call (from the event stream thread)."""
        kind = event.get("type")
        channel_id = self.channel_id
        if kind == "voice.moved":
            logger.info("Moved to channel %s by %s", event.get("channel_id"), event.get("by"))
            self.listener.on_notice("info", f"{event.get('by', 'Модератор')} переместил вас в другой канал")
            self.join(int(event["channel_id"]))
        elif kind == "voice.kicked":
            logger.info("Disconnected from the channel by %s", event.get("by"))
            self.listener.on_notice("warning", f"{event.get('by', 'Модератор')} отключил вас от канала")
            self._submit(lambda: self._leave(notify_server=False), "kicked")
        elif channel_id is None:
            return
        elif kind == "voice.state" and event["state"]["channel_id"] == channel_id:
            if event["state"]["user_id"] != self.keys.my_id:
                self._submit(lambda: self._sync_keys(channel_id), "keys")
        elif kind == "voice.left" and event.get("channel_id") == channel_id and event.get("user_id") != self.keys.my_id:
            # Whoever left must not be able to decrypt what comes next.
            logger.info("User %s left channel %s: rotating our key", event.get("user_id"), channel_id)
            self._submit(lambda: self._rotate_keys(channel_id), "rotate")
        elif kind == "e2ee.updated" and event.get("channel_id") == channel_id:
            self._submit(lambda: self._sync_keys(channel_id), "keys")
        elif kind == "connected":
            # Event stream reconnected: we may have missed joins; refresh keys.
            self._submit(lambda: self._sync_keys(channel_id), "keys")

    def shutdown(self, timeout: float = 5.0) -> None:
        """App exit: leave the channel (so friends see it at once), stop audio, stop threads."""
        done = threading.Event()
        self._submit(lambda: (self._leave(), done.set()), "shutdown")
        done.wait(timeout)
        self._jobs.put(None)

    # --- worker ---------------------------------------------------------------------------------

    def _submit(self, func: Callable[[], None], name: str) -> None:
        self._jobs.put(_Job(func, name))

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                job.func()
            except Exception as exc:
                logger.exception("Call job %s failed", job.name)
                self.listener.on_notice("error", _friendly_error(exc))

    def _set_state(self, state: str) -> None:
        self.state = state
        self.listener.on_state(state, self.channel_id)

    def _join(self, channel_id: int) -> None:
        if self.channel_id == channel_id and self.state in (CONNECTED, CONNECTING, RECONNECTING):
            return
        logger.info("Joining channel %s (from %s)", channel_id, self.channel_id)
        self._silence_noticed = False
        if self.channel_id is not None:
            # The server moves us and kicks us from the old room. No IDLE in between, so the UI
            # switches channels without flashing "disconnected".
            # Devices stay open: closing and re-opening them on every switch cut the sound for a moment
            # and sometimes left it broken.
            self._leave(notify_server=False, final=False, keep_audio=True)
        self.channel_id = channel_id
        self._set_state(CONNECTING)
        try:
            self.api.connect(channel_id, self.muted, self.deafened)
            self.keys.register_device()
            self._sync_keys(channel_id)
            self.audio.start()
        except Exception:
            self.channel_id = None
            self._set_state(IDLE)
            self.audio.stop()
            raise
        callbacks = MediaCallbacks(
            on_state=self._on_media_state,
            on_speakers=self._on_speakers,
            on_quality=self.listener.on_quality,
            on_streams=self.listener.on_streams,
            on_video_frame=self.listener.on_video_frame,
            on_cameras=self.listener.on_cameras,
            on_participants=lambda users: None,
            on_key_problem=lambda user_id: self._on_key_problem(channel_id, user_id),
        )
        media = self.media_factory(
            credentials=lambda: self.api.media_token(channel_id),
            my_id=self.keys.my_id,
            outgoing_key=lambda: self.keys.outgoing(channel_id),
            incoming_key=lambda user_id: self.keys.incoming(channel_id, user_id),
            mixer=self.audio.mixer,
            callbacks=callbacks,
        )
        media.set_muted(self.muted or self.deafened)
        self.media = media
        media.start()
        self._start_heartbeat(channel_id)

    def _leave(self, notify_server: bool = True, final: bool = True, keep_audio: bool = False) -> None:
        channel_id = self.channel_id
        if channel_id is not None:
            logger.info("Leaving channel %s (tell server: %s, switching: %s)", channel_id, notify_server, not final)
        self._stop_heartbeat()
        media, self.media = self.media, None
        if media:
            media.stop()
        if keep_audio:
            self.audio.mixer.clear()
        else:
            self.audio.stop()
        self.speakers = set()
        self.listener.on_speakers(set())
        self.listener.on_streams(set())
        self.listener.on_cameras(set())
        if channel_id is not None:
            self.keys.forget(channel_id)
            if notify_server:
                try:
                    self.api.disconnect(channel_id)
                except Exception:
                    logger.info("Disconnect request failed; the server will expire the state")
        self.channel_id = None
        if final:
            self._set_state(IDLE)
        else:
            self.state = IDLE

    def _sync_keys(self, channel_id: int | None) -> None:
        if channel_id is None or channel_id != self.channel_id:
            return
        self.keys.sync(channel_id)
        self._last_key_refresh = time.monotonic()
        if self.media:
            self.media.refresh_keys()

    def _rotate_keys(self, channel_id: int) -> None:
        if channel_id != self.channel_id:
            return
        self.keys.rotate(channel_id)
        self._sync_keys(channel_id)

    # --- heartbeat ------------------------------------------------------------------------------

    def _start_heartbeat(self, channel_id: int) -> None:
        self._heartbeat_stop = threading.Event()
        stop = self._heartbeat_stop

        def beat() -> None:
            beats = 0
            while not stop.wait(HEARTBEAT_SECONDS):
                beats += 1
                if beats % AUDIO_LOG_EVERY == 1:
                    self._log_audio(channel_id)
                try:
                    self.api.update_voice(channel_id, self.muted, self.deafened, False)
                except Exception as exc:
                    if getattr(exc, "status_code", None) == 404 and not stop.is_set():
                        # The server dropped us (e.g. it restarted). Rejoin on the call worker, after
                        # checking we are still in this channel: from this thread the rejoin could land
                        # after a switch and put us back into the channel we had just left.
                        self._submit(lambda: self._rejoin_after_drop(channel_id), "rejoin")
                    else:
                        logger.info("Heartbeat failed: %s", exc)
                if time.monotonic() - self._last_key_refresh > KEY_REFRESH_SECONDS:
                    self._submit(lambda: self._sync_keys(channel_id), "keys")

        self._heartbeat = threading.Thread(target=beat, name="call-heartbeat", daemon=True)
        self._heartbeat.start()

    def _on_key_problem(self, channel_id: int, user_id: int) -> None:
        """We can't decrypt someone: their key was missed or changed. Fetch keys now, not in a minute."""
        logger.info("Fetching keys again: cannot decrypt user %s in channel %s", user_id, channel_id)
        self._submit(lambda: self._sync_keys(channel_id), "keys")

    def _rejoin_after_drop(self, channel_id: int) -> None:
        if self.channel_id != channel_id:
            return
        logger.warning("Server no longer had us in channel %s: rejoining", channel_id)
        try:
            self.api.connect(channel_id, self.muted, self.deafened)
        except Exception:
            logger.info("Rejoin after 404 failed", exc_info=True)

    def _log_audio(self, channel_id: int) -> None:
        try:
            stats = self.audio.stats()
            media = self.media
            stats["media"] = "connected" if media is not None and media.connected else "not connected"
            stats["state"] = self.state
            # Frames received from each person (100 a second while they talk; 0 = we hear nothing).
            stats["heard"] = self.audio.mixer.stats()
            # Times each person's sound ran dry and had to rebuffer (a short dropout each).
            stats["underruns"] = self.audio.mixer.underrun_stats()
            muted = self.muted or self.deafened
            if stats["running"] and stats["mic_frames"] == 0:
                logger.warning("Channel %s: the microphone delivers no sound at all: %s", channel_id, stats)
            elif stats["running"] and not muted and stats["mic_peak_db"] > stats["threshold_db"] \
                    and stats["sent_frames"] == 0:
                logger.warning("Channel %s: the mic hears sound but nothing passes the voice gate: %s",
                               channel_id, stats)
            else:
                logger.info("Channel %s audio: %s", channel_id, stats)
        except Exception:
            logger.debug("Audio stats failed", exc_info=True)

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        self._heartbeat = None

    # --- callbacks from audio/media threads -----------------------------------------------------

    def _on_mic_frame(self, frame: bytes) -> None:
        media = self.media
        if media is not None and not self.muted and not self.deafened:
            media.push_mic_frame(frame)

    def _on_audio_problem(self, problem: str) -> None:
        if problem == "fallback":
            self.listener.on_notice("warning", "Выбранное устройство недоступно, звук идёт через стандартное. "
                                               "Переключусь обратно, когда оно появится")
            return
        if problem == "mic_silent":
            if self.channel_id is not None and not self._silence_noticed:
                self._silence_noticed = True
                self.listener.on_notice(
                    "warning", "Микрофон передаёт полную тишину, переоткрываю его. Если вас всё равно не слышно, "
                               "проверьте кнопку выключения микрофона на ноутбуке или гарнитуре и звук в Windows, "
                               "или выберите другой микрофон в настройках")
            if not self._recovering:
                self._recovering = True
                self._submit(self._recover_audio, "audio")
            return
        # Also outside a call: the settings screen's mic test uses the same devices.
        now = time.monotonic()
        if now - self._last_audio_notice > 30 and self.channel_id is not None:
            self._last_audio_notice = now
            text = ("Аудиоустройства изменились, переключаю звук" if problem == "devices_changed"
                    else "Аудиоустройство отключилось, переключаюсь на другое")
            self.listener.on_notice("info" if problem == "devices_changed" else "warning", text)
        if not self._recovering:
            self._recovering = True
            self._submit(self._recover_audio, "audio")

    def _recover_audio(self) -> None:
        try:
            self._recover_audio_attempts()
        finally:
            self._recovering = False

    def _recover_audio_attempts(self) -> None:
        for attempt in range(5):
            try:
                self.audio.restart()
                logger.info("Audio devices reopened after a loss (attempt %d)", attempt + 1)
                return
            except Exception:
                logger.info("Audio reopen attempt %d failed", attempt + 1, exc_info=True)
                time.sleep(1 + attempt)
        logger.warning("Audio devices could not be reopened")
        self.listener.on_notice("error", "Не удалось открыть микрофон или наушники. Проверьте устройство в настройках")

    def _on_media_state(self, state: str) -> None:
        if self.channel_id is None:
            return
        if state == UNAVAILABLE and self.state != UNAVAILABLE:
            self.listener.on_notice("error", "Голосовой сервер недоступен: на сервере не настроен LiveKit. "
                                             "Каналы и чат работают, голос появится, когда его включат")
        mapped = {"connected": CONNECTED, "reconnecting": RECONNECTING, "connecting": CONNECTING,
                  UNAVAILABLE: UNAVAILABLE}.get(state)
        if mapped and mapped != self.state:
            self._set_state(mapped)

    def _on_speakers(self, speakers: set[int]) -> None:
        if self.audio.transmitting and not self.muted:
            speakers = speakers | {self.keys.my_id}
        self.speakers = speakers
        self.listener.on_speakers(speakers)

    def _apply_local(self) -> None:
        self.audio.muted = self.muted or self.deafened
        if self.media:
            self.media.set_muted(self.muted or self.deafened)
        self.listener.on_local({"muted": self.muted, "deafened": self.deafened})
        channel_id = self.channel_id
        if channel_id is not None:
            self._submit(lambda: self.api.update_voice(channel_id, self.muted, self.deafened, False), "voice-state")


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "Канал заполнен" in text:
        return "Канал заполнен"
    if getattr(exc, "status_code", None) in (401, 403):
        return text or "Нет доступа"
    if "аудиоустройство" in text.lower() or "audio" in text.lower():
        return "Не удалось открыть микрофон или наушники. Проверьте устройство в настройках"
    return text or "Не удалось подключиться"


__all__ = ["CallController", "CallListener", "IDLE", "CONNECTING", "CONNECTED", "RECONNECTING", "UNAVAILABLE",
           "field"]
