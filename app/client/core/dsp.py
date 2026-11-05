"""Audio processing that does not touch devices or the network, so it can be tested directly.

All audio is 48 kHz mono int16 in 10 ms frames (480 samples), the native WebRTC frame size.
"""
from __future__ import annotations

import ctypes
import importlib.util
import math
import os
import platform
import sys
import threading
from collections import deque
from pathlib import Path

import numpy as np

SAMPLE_RATE = 48_000
FRAME_SAMPLES = 480
FRAME_BYTES = FRAME_SAMPLES * 2
INT16_MAX = 32767.0
SILENCE = bytes(FRAME_BYTES)


def to_float(frame: bytes) -> np.ndarray:
    return np.frombuffer(frame, dtype=np.int16).astype(np.float32)


def to_bytes(samples: np.ndarray) -> bytes:
    return np.clip(samples, -INT16_MAX, INT16_MAX).astype(np.int16).tobytes()


def rms_dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -96.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return 20 * math.log10(max(rms, 1.0) / INT16_MAX)


def apply_gain(frame: bytes, gain: float) -> bytes:
    if abs(gain - 1.0) < 1e-3:
        return frame
    return to_bytes(to_float(frame) * gain)


class Limiter:
    """Brick-wall peak limiter with a soft knee: loud is allowed, distortion is not.

    Gain drops instantly when a peak would clip and recovers over ~80 ms, so raising a quiet
    friend to 300% never crackles.
    """

    def __init__(self, ceiling: float = 0.89, release_ms: float = 80.0) -> None:
        self.ceiling = ceiling * INT16_MAX
        self.gain = 1.0
        self.release = math.exp(-10.0 / release_ms)

    def process(self, samples: np.ndarray) -> np.ndarray:
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        target = min(1.0, self.ceiling / peak) if peak > 0 else 1.0
        if target < self.gain:
            self.gain = target
        else:
            self.gain = target + (self.gain - target) * self.release
        out = samples * self.gain
        # Soft clip whatever is left above the ceiling (between frames the envelope can lag).
        knee = self.ceiling
        over = np.abs(out) > knee
        if np.any(over):
            excess = (np.abs(out[over]) - knee) / (INT16_MAX - knee)
            out[over] = np.sign(out[over]) * (knee + (INT16_MAX - knee) * np.tanh(excess))
        return out


class Leveler:
    """Slow per-speaker automatic gain: brings quiet and loud friends to a similar loudness."""

    def __init__(self, target_dbfs: float = -20.0, max_gain: float = 4.0, min_gain: float = 0.5) -> None:
        self.target = target_dbfs
        self.max_gain, self.min_gain = max_gain, min_gain
        self.level_db = target_dbfs
        self.gain = 1.0

    def process(self, samples: np.ndarray) -> np.ndarray:
        level = rms_dbfs(samples)
        if level > -55.0:  # only learn from speech, not from silence between words
            self.level_db += (level - self.level_db) * 0.02
            wanted = 10 ** ((self.target - self.level_db) / 20)
            wanted = min(self.max_gain, max(self.min_gain, wanted))
            self.gain += (wanted - self.gain) * 0.05
        return samples * self.gain


class VoiceGate:
    """Voice activation with an automatic threshold that follows the room's noise floor."""

    HANGOVER_FRAMES = 30  # keep transmitting 300 ms after speech so word endings are not cut

    def __init__(self) -> None:
        self.auto = True
        self.manual_threshold_db = -45.0
        self.noise_floor_db = -60.0
        self._hold = 0
        self.level_db = -96.0
        self.open = False

    @property
    def threshold_db(self) -> float:
        if not self.auto:
            return self.manual_threshold_db
        return min(-28.0, max(-58.0, self.noise_floor_db + 12.0))

    def process(self, samples: np.ndarray, speech_probability: float | None = None) -> bool:
        level = rms_dbfs(samples)
        self.level_db = level
        # The floor drops quickly to quiet moments and rises slowly, so speech does not raise it.
        if level < self.noise_floor_db:
            self.noise_floor_db += (level - self.noise_floor_db) * 0.2
        else:
            self.noise_floor_db += (level - self.noise_floor_db) * 0.002
        voiced = level >= self.threshold_db
        if speech_probability is not None and self.auto:
            voiced = voiced and speech_probability >= 0.35 or speech_probability >= 0.85
        if voiced:
            self._hold = self.HANGOVER_FRAMES
        elif self._hold:
            self._hold -= 1
        self.open = voiced or self._hold > 0
        return self.open


class _SpeakerBuffer:
    __slots__ = ("frames", "leveler", "started")

    def __init__(self) -> None:
        self.frames: deque[bytes] = deque()
        self.leveler = Leveler()
        self.started = False


class Mixer:
    """Mixes every speaker into one output stream.

    Each speaker has a tiny jitter buffer (starts after 2 frames, never holds more than 120 ms, so
    latency cannot pile up), a volume from 0 to 300%, optional loudness levelling, and the sum passes
    through a limiter.
    """

    START_FRAMES = 2
    MAX_FRAMES = 12

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._speakers: dict[int, _SpeakerBuffer] = {}
        self._effects: deque[tuple[bytes, bool]] = deque(maxlen=600)
        self.master_volume = 1.0
        self.user_volume: dict[int, float] = {}
        self.muted_users: set[int] = set()
        self.deafened = False
        self.normalize = True
        self.limiter = Limiter()
        self.output_level_db = -96.0

    def put(self, user_id: int, frame: bytes) -> None:
        if len(frame) != FRAME_BYTES:
            return
        with self._lock:
            buffer = self._speakers.setdefault(user_id, _SpeakerBuffer())
            buffer.frames.append(frame)
            while len(buffer.frames) > self.MAX_FRAMES:
                buffer.frames.popleft()

    def play_effect(self, pcm: bytes, always: bool = False) -> None:
        """Short UI sounds. Join/leave chimes are skipped while deafened; `always` is for the spoken
        mic/sound prompts, which must be heard right after you deafen yourself. A new sound replaces one
        still playing, so quick clicks never queue up a long tail."""
        with self._lock:
            self._effects.clear()
            for offset in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
                self._effects.append((pcm[offset:offset + FRAME_BYTES], always))

    def remove(self, user_id: int) -> None:
        with self._lock:
            self._speakers.pop(user_id, None)

    def clear(self) -> None:
        with self._lock:
            self._speakers.clear()
            self._effects.clear()

    def mix(self) -> bytes:
        with self._lock:
            total = np.zeros(FRAME_SAMPLES, dtype=np.float32)
            active = False
            for user_id, buffer in self._speakers.items():
                if not buffer.started:
                    if len(buffer.frames) < self.START_FRAMES:
                        continue
                    buffer.started = True
                if not buffer.frames:
                    buffer.started = False  # underrun: rebuffer quietly instead of crackling
                    continue
                frame = buffer.frames.popleft()
                if self.deafened or user_id in self.muted_users:
                    continue
                samples = to_float(frame)
                if self.normalize:
                    samples = buffer.leveler.process(samples)
                total += samples * self.user_volume.get(user_id, 1.0)
                active = True
            if self._effects:
                effect, always = self._effects.popleft()
                if always or not self.deafened:
                    total += to_float(effect) * (0.9 if always else 0.6)
                    active = True
        if not active:
            self.output_level_db = -96.0
            self.limiter.gain = 1.0
            return SILENCE
        total *= self.master_volume
        total = self.limiter.process(total)
        self.output_level_db = rms_dbfs(total)
        return to_bytes(total)


class RNNoise:
    """Neural noise suppression (keyboard, fans, background). Optional: the native library ships with
    the `pyrnnoise` wheel, loaded directly so its heavy Python dependencies are not needed."""

    def __init__(self) -> None:
        self._lib = _load_rnnoise()
        if self._lib is None:
            raise RuntimeError("RNNoise is not available")
        self._state = self._lib.rnnoise_create(None)
        self._buffer = (ctypes.c_float * FRAME_SAMPLES)()

    @staticmethod
    def available() -> bool:
        return _load_rnnoise() is not None

    def process(self, samples: np.ndarray) -> tuple[np.ndarray, float]:
        ctypes.memmove(self._buffer, samples.astype(np.float32).ctypes.data, FRAME_SAMPLES * 4)
        probability = self._lib.rnnoise_process_frame(self._state, self._buffer, self._buffer)
        out = np.frombuffer(self._buffer, dtype=np.float32, count=FRAME_SAMPLES).copy()
        return out, float(probability)

    def close(self) -> None:
        if self._state:
            self._lib.rnnoise_destroy(self._state)
            self._state = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


_RNNOISE_LIB: ctypes.CDLL | None | bool = False


def _load_rnnoise() -> ctypes.CDLL | None:
    global _RNNOISE_LIB
    if _RNNOISE_LIB is not False:
        return _RNNOISE_LIB  # type: ignore[return-value]
    _RNNOISE_LIB = None
    names = {"Windows": "rnnoise.dll", "Darwin": "librnnoise.dylib"}.get(platform.system(), "librnnoise.so")
    candidates = []
    spec = importlib.util.find_spec("pyrnnoise")
    if spec and spec.submodule_search_locations:
        candidates += [Path(p) / names for p in spec.submodule_search_locations]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(getattr(sys, "_MEIPASS", ".")) / "pyrnnoise" / names)
    if os.getenv("VOICECHAT_RNNOISE_LIB"):
        candidates.insert(0, Path(os.environ["VOICECHAT_RNNOISE_LIB"]))
    for path in candidates:
        if path.is_file():
            try:
                lib = ctypes.CDLL(str(path))
            except OSError:
                continue
            lib.rnnoise_create.argtypes = [ctypes.c_void_p]
            lib.rnnoise_create.restype = ctypes.c_void_p
            lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
            lib.rnnoise_process_frame.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float),
                                                  ctypes.POINTER(ctypes.c_float)]
            lib.rnnoise_process_frame.restype = ctypes.c_float
            _RNNOISE_LIB = lib
            break
    return _RNNOISE_LIB  # type: ignore[return-value]
