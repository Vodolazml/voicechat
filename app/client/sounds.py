from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 44_100


def _note(freq: float, duration_ms: int, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    count = int(sample_rate * duration_ms / 1000)
    t = np.arange(count) / sample_rate
    fade = min(30, duration_ms // 3)
    fade_count = max(1, int(sample_rate * fade / 1000))
    envelope = np.ones(count, dtype=np.float32)
    envelope[:fade_count] = np.linspace(0.0, 1.0, fade_count, dtype=np.float32)
    envelope[-fade_count:] = np.minimum(envelope[-fade_count:], np.linspace(1.0, 0.0, fade_count, dtype=np.float32))
    return (np.sin(2 * np.pi * freq * t).astype(np.float32) * envelope)


def _chime(frequencies: tuple[float, ...], note_ms: int = 85) -> np.ndarray:
    return np.concatenate([_note(freq, note_ms) for freq in frequencies]) * 0.22


_JOIN_TONE = _chime((523.25, 783.99))
_LEAVE_TONE = _chime((587.33, 392.00))


def _play(samples: np.ndarray, device: int | None) -> None:
    def run() -> None:
        try:
            sd.play(samples, SAMPLE_RATE, device=device, blocking=True)
        except Exception:
            pass

    threading.Thread(target=run, name="voice-notify-sound", daemon=True).start()


def play_join_sound(device: int | None = None) -> None:
    _play(_JOIN_TONE, device)


def play_leave_sound(device: int | None = None) -> None:
    _play(_LEAVE_TONE, device)
