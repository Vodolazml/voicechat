"""Measures the card encoder for screen shares on this computer: python -m deploy.nvenc_bench [W H FPS SECONDS]

Feeds changing synthetic BGRA frames to NvencPipe at the given rate (no network, no LiveKit room) and prints
encoded frames per second, average frame size, and this process's CPU use."""
import sys
import time
import types

import numpy as np

from app.client.core import nvenc


class Sink:
    def __init__(self):
        self.count = self.bytes = 0

    def take_feedback(self):
        return types.SimpleNamespace(keyframe_requested=False, rate_control=None)

    def capture_frame(self, frame):
        self.count += 1
        self.bytes += len(frame.data)
        return True


def main() -> None:
    width, height, fps, seconds = (int(x) for x in (sys.argv[1:5] + ["2560", "1440", "60", "20"][len(sys.argv[1:5]):]))
    print("NVENC available:", nvenc.available())
    base = np.random.default_rng(1).integers(0, 255, (height, width, 4), dtype=np.uint8)
    sink = Sink()
    pipe = nvenc.NvencPipe(sink, width, height, fps, 12_000_000, on_failure=lambda r: print("FAILED:", r))
    cpu0, wall0 = time.process_time(), time.perf_counter()
    for i in range(fps * seconds):
        frame = np.roll(base, i * 8, axis=1)  # moving picture, like a game
        pipe.capture_bgra(frame.tobytes())
        time.sleep(max(0.0, wall0 + (i + 1) / fps - time.perf_counter()))
    time.sleep(0.5)
    pipe.close()
    wall, cpu = time.perf_counter() - wall0, time.process_time() - cpu0
    print(f"{width}x{height}@{fps}: encoded {sink.count} frames in {wall:.1f} s = {sink.count / wall:.1f} fps, "
          f"avg {sink.bytes / max(1, sink.count) / 1024:.0f} KB/frame, process CPU {100 * cpu / wall:.0f}% of one core "
          f"(includes making the test frames)")


if __name__ == "__main__":
    main()
