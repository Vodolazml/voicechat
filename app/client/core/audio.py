"""Microphone and speakers.

Capture runs in the PortAudio callback: gain → WebRTC processing (echo cancellation, noise
suppression, auto gain) → optional RNNoise → voice gate / push-to-talk. Each processed 10 ms frame is
handed to `on_frame` immediately; nothing waits on timers, so a minimised window or a busy game cannot
add delay. Playback pulls mixed audio from the Mixer in the output callback.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .dsp import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE, Mixer, RNNoise, VoiceGate, apply_gain, to_bytes, to_float

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
    import sounddevice as sd

    if not name:
        return None
    channels_key = "max_input_channels" if kind == "input" else "max_output_channels"
    for index, device in enumerate(sd.query_devices()):
        if device["name"] == name and device[channels_key] > 0:
            return index
    return None  # unplugged: fall back to the default device


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
        self._lock = threading.RLock()
        self._apm = None
        self._apm_config: tuple | None = None
        self._rnnoise: RNNoise | None = None
        self._input = None
        self._output = None
        self._running = False
        self._delay_ms = 40
        self.apply_settings(settings)

    # --- settings ---------------------------------------------------------------------------------

    def apply_settings(self, settings: AudioSettings) -> None:
        with self._lock:
            restart = self._running and (
                settings.input_device != self.settings.input_device
                or settings.output_device != self.settings.output_device)
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
                auto_gain_control=config[2])
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
                self._input = sd.RawInputStream(
                    samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES, channels=1, dtype="int16",
                    device=_device_index(self.settings.input_device, "input"), latency=DEVICE_LATENCY,
                    callback=self._capture, finished_callback=self._stream_finished)
                self._output = sd.RawOutputStream(
                    samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES, channels=1, dtype="int16",
                    device=_device_index(self.settings.output_device, "output"), latency=DEVICE_LATENCY,
                    callback=self._playback, finished_callback=self._stream_finished)
                self._input.start()
                self._output.start()
                self._delay_ms = min(500, int((self._input.latency + self._output.latency) * 1000))
                self._running = True
            except Exception as exc:
                self._close_streams()
                raise RuntimeError(f"Не удалось открыть аудиоустройство: {exc}") from exc

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._close_streams()
            self.mixer.clear()

    def restart(self) -> None:
        """Re-open devices: after unplugging a headset or changing the Windows default device."""
        import sounddevice as sd

        with self._lock:
            was_running = self._running
            self._running = False
            self._close_streams()
            # PortAudio caches the device list; re-initialise it to see newly plugged devices.
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
            self.on_problem("device_lost")

    # --- real-time callbacks ----------------------------------------------------------------------

    def _capture(self, indata, frames, time_info, status) -> None:
        if frames != FRAME_SAMPLES:
            return
        frame = apply_gain(bytes(indata), self.settings.input_gain)
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
        samples = to_float(frame)
        probability = None
        rnnoise = self._rnnoise
        if rnnoise is not None:
            samples, probability = rnnoise.process(samples)
            frame = to_bytes(samples)
        is_voice = self.gate.process(samples, probability)
        self.input_level_db = self.gate.level_db
        if self.settings.mode == MODE_PTT:
            send = self.ptt_pressed
        else:
            send = is_voice
        send = send and not self.muted
        self.transmitting = send
        if send:
            self.on_frame(frame)

    def _playback(self, outdata, frames, time_info, status) -> None:
        mixed = self.mixer.mix() if frames == FRAME_SAMPLES else bytes(frames * 2)
        outdata[:] = mixed
        apm = self._apm
        if apm is not None and frames == FRAME_SAMPLES and self.settings.echo_cancellation:
            try:
                from livekit import rtc

                apm.process_reverse_stream(rtc.AudioFrame(mixed, SAMPLE_RATE, 1, FRAME_SAMPLES))
            except Exception:
                pass


def mic_test_level(samples: np.ndarray) -> float:
    """0..1 meter value for the settings screen."""
    from .dsp import rms_dbfs

    return max(0.0, min(1.0, (rms_dbfs(samples) + 60.0) / 60.0))


__all__ = ["AudioEngine", "AudioSettings", "list_devices", "FRAME_BYTES", "NOISE_OFF", "NOISE_STANDARD",
           "NOISE_STRONG", "MODE_PTT", "MODE_VOICE"]
