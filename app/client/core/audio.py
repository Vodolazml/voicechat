"""Microphone and speakers.

Capture runs in the PortAudio callback: gain → WebRTC processing (echo cancellation, noise
suppression, auto gain) → optional RNNoise → voice gate / push-to-talk. Each processed 10 ms frame is
handed to `on_frame` immediately; nothing waits on timers, so a minimised window or a busy game cannot
add delay. Playback pulls mixed audio from the Mixer in the output callback.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .dsp import (FADE_IN, FADE_OUT, FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE, SILENCE, Leveler, Limiter, Mixer,
                  RNNoise, VoiceGate, apply_gain, fade, to_bytes, to_float)
from .rt import boost_voice_thread

logger = logging.getLogger("voicechat.audio")

# Device buffer. "low" (~10 ms) left no slack: any moment the app was busy (screen share, drawing video)
# the speakers ran dry and the voice crackled or cut out. 40 ms rides over such hiccups and is still well
# below what anyone can hear in a conversation.
DEVICE_LATENCY = 0.04

NOISE_OFF, NOISE_STANDARD, NOISE_STRONG = "off", "standard", "strong"
MODE_VOICE, MODE_PTT = "voice", "ptt"


@dataclass
class AudioSettings:
    input_device: str = ""    # device name; "" follows the Windows default
    output_device: str = ""
    input_gain: float = 1.0   # 0..2
    output_volume: float = 1.0  # 0..2
    noise: str = NOISE_STANDARD
    echo_cancellation: bool = True
    auto_gain: bool = True
    normalize_voices: bool = True
    mode: str = MODE_VOICE
    auto_threshold: bool = True
    threshold_db: float = -45.0
    user_volumes: dict[int, float] = field(default_factory=dict)


def list_devices() -> tuple[list[str], list[str]]:
    """Input and output device names, de-duplicated across host APIs (WASAPI preferred on Windows)."""
    import sounddevice as sd

    inputs: list[str] = []
    outputs: list[str] = []
    try:
        apis = sd.query_hostapis()
        preferred = next((i for i, api in enumerate(apis) if "WASAPI" in api["name"]), None)
        for device in sd.query_devices():
            if preferred is not None and device["hostapi"] != preferred:
                continue
            if device["max_input_channels"] > 0 and device["name"] not in inputs:
                inputs.append(device["name"])
            if device["max_output_channels"] > 0 and device["name"] not in outputs:
                outputs.append(device["name"])
    except Exception:
        logger.exception("Device listing failed")
    return inputs, outputs


def _device_index(name: str, kind: str) -> int | None:
    """PortAudio index for a device name (\"\" = the Windows default device), preferring WASAPI.

    The same headset is listed once per host API (MME, DirectSound, WASAPI). MME cuts names at 31
    characters, so a plain name search used to land on DirectSound, which hands over sound in uneven
    pieces: about a third of the microphone was thrown away and friends heard a broken voice.
    """
    import sounddevice as sd

    channels_key = "max_input_channels" if kind == "input" else "max_output_channels"
    try:
        apis = sd.query_hostapis()
        wasapi = next((i for i, api in enumerate(apis) if "WASAPI" in api["name"]), None)
        devices = sd.query_devices()
    except Exception:
        logger.info("Device lookup failed", exc_info=True)
        return None
    if not name:
        if wasapi is not None:
            default = apis[wasapi]["default_input_device" if kind == "input" else "default_output_device"]
            if default is not None and default >= 0:
                return default
        return None
    matches = [i for i, d in enumerate(devices) if d["name"] == name and d[channels_key] > 0]
    if not matches:
        # Windows renames a re-plugged USB headset: "Микрофон (Razer …)" becomes "Микрофон (2- Razer …)".
        wanted = _plain_name(name)
        matches = [i for i, d in enumerate(devices) if _plain_name(d["name"]) == wanted and d[channels_key] > 0]
    preferred = [i for i in matches if wasapi is not None and devices[i]["hostapi"] == wasapi]
    if preferred:
        return preferred[0]
    if matches:
        return matches[0]
    return None  # unplugged: fall back to the default device


def _plain_name(name: str) -> str:
    import re

    return re.sub(r"\(\d+- ", "(", name).strip().lower()


def _extra_settings(device: int | None):
    """Shared-mode WASAPI only opens at the device's own rate unless told to convert."""
    import sounddevice as sd

    try:
        if device is None or "WASAPI" not in sd.query_hostapis(sd.query_devices(device)["hostapi"])["name"]:
            return None
    except Exception:
        return None
    try:
        return sd.WasapiSettings(auto_convert=True)
    except TypeError:  # sounddevice < 0.5
        return None


def _system_signature():
    """What Windows has plugged in right now, and which devices are the defaults, read through winmm.

    PortAudio only sees the devices that existed when it started, and a stream opened on the
    default device stays on that device. This cheap check (no PortAudio involved) lets the engine notice a
    headset being plugged back in, or a new default device, and reopen. None where unsupported.
    """
    import sys

    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _InCaps(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                    ("szPname", wintypes.WCHAR * 32), ("dwFormats", wintypes.DWORD),
                    ("wChannels", wintypes.WORD), ("wReserved1", wintypes.WORD)]

    class _OutCaps(ctypes.Structure):
        _fields_ = _InCaps._fields_ + [("dwSupport", wintypes.DWORD)]

    try:
        winmm = ctypes.WinDLL("winmm")
        signature = []
        for prefix, caps_type in (("waveIn", _InCaps), ("waveOut", _OutCaps)):
            count = getattr(winmm, prefix + "GetNumDevs")()
            names = []
            for index in range(count):
                caps = caps_type()
                if getattr(winmm, prefix + "GetDevCapsW")(index, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
                    names.append(caps.szPname)
            preferred, status = wintypes.DWORD(0xFFFFFFFF), wintypes.DWORD(0)
            # DRVM_MAPPER_PREFERRED_GET on WAVE_MAPPER: the current default device.
            getattr(winmm, prefix + "Message")(wintypes.HANDLE(0xFFFFFFFF), 0x2015, ctypes.byref(preferred),
                                               ctypes.byref(status))
            signature.append((tuple(names), preferred.value))
        return tuple(signature)
    except Exception:
        return None


class _Rechunker:
    """Turns pieces of any size into exact 10 ms frames. Some drivers (DirectSound, Bluetooth) deliver
    uneven blocks; dropping those lost up to a third of the sound."""

    def __init__(self, frame_bytes: int) -> None:
        self.frame_bytes = frame_bytes
        self._pending = bytearray()

    def push(self, data: bytes) -> list[bytes]:
        self._pending += data
        frames = []
        while len(self._pending) >= self.frame_bytes:
            frames.append(bytes(self._pending[:self.frame_bytes]))
            del self._pending[:self.frame_bytes]
        if len(self._pending) > self.frame_bytes * 50:  # never let a backlog build up
            self._pending.clear()
        return frames

    def clear(self) -> None:
        self._pending.clear()


SILENT_PEAK = 3          # raw int16 peak at or under this is "nothing at all" (about -80 dBFS)
SILENT_FIRST = 3.5       # a mic silent since it was opened is reopened this soon
SILENT_SECONDS = 8.0     # and again this often while it stays silent
SILENT_REOPENS = 4       # reopen tries per opening, the hint shows after the last one
PROCESSED_DEAD_SECONDS = 2.0  # the mic hears sound but processing outputs zeros this long: rebuild it
SOUND_BLOCKS = 30        # blocks with real sound (about 0.3 s) before a fresh mic counts as alive: opening
                         # a stuck Realtek mic gives one click and then zeros (Evgen, 2026-11-15 18:16)
# A mic set too loud in Windows or the headset app (PC3's Logitech G733, 2026-11-14/15: peaks at -0.6..-3 dBFS)
# leaves the echo/noise/gain processing no headroom: its output hit full scale and the voice came out harsh.
HOT_PEAK = 23197         # -3 dBFS: louder than this before processing is turned down (headroom guard)
HEADROOM_TARGET = 16422  # -6 dBFS: where the guard puts such peaks
HEADROOM_MIN = 0.25      # the guard never turns the mic down more than 12 dB
HEADROOM_RECOVER = 10 ** (0.15 / 20 / 100)  # +0.15 dB per second back towards no attenuation
OVERLOAD_PEAK = 29204    # -1 dBFS straight from the device: the mic is overloaded (often already clipped)
OVERLOAD_FRAMES = 30     # that many such 10 ms frames within OVERLOAD_WINDOW seconds shows a hint
OVERLOAD_WINDOW = 60.0
FULL_SCALE = 32700       # a processed sample this loud is clipping
# Our own automatic gain for the outgoing voice. WebRTC's (in the LiveKit module, no target setting) drove
# speech to exactly 0 dBFS nearly every minute for everyone (2026-11-15): loud syllables were cut off and
# friends heard rasping. Ours brings speech to about -20 dBFS RMS slowly and never past the limiter ceiling.
VOICE_TARGET_DBFS = -20.0
VOICE_MAX_GAIN = 4.0     # +12 dB for a quiet mic
VOICE_MIN_GAIN = 0.5
OUT_CEILING = 0.71       # -3 dBFS: the soft limiter keeps every outgoing peak under this


def _peak(frame: bytes) -> int:
    samples = np.frombuffer(frame, dtype=np.int16)
    return int(np.abs(samples.astype(np.int32)).max()) if samples.size else 0


class AudioEngine:
    def __init__(self, settings: AudioSettings, on_frame: Callable[[bytes], None],
                 on_problem: Callable[[str], None] = lambda message: None) -> None:
        self.settings = settings
        self.on_frame = on_frame
        self.on_problem = on_problem
        self.mixer = Mixer()
        self.gate = VoiceGate()
        self.muted = False
        self.ptt_pressed = False
        self.transmitting = False
        self.input_level_db = -96.0
        self._was_sending = False
        self._lock = threading.RLock()
        self._apm = None
        self._apm_config: tuple | None = None
        self._rnnoise: RNNoise | None = None
        self._input = None
        self._output = None
        self._running = False
        self._delay_ms = 40
        self._in_chunks = _Rechunker(FRAME_BYTES)
        self._out_pending = bytearray()
        self._reverse_extra: deque[bytes] = deque(maxlen=20)  # UI sounds played by the separate player
        self._opened_names: tuple[str, str] | None = None  # device settings the open streams were made for
        self._callbacks = 0          # capture callbacks so far, to notice a stream that silently stopped
        self._window_peak = 0        # loudest raw sample since the device watcher last looked
        self._window_out = 0         # loudest processed sample since the device watcher last looked
        self._window_apm = 0         # loudest sample after WebRTC processing, same window
        self._heard_since_open = False  # the mic has delivered real sound (not just a click) since opening
        self._sound_blocks = 0
        self._headroom = 1.0         # headroom guard gain applied before processing (1.0 = untouched)
        self._leveler = Leveler(VOICE_TARGET_DBFS, VOICE_MAX_GAIN, VOICE_MIN_GAIN)
        self._limiter = Limiter(ceiling=OUT_CEILING)
        self._window_overload = 0    # device frames at -1 dBFS or louder since the device watcher last looked
        self._monitor_stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._reset_counters()
        self.apply_settings(settings)

    # --- diagnostics ------------------------------------------------------------------------------

    def _reset_counters(self) -> None:
        # Written only by the audio callbacks, read and reset by stats(); plain ints are fine for that.
        self._frames_in = 0
        self._odd_blocks = 0
        self._frames_sent = 0
        self._frames_out = 0
        self._overflows = 0
        self._underflows = 0
        self._peak_db = -96.0
        self._raw_peak = 0         # loudest raw sample from the device, before any processing
        self._apm_peak = 0         # loudest sample after WebRTC processing (echo, noise, gain)
        self._out_peak = 0         # loudest sample after RNNoise, what the voice gate sees
        self._overload_frames = 0  # 10 ms frames at -1 dBFS or louder from the device (mic set too loud)
        self._clipped_frames = 0   # processed frames that hit full scale (heard as a harsh, cracking voice)
        self._sent_peak = 0        # loudest sample actually sent, after our gain and limiter
        self._min_headroom = 1.0   # strongest headroom guard attenuation in the period

    def stats(self) -> dict:
        """What the microphone and speakers did since the last call, for the log."""
        stats = {
            "running": self._running, "mic_frames": self._frames_in, "sent_frames": self._frames_sent,
            "speaker_frames": self._frames_out, "uneven_blocks": self._odd_blocks,
            "mic_peak_db": round(self._peak_db, 1),
            "raw_peak_db": round(20 * math.log10(max(self._raw_peak, 1) / 32767.0), 1),
            # Where a lost voice disappears: after WebRTC processing, then after RNNoise.
            "apm_peak_db": round(20 * math.log10(max(self._apm_peak, 1) / 32767.0), 1),
            "out_peak_db": round(20 * math.log10(max(self._out_peak, 1) / 32767.0), 1),
            "threshold_db": round(getattr(self.gate, "threshold_db", self.settings.threshold_db), 1),
            "sent_peak_db": round(20 * math.log10(max(self._sent_peak, 1) / 32767.0), 1),
            "overload_frames": self._overload_frames, "clipped_frames": self._clipped_frames,
            "headroom_db": round(20 * math.log10(max(self._min_headroom, 1e-3)), 1),
            "overflows": self._overflows, "underflows": self._underflows, "muted": self.muted,
            "mode": self.settings.mode,
        }
        self._reset_counters()
        return stats

    # --- settings ---------------------------------------------------------------------------------

    def apply_settings(self, settings: AudioSettings) -> None:
        with self._lock:
            # The settings object is shared with (and changed in place by) the app, so compare with what
            # the streams were opened for. Comparing with self.settings meant a newly chosen microphone or
            # headset only took effect after leaving and rejoining the call.
            restart = self._running and self._opened_names != (settings.input_device, settings.output_device)
            self.settings = settings
            self.mixer.master_volume = settings.output_volume
            self.mixer.user_volume = dict(settings.user_volumes)
            self.mixer.normalize = settings.normalize_voices
            self.gate.auto = settings.auto_threshold
            self.gate.manual_threshold_db = settings.threshold_db
            if settings.noise == NOISE_STRONG and self._rnnoise is None and RNNoise.available():
                self._rnnoise = RNNoise()
            elif settings.noise != NOISE_STRONG and self._rnnoise is not None:
                self._rnnoise.close()
                self._rnnoise = None
            self._ensure_apm()
        if restart:
            self.restart()

    def _new_processing(self) -> None:
        """Fresh echo/noise/gain processing and RNNoise, as at app start. They used to live as long as the
        app: once they got into a state that turned speech into zeros (Evgen, 2026-11-13 and 11-15),
        rejoining did not help, only restarting the app did. The audio callback holds its own reference
        to the old objects for the frame it is on, so swapping them here is safe."""
        self._apm_config = None
        self._ensure_apm()
        if self.settings.noise == NOISE_STRONG and RNNoise.available():
            try:
                self._rnnoise = RNNoise()
            except Exception:
                logger.warning("RNNoise could not be recreated", exc_info=True)

    def _ensure_apm(self) -> None:
        # "Strong" = WebRTC suppression plus RNNoise on top, so it is never weaker than "standard",
        # even on a machine where the RNNoise library is missing.
        config = (self.settings.echo_cancellation, self.settings.noise != NOISE_OFF, self.settings.auto_gain)
        if config == self._apm_config:
            return
        try:
            from livekit import rtc

            self._apm = rtc.AudioProcessingModule(
                echo_cancellation=config[0], noise_suppression=config[1], high_pass_filter=True,
                auto_gain_control=False)  # our own Leveler does it, see VOICE_TARGET_DBFS
            self._apm_config = config
        except Exception:
            logger.exception("WebRTC audio processing unavailable")
            self._apm = None

    # --- devices ----------------------------------------------------------------------------------

    def start(self) -> None:
        import sounddevice as sd

        with self._lock:
            if self._running:
                return
            try:
                self._refresh_device_list_if_missing()
                self._in_chunks.clear()
                self._out_pending.clear()
                self._heard_since_open = False
                self._sound_blocks = 0
                self._headroom = 1.0
                self._limiter.gain = 1.0
                self._new_processing()
                mic = _device_index(self.settings.input_device, "input")
                speakers = _device_index(self.settings.output_device, "output")
                missing = [name for name, index in ((self.settings.input_device, mic),
                                                    (self.settings.output_device, speakers)) if name and index is None]
                if missing:
                    # Unplugged: run on the default device for now. The device watcher reopens the chosen one
                    # as soon as Windows reports it back.
                    logger.warning("Chosen device not found (%s): using the default for now", ", ".join(missing))
                    self.on_problem("fallback")
                self._input = self._open(sd.RawInputStream, mic, self._capture)
                self._output = self._open(sd.RawOutputStream, speakers, self._playback)
                self._input.start()
                self._output.start()
                self._delay_ms = min(500, int((self._input.latency + self._output.latency) * 1000))
                self._running = True
                self._opened_names = (self.settings.input_device, self.settings.output_device)
                self._reset_counters()
                self._start_monitor()
                logger.info("Audio opened: mic %s, speakers %s, delay %d ms, noise %s, echo %s, agc %s",
                            _describe(self._input.device), _describe(self._output.device), self._delay_ms,
                            self.settings.noise, self.settings.echo_cancellation, self.settings.auto_gain)
            except Exception as exc:
                self._close_streams()
                logger.warning("Audio device could not be opened (mic %r, speakers %r): %s",
                               self.settings.input_device or "default", self.settings.output_device or "default", exc)
                raise RuntimeError(f"Не удалось открыть аудиоустройство: {exc}") from exc

    def _open(self, stream_class, device: int | None, callback):
        def make(dev, extra):
            return stream_class(samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES, channels=1, dtype="int16",
                                device=dev, latency=DEVICE_LATENCY, extra_settings=extra,
                                callback=callback, finished_callback=self._stream_finished)
        try:
            return make(device, _extra_settings(device))
        except Exception as exc:
            if device is None:
                raise
            # A device that refuses WASAPI (rare drivers): let PortAudio pick its default way.
            logger.warning("Opening device %s failed (%s); using the system default", device, exc)
            self.on_problem("fallback")
            return make(None, None)

    def _refresh_device_list_if_missing(self) -> None:
        """A device chosen by name but plugged in after PortAudio started is not in its list yet."""
        import sounddevice as sd

        try:
            known = {d["name"] for d in sd.query_devices()}
        except Exception:
            return
        wanted = {name for name in (self.settings.input_device, self.settings.output_device) if name}
        if wanted - known:
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                logger.debug("PortAudio re-init failed", exc_info=True)

    def _start_monitor(self) -> None:
        if self._monitor is not None and self._monitor.is_alive():
            return
        self._monitor_stop = threading.Event()
        stop = self._monitor_stop

        def watch() -> None:
            signature = _system_signature()
            pending = None  # a change must look the same on two checks in a row (Windows settles first)
            last_calls, quiet_since = self._callbacks, None
            silent_since, silence_reopens = None, 0
            dead_since = None
            rebuilt_check = 0  # checks left to report whether the voice came back after a rebuild
            overloads: deque[tuple[float, int]] = deque()  # (when, overloaded frames) per check
            overload_told = False
            while not stop.wait(1.5):
                if not self._running:
                    silent_since, silence_reopens = None, 0
                    overloads.clear()
                    continue
                # A mic set too loud: tell the person once per opening, the guard below can soften it
                # but not undo distortion that the headset itself already made.
                now = time.monotonic()
                over, self._window_overload = self._window_overload, 0
                if over and not self.muted:
                    overloads.append((now, over))
                while overloads and now - overloads[0][0] > OVERLOAD_WINDOW:
                    overloads.popleft()
                if not overload_told and sum(n for _, n in overloads) >= OVERLOAD_FRAMES:
                    overload_told = True
                    logger.warning("The microphone is overloaded: %d frames at -1 dBFS or louder within %d s",
                                   sum(n for _, n in overloads), int(OVERLOAD_WINDOW))
                    self.on_problem("mic_overload")
                # A microphone that has not delivered a single sound since it was opened is stuck: Windows
                # mute, a mic key on the laptop or headset, another app holding it, or a Realtek driver that
                # did not wake up (Evgen's first joins, 2026-11-13..15; rejoining helped). Reopen it soon, and
                # again while it stays silent, up to SILENT_REOPENS times.
                # Once it has delivered sound, later pure zeros are NOT a fault: Realtek drivers and Windows
                # "audio enhancements" cut pauses to exact zero. Treating that as stuck showed a "mic broken"
                # hint whenever someone was quiet, and every reopen cut their mic for a moment.
                raw_window, self._window_peak = self._window_peak, 0
                out_window, self._window_out = self._window_out, 0
                apm_window, self._window_apm = self._window_apm, 0
                if rebuilt_check:
                    rebuilt_check -= 1
                    if rebuilt_check == 0 or out_window > 1:
                        logger.info("After recreating the processing: raw peak %d, after WebRTC %d, out %d",
                                    raw_window, apm_window, out_window)
                        rebuilt_check = 0
                # The mic hears sound but our processing turns it into zeros: rebuild the processing.
                if not self.muted and raw_window > 300 and out_window <= 1:
                    if dead_since is None:
                        dead_since = time.monotonic()
                    elif time.monotonic() - dead_since > PROCESSED_DEAD_SECONDS:
                        # Which stage zeroes it: WebRTC (echo/noise/gain) or RNNoise after it.
                        logger.warning("The mic hears sound (raw peak %d) but processing outputs silence "
                                       "(after WebRTC %d, after RNNoise %d): recreating the processing",
                                       raw_window, apm_window, out_window)
                        self._new_processing()
                        dead_since = None
                        rebuilt_check = 4
                else:
                    dead_since = None
                if self._heard_since_open or self.muted:
                    silent_since, silence_reopens = None, 0
                elif silent_since is None:
                    silent_since = time.monotonic()
                elif (silence_reopens < SILENT_REOPENS
                      and time.monotonic() - silent_since > SILENT_FIRST + SILENT_SECONDS * silence_reopens):
                    silence_reopens += 1
                    logger.warning("The microphone has delivered only perfect silence since it was opened: "
                                   "reopening (%d)", silence_reopens)
                    self.on_problem("mic_silent" if silence_reopens < SILENT_REOPENS else "mic_silent_last")
                now_signature = _system_signature()
                if signature is None:
                    signature = now_signature
                elif now_signature is not None and now_signature != signature:
                    if pending != now_signature:
                        pending = now_signature
                        continue
                    signature, pending = now_signature, None
                    logger.info("Audio devices changed in Windows: reopening")
                    self.on_problem("devices_changed")
                    last_calls, quiet_since = self._callbacks, None
                    continue
                else:
                    pending = None
                # A stream on an unplugged device can simply stop calling back, with no error at all.
                if self._callbacks != last_calls:
                    last_calls, quiet_since = self._callbacks, None
                elif quiet_since is None:
                    quiet_since = time.monotonic()
                elif time.monotonic() - quiet_since > 3.0:
                    logger.warning("The microphone stopped delivering sound: reopening")
                    quiet_since = None
                    self.on_problem("device_lost")

        self._monitor = threading.Thread(target=watch, name="audio-devices", daemon=True)
        self._monitor.start()

    def add_reverse(self, frame: bytes) -> None:
        """A 10 ms frame another stream plays on the same speakers (UI sounds): the echo canceller has to
        hear it too, or a "Микрофон включен" from the speakers would go out through the microphone."""
        if self._running and len(frame) == FRAME_BYTES:
            self._reverse_extra.append(frame)

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._monitor_stop.set()
            self._monitor = None
            self._close_streams()
            self.mixer.clear()

    def restart(self, reinit: bool = True) -> None:
        """Re-open devices: after unplugging a headset or changing the Windows default device.

        reinit=False only reopens the streams (a stuck microphone): re-initialising PortAudio took up to
        10 s on a Realtek laptop, and the device list has not changed then anyway."""
        import sounddevice as sd

        with self._lock:
            was_running = self._running
            self._running = False
            self._close_streams()
            # PortAudio caches the device list; re-initialise it to see newly plugged devices.
            if reinit:
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    logger.debug("PortAudio re-init failed", exc_info=True)
        if was_running:
            self.start()

    def _close_streams(self) -> None:
        for stream in (self._input, self._output):
            if stream is not None:
                try:
                    stream.abort(ignore_errors=True)
                    stream.close(ignore_errors=True)
                except Exception:
                    pass
        self._input = self._output = None

    def _stream_finished(self) -> None:
        if self._running:
            logger.warning("Audio stream stopped by the system (device unplugged or taken over)")
            self.on_problem("device_lost")

    # --- real-time callbacks ----------------------------------------------------------------------

    def _capture(self, indata, frames, time_info, status) -> None:
        boost_voice_thread()
        if status and status.input_overflow:
            self._overflows += 1
        self._callbacks += 1
        if frames != FRAME_SAMPLES:
            self._odd_blocks += 1
        raw = np.frombuffer(indata, dtype=np.int16)
        if raw.size:
            peak = int(np.abs(raw.astype(np.int32)).max())
            if peak > self._raw_peak:
                self._raw_peak = peak
            if peak > self._window_peak:
                self._window_peak = peak
            if peak > SILENT_PEAK:
                self._sound_blocks += 1
                if self._sound_blocks >= SOUND_BLOCKS:
                    self._heard_since_open = True
        for frame in self._in_chunks.push(bytes(indata)):
            self._frames_in += 1
            self._process_frame(frame)

    def _guard_headroom(self, frame: bytes) -> bytes:
        """Turn a too-loud mic down before processing, quickly on a loud peak and back up slowly.

        The gain glides across the frame (no step in the waveform) and changes rarely once it has
        settled, so echo cancellation adapts to it like to a volume change in Windows."""
        peak = _peak(frame)
        previous = self._headroom
        if peak * previous > HOT_PEAK:
            self._headroom = max(HEADROOM_MIN, HEADROOM_TARGET / peak)
        elif previous < 1.0:
            self._headroom = min(1.0, previous * HEADROOM_RECOVER)
        if self._headroom < self._min_headroom:
            self._min_headroom = self._headroom
        if previous >= 0.999 and self._headroom >= 0.999:
            return frame
        ramp = np.linspace(previous, self._headroom, FRAME_SAMPLES, dtype=np.float32)
        return to_bytes(to_float(frame) * ramp)

    def _process_frame(self, frame: bytes) -> None:
        frame = apply_gain(frame, self.settings.input_gain)
        if _peak(frame) >= OVERLOAD_PEAK:
            self._overload_frames += 1
            self._window_overload += 1
        frame = self._guard_headroom(frame)
        apm = self._apm
        if apm is not None:
            try:
                from livekit import rtc

                audio = rtc.AudioFrame(frame, SAMPLE_RATE, 1, FRAME_SAMPLES)
                apm.set_stream_delay_ms(self._delay_ms)
                apm.process_stream(audio)
                frame = bytes(audio.data.cast("B"))
            except Exception:
                pass
        peak = _peak(frame)
        if peak >= FULL_SCALE:
            self._clipped_frames += 1
        if peak > self._apm_peak:
            self._apm_peak = peak
        if peak > self._window_apm:
            self._window_apm = peak
        samples = to_float(frame)
        probability = None
        rnnoise = self._rnnoise
        # RNNoise runs on every frame. 0.5.7 skipped near-silent ones to save processor time, but it is a
        # recurrent network with overlapping windows: resumed on a word's first syllable with the state of
        # the last word, it rasped at word starts for everyone (2026-11-15).
        if rnnoise is not None:
            samples, probability = rnnoise.process(samples)
            frame = to_bytes(samples)
            peak = _peak(frame)
        if peak > self._out_peak:
            self._out_peak = peak
        if peak > self._window_out:
            self._window_out = peak
        is_voice = self.gate.process(samples, probability)
        self.input_level_db = self.gate.level_db
        if self.input_level_db > self._peak_db:
            self._peak_db = self.input_level_db
        if self.settings.mode == MODE_PTT:
            send = self.ptt_pressed
        else:
            send = is_voice
        send = send and not self.muted
        self.transmitting = send
        was_sending, self._was_sending = self._was_sending, send
        if self.muted:
            return  # the track itself is muted; nothing goes out
        if send or was_sending:
            if self.settings.auto_gain:
                samples = self._leveler.process(samples, learn=send)
            samples = self._limiter.process(samples)
            frame = to_bytes(samples)
            if _peak(frame) > self._sent_peak:
                self._sent_peak = _peak(frame)
        # The stream never stops while unmuted: between words it carries silence (Opus DTX makes that
        # almost free). It used to stop dead and start again on the next word, and friends' jitter
        # buffers heard every restart as a crackle. The voice fades in and out over 10 ms, so the gate
        # opening or closing in the middle of a waveform is no click either.
        if send:
            self._frames_sent += 1
            self.on_frame(frame if was_sending else fade(frame, FADE_IN))
        elif was_sending:
            self.on_frame(fade(frame, FADE_OUT))
        else:
            self.on_frame(SILENCE)

    def _playback(self, outdata, frames, time_info, status) -> None:
        boost_voice_thread()
        if status and status.output_underflow:
            self._underflows += 1
        if frames != FRAME_SAMPLES:
            self._odd_blocks += 1
        needed = frames * 2
        # Whole 10 ms frames from the mixer, whatever block size the driver asks for (it used to get
        # silence for any other size).
        while len(self._out_pending) < needed:
            mixed = self.mixer.mix()
            self._frames_out += 1
            self._out_pending += mixed
            apm = self._apm
            if apm is not None and self.settings.echo_cancellation:
                try:
                    from livekit import rtc

                    reverse = mixed
                    if self._reverse_extra:
                        reverse = to_bytes(to_float(mixed) + to_float(self._reverse_extra.popleft()))
                    apm.process_reverse_stream(rtc.AudioFrame(reverse, SAMPLE_RATE, 1, FRAME_SAMPLES))
                except Exception:
                    pass
        outdata[:] = bytes(self._out_pending[:needed])
        del self._out_pending[:needed]


def _describe(device) -> str:
    """'Name (host API)' of the device PortAudio actually opened."""
    try:
        import sounddevice as sd

        info = sd.query_devices(device)
        api = sd.query_hostapis(info["hostapi"])["name"]
        return f"{info['name']!r} ({api}, {int(info['default_samplerate'])} Hz)"
    except Exception:
        return repr(device)


def mic_test_level(samples: np.ndarray) -> float:
    """0..1 meter value for the settings screen."""
    from .dsp import rms_dbfs

    return max(0.0, min(1.0, (rms_dbfs(samples) + 60.0) / 60.0))


__all__ = ["AudioEngine", "AudioSettings", "list_devices", "FRAME_BYTES", "NOISE_OFF", "NOISE_STANDARD",
           "NOISE_STRONG", "MODE_PTT", "MODE_VOICE"]
