from __future__ import annotations

import threading
from time import sleep

import numpy as np

from .audio_mixer import AudioMixer

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 320  # 20ms at 16kHz, matches voice_audio.BLOCKSIZE / FRAME_BYTES
NOTIFICATION_SENDER_ID = -1


def _note(freq: float, duration_ms: int, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    count = int(sample_rate * duration_ms / 1000)
    t = np.arange(count) / sample_rate
    fade = min(30, duration_ms // 3)
    fade_count = max(1, int(sample_rate * fade / 1000))
    envelope = np.ones(count, dtype=np.float32)
    envelope[:fade_count] = np.linspace(0.0, 1.0, fade_count, dtype=np.float32)
    envelope[-fade_count:] = np.minimum(envelope[-fade_count:], np.linspace(1.0, 0.0, fade_count, dtype=np.float32))
    return np.sin(2 * np.pi * freq * t).astype(np.float32) * envelope


def _chime_frames(frequencies: tuple[float, ...], note_ms: int = 90) -> list[bytes]:
    """Pre-slices a short chime into 20ms PCM frames matching the voice mixer's frame size."""
    samples = np.concatenate([_note(freq, note_ms) for freq in frequencies]) * 0.28
    pcm = np.clip(samples * 32767, -32768, 32767).astype("<i2")
    frames = []
    for offset in range(0, len(pcm), FRAME_SAMPLES):
        chunk = pcm[offset:offset + FRAME_SAMPLES]
        if len(chunk) < FRAME_SAMPLES:
            chunk = np.pad(chunk, (0, FRAME_SAMPLES - len(chunk)))
        frames.append(chunk.tobytes())
    return frames


_JOIN_FRAMES = _chime_frames((523.25, 783.99))
_LEAVE_FRAMES = _chime_frames((587.33, 392.00))


def _feed(mixer: AudioMixer, frames: list[bytes]) -> None:
    def run() -> None:
        for frame in frames:
            mixer.put(NOTIFICATION_SENDER_ID, frame)
            sleep(0.02)

    threading.Thread(target=run, name="voice-notify-sound", daemon=True).start()


def play_join_sound(mixer: AudioMixer | None) -> None:
    """Mixes a short chime into the live call's own output stream.

    Deliberately does NOT open a separate sounddevice stream: doing so used to
    fight the call's output stream for the audio device and caused audible
    glitches in the ongoing call every time someone joined or left.
    """
    if mixer is not None:
        _feed(mixer, _JOIN_FRAMES)


def play_leave_sound(mixer: AudioMixer | None) -> None:
    if mixer is not None:
        _feed(mixer, _LEAVE_FRAMES)
