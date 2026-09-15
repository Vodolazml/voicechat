from __future__ import annotations

from collections import deque
from threading import Lock
from time import monotonic
from typing import Callable

import numpy as np


class AudioMixer:
    """Bounded per-speaker queues, mixed on the output device's clock."""

    def __init__(self, frame_bytes: int, max_frames: int = 6) -> None:
        self.frame_bytes = frame_bytes
        self.max_frames = max_frames
        self._queues: dict[int, deque] = {}
        self._lock = Lock()

    def put(self, user_id: int, pcm: bytes) -> None:
        if len(pcm) != self.frame_bytes:
            return
        with self._lock:
            self._queues.setdefault(user_id, deque(maxlen=self.max_frames)).append((monotonic(), pcm))

    def clear(self) -> None:
        with self._lock:
            self._queues.clear()

    def render(self, muted: Callable[[int], bool], volume: Callable[[int], int]) -> tuple[bytes, set[int]]:
        mixed = np.zeros(self.frame_bytes // 2, dtype=np.float32)
        audible = set()
        now = monotonic()
        with self._lock:
            for user_id, frames in list(self._queues.items()):
                while frames and now - frames[0][0] > 0.2:
                    frames.popleft()
                if not frames:
                    self._queues.pop(user_id, None)
                    continue
                _, pcm = frames.popleft()
                gain = 0 if muted(user_id) else max(0, min(200, volume(user_id))) / 100
                samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32) * gain
                if np.any(samples):
                    audible.add(user_id)
                mixed += samples
        return np.clip(mixed, -32768, 32767).astype('<i2').tobytes(), audible
