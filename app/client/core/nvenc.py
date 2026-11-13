"""Screen share encoded by the graphics card (NVIDIA NVENC through PyAV's ffmpeg).

The Windows build of LiveKit's native library has no hardware encoder: the processor encoded every frame
with OpenH264 and, next to a game, fell from 60 to 8 fps at 1440p (2026-11-13). Here the card's own video
encoder block does that work and LiveKit only sends the finished H.264 (an EncodedVideoSource), so the game
and the voice keep the processor.

`NvencPipe` stands in for a `rtc.VideoSource`: the screen capturer hands it BGRA frames through the same
`capture_frame(rtc.VideoFrame)` call. A thread of its own encodes the newest frame (older ones waiting are
dropped, never queued) and follows WebRTC's requests: a key frame for a new viewer, a lower bitrate when the
network cannot carry more. If anything fails it calls `on_failure` and the share switches to the old way.
"""
from __future__ import annotations

import logging
import threading
import time
from fractions import Fraction
from typing import Callable

logger = logging.getLogger("voicechat.nvenc")

CODEC = "h264_nvenc"
_probe_result: bool | None = None
_probe_lock = threading.Lock()


def _open(width: int, height: int, fps: int, bitrate: int, codec: str = CODEC):
    import av

    ctx = av.CodecContext.create(codec, "w")
    ctx.width, ctx.height = width, height
    ctx.pix_fmt = "bgr0" if codec == CODEC else "yuv420p"
    ctx.time_base = Fraction(1, 1_000_000)
    ctx.framerate = Fraction(fps, 1)
    ctx.bit_rate = bitrate
    ctx.gop_size = fps * 4  # a key frame every 4 s anyway; viewers that need one sooner ask for it
    ctx.max_b_frames = 0
    if codec == CODEC:
        # Low latency, constant bitrate, no frames held back. The card converts BGRA to YUV itself.
        ctx.options = {"preset": "p4", "tune": "ll", "rc": "cbr", "zerolatency": "1", "delay": "0",
                       "forced-idr": "1", "bf": "0", "profile": "high"}
    else:  # tests on machines without a card
        ctx.options = {"preset": "ultrafast", "tune": "zerolatency"}
    ctx.open()
    return ctx


def available() -> bool:
    """Can this computer encode with NVENC? Tried once per run on a tiny picture (about 0.2 s)."""
    global _probe_result
    with _probe_lock:
        if _probe_result is None:
            try:
                import av

                ctx = _open(256, 144, 30, 500_000)
                frame = av.VideoFrame(256, 144, "bgr0")
                frame.pts = 0
                packets = list(ctx.encode(frame)) + list(ctx.encode(None))
                _probe_result = any(bytes(p) for p in packets)
            except Exception as exc:
                logger.info("NVENC not available: %s", exc)
                _probe_result = False
            logger.info("NVENC screen encoder: %s", "available" if _probe_result else "not available")
        return _probe_result


class NvencPipe:
    """Takes BGRA `rtc.VideoFrame`s like a VideoSource, encodes them on the card, feeds `encoded_source`."""

    REOPEN_EVERY = 5.0  # seconds between bitrate changes (each one restarts the encoder with a key frame)

    def __init__(self, encoded_source, width: int, height: int, fps: int, max_bitrate: int,
                 on_failure: Callable[[str], None] = lambda reason: None, codec: str = CODEC) -> None:
        self.source = encoded_source
        self.width, self.height, self.fps = width, height, fps
        self.max_bitrate = max_bitrate
        self.bitrate = max_bitrate
        self.on_failure = on_failure
        self.codec = codec
        self.encoded = 0
        self._times: list[float] = []
        self._slot: tuple[bytes, int] | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="nvenc", daemon=True)
        self._thread.start()

    # --- the capturer's side (any thread) ------------------------------------------------------

    def capture_bgra(self, data: bytes) -> None:
        """One captured screen frame, BGRA, width*height*4 bytes. Replaces a frame still waiting."""
        with self._lock:
            self._slot = (data, int(time.monotonic() * 1_000_000))
        self._wake.set()

    def capture_frame(self, frame, timestamp_us: int = 0, rotation=None) -> None:
        """VideoSource-compatible entry for a BGRA rtc.VideoFrame."""
        data = frame.data
        self.capture_bgra(data if isinstance(data, bytes) else bytes(data))

    def fps_now(self) -> float:
        now = time.perf_counter()
        with self._lock:
            while self._times and now - self._times[0] > 1.0:
                self._times.pop(0)
            return float(len(self._times))

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(2)

    # --- the encoder thread --------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._loop()
        except Exception as exc:
            logger.warning("NVENC screen encoding stopped: %s", exc, exc_info=True)
            self.on_failure(str(exc))

    def _loop(self) -> None:
        import av
        from livekit import rtc
        from livekit.rtc._proto import video_frame_pb2 as proto

        ctx = _open(self.width, self.height, self.fps, self.bitrate, self.codec)
        logger.info("NVENC screen encoder: %dx%d at %d fps, %d kbps", self.width, self.height, self.fps,
                    self.bitrate // 1000)
        frame_bytes = self.width * self.height * 4
        want_key, reopened, last_pts = True, time.monotonic(), -1
        try:
            while not self._stop.is_set():
                if not self._wake.wait(0.5):
                    continue
                self._wake.clear()
                with self._lock:
                    slot, self._slot = self._slot, None
                if slot is None or len(slot[0]) < frame_bytes:
                    continue
                data, pts = slot
                if pts <= last_pts:
                    pts = last_pts + 1
                last_pts = pts

                feedback = self.source.take_feedback()
                want_key = want_key or feedback.keyframe_requested
                rate = feedback.rate_control
                if rate is not None and rate.target_bitrate_bps > 0:
                    target = max(300_000, min(self.max_bitrate, int(rate.target_bitrate_bps)))
                    if (abs(target - self.bitrate) > 0.3 * self.bitrate
                            and time.monotonic() - reopened > self.REOPEN_EVERY):
                        self.bitrate, reopened = target, time.monotonic()
                        ctx = _open(self.width, self.height, self.fps, self.bitrate, self.codec)
                        want_key = True
                        logger.info("NVENC screen encoder: bitrate %d kbps", self.bitrate // 1000)

                if self.codec == CODEC:
                    picture = av.VideoFrame(self.width, self.height, "bgr0")
                    plane = picture.planes[0]
                    if plane.line_size == self.width * 4:
                        plane.update(data[:frame_bytes])
                    else:
                        picture = self._from_array(av, data)
                else:
                    picture = self._from_array(av, data).reformat(format="yuv420p")
                picture.pts = pts
                picture.time_base = ctx.time_base
                if want_key:
                    picture.pict_type = av.video.frame.PictureType.I
                    want_key = False
                for packet in ctx.encode(picture):
                    payload = bytes(packet)
                    if not payload:
                        continue
                    self.source.capture_frame(rtc.EncodedVideoFrame(
                        data=payload, width=self.width, height=self.height, codec=rtc.VideoCodec.H264,
                        frame_type=proto.ENCODED_FRAME_KEY if packet.is_keyframe else proto.ENCODED_FRAME_DELTA,
                        timestamp_us=int(packet.pts if packet.pts is not None else pts)))
                    self.encoded += 1
                    now = time.perf_counter()
                    with self._lock:
                        self._times.append(now)
        finally:
            try:
                list(ctx.encode(None))
            except Exception:
                pass

    def _from_array(self, av, data: bytes):
        import numpy as np

        array = np.frombuffer(data, dtype=np.uint8, count=self.width * self.height * 4)
        return av.VideoFrame.from_ndarray(array.reshape(self.height, self.width, 4), format="bgra")
