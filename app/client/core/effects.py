"""UI sounds (joins, leaves, "включен"/"выключен") on their own output stream, apart from the call.

They used to be mixed into the call's audio, so every reopen of the call's devices (joining, leaving,
switching channels, a re-plugged headset) wiped a sound that was still playing, and your own "left"
sound had to open a device in the very moment the call closed its own. This player keeps one stream
of its own: it opens on the first sound (or ahead of time with `warm`), follows the chosen output
device, and closes itself after a while without sounds.

Sounds play in lanes. A "replace" lane (the mic and sound buttons) cuts its current sound short, so
quick clicks answer at once. A queue lane (joins and leaves) plays one sound after another and drops
a sound that is already playing or waiting: five friends joining at once is one announcement.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Callable

import numpy as np

from .dsp import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE

logger = logging.getLogger("voicechat.sounds")

IDLE_CLOSE_SECONDS = 30.0


class _Lane:
    def __init__(self) -> None:
        self.current: tuple[str, np.ndarray] | None = None  # (key, samples)
        self.position = 0
        self.waiting: deque[tuple[str, np.ndarray]] = deque()

    def busy_with(self, key: str) -> bool:
        return (self.current is not None and self.current[0] == key) or any(k == key for k, _ in self.waiting)


class SoundPlayer:
    def __init__(self, output_device: Callable[[], str], volume: Callable[[], float] = lambda: 1.0,
                 reverse_sink: Callable[[bytes], None] | None = None, stream_factory=None) -> None:
        self._output_device = output_device       # the device name from the settings ("" = default)
        self._volume = volume
        self.reverse_sink = reverse_sink          # the call's echo canceller hears what we play
        self._stream_factory = stream_factory or _open_stream
        self._lock = threading.Lock()
        self._lanes: dict[str, _Lane] = {}
        self._stream = None
        self._device_name: str | None = None
        self._reverse = bytearray()
        self._idle_timer: threading.Timer | None = None

    # --- public -----------------------------------------------------------------------------------

    def play(self, pcm: bytes, lane: str = "ui", key: str = "", replace: bool = False, gain: float = 0.9) -> bool:
        """Plays 48 kHz mono int16 `pcm`. Returns False when it was dropped as a duplicate."""
        if not pcm:
            return False
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) * (gain / 32768.0)
        key = key or str(id(pcm))
        with self._lock:
            state = self._lanes.setdefault(lane, _Lane())
            if replace:
                state.current, state.position = (key, samples), 0
                state.waiting.clear()
            elif state.busy_with(key):
                return False
            elif state.current is None:
                state.current, state.position = (key, samples), 0
            else:
                state.waiting.append((key, samples))
        self._ensure_stream()
        return True

    def warm(self) -> None:
        """Opens the stream ahead of time, so the next sound starts without the device's open delay."""
        self._ensure_stream()

    def stop(self) -> None:
        with self._lock:
            self._lanes.clear()
            stream, self._stream = self._stream, None
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
        _close(stream)

    def is_playing(self, lane: str | None = None) -> bool:
        with self._lock:
            lanes = [self._lanes.get(lane)] if lane else list(self._lanes.values())
            return any(s is not None and s.current is not None for s in lanes)

    # --- stream -----------------------------------------------------------------------------------

    def _ensure_stream(self) -> None:
        name = self._output_device() or ""
        with self._lock:
            stale = self._stream if self._stream is not None and self._device_name != name else None
            if stale is not None:
                self._stream = None
            need = self._stream is None
        _close(stale)
        if need:
            try:
                stream = self._stream_factory(name, self._fill, self._finished)
            except Exception:
                logger.warning("Sound output could not be opened", exc_info=True)
                with self._lock:
                    self._lanes.clear()
                return
            with self._lock:
                if self._stream is None:
                    self._stream, self._device_name = stream, name
                    stream = None
            _close(stream)  # another thread opened one first
        self._arm_idle_close()

    def _arm_idle_close(self) -> None:
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
            timer = threading.Timer(IDLE_CLOSE_SECONDS, self._close_if_idle)
            timer.daemon = True
            self._idle_timer = timer
        timer.start()

    def _close_if_idle(self) -> None:
        if self.is_playing():
            self._arm_idle_close()
            return
        with self._lock:
            stream, self._stream = self._stream, None
            self._idle_timer = None
        _close(stream)

    def _finished(self) -> None:
        # The device went away (unplugged): the next sound opens whatever is chosen or default then.
        with self._lock:
            self._stream = None

    def _fill(self, frames: int) -> bytes:
        """Real-time callback: the next `frames` samples of every lane, summed."""
        out = np.zeros(frames, dtype=np.float32)
        with self._lock:
            for state in self._lanes.values():
                filled = 0
                while filled < frames and state.current is not None:
                    samples = state.current[1]
                    take = min(frames - filled, len(samples) - state.position)
                    out[filled:filled + take] += samples[state.position:state.position + take]
                    filled += take
                    state.position += take
                    if state.position >= len(samples):
                        state.current = state.waiting.popleft() if state.waiting else None
                        state.position = 0
        volume = max(0.0, min(2.0, float(self._volume())))
        pcm = np.rint(np.clip(out * volume * 32768.0, -32768, 32767)).astype(np.int16).tobytes()
        sink = self.reverse_sink
        if sink is not None:
            self._reverse += pcm
            while len(self._reverse) >= FRAME_BYTES:
                frame = bytes(self._reverse[:FRAME_BYTES])
                del self._reverse[:FRAME_BYTES]
                try:
                    sink(frame)
                except Exception:
                    pass
        return pcm


def _close(stream) -> None:
    if stream is None:
        return
    try:
        stream.abort(ignore_errors=True)
        stream.close(ignore_errors=True)
    except Exception:
        pass


def _open_stream(device_name: str, fill: Callable[[int], bytes], finished: Callable[[], None]):
    import sounddevice as sd

    from .audio import _device_index, _extra_settings

    def callback(outdata, frames, time_info, status) -> None:
        outdata[:] = fill(frames)

    def make(device):
        return sd.RawOutputStream(samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES, channels=1, dtype="int16",
                                  device=device, latency="low", extra_settings=_extra_settings(device),
                                  callback=callback, finished_callback=finished)

    device = _device_index(device_name, "output")
    try:
        stream = make(device)
    except Exception:
        if device is None:
            raise
        stream = make(None)
    stream.start()
    return stream
