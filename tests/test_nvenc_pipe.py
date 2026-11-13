"""The card-encoder pipe, run with libx264 standing in for NVENC (no graphics card here)."""
import time
import types

import pytest

av = pytest.importorskip("av")
pytest.importorskip("livekit.rtc")
if "libx264" not in av.codecs_available:
    pytest.skip("no libx264 in this PyAV", allow_module_level=True)

from app.client.core import nvenc  # noqa: E402


class FakeSource:
    def __init__(self):
        self.frames = []
        self.feedback = []

    def take_feedback(self):
        if self.feedback:
            return self.feedback.pop(0)
        return types.SimpleNamespace(keyframe_requested=False, rate_control=None)

    def capture_frame(self, frame):
        self.frames.append(frame)
        return True


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_pipe_encodes_newest_frames_as_annex_b_h264_and_obeys_keyframe_requests():
    from livekit.rtc._proto import video_frame_pb2 as proto

    source = FakeSource()
    failures = []
    pipe = nvenc.NvencPipe(source, 320, 180, 30, 1_000_000, on_failure=failures.append, codec="libx264")
    try:
        frame = bytes(range(256)) * (320 * 180 * 4 // 256)
        for _ in range(5):
            pipe.capture_bgra(frame)
            time.sleep(0.03)
        assert wait_for(lambda: len(source.frames) >= 3)
        first = source.frames[0]
        assert first.data.startswith(b"\x00\x00\x00\x01") or first.data.startswith(b"\x00\x00\x01")
        assert first.frame_type == proto.ENCODED_FRAME_KEY and (first.width, first.height) == (320, 180)
        assert all(f.frame_type == proto.ENCODED_FRAME_DELTA for f in source.frames[1:])
        stamps = [f.timestamp_us for f in source.frames]
        assert stamps == sorted(stamps)

        count = len(source.frames)
        source.feedback.append(types.SimpleNamespace(keyframe_requested=True, rate_control=None))
        pipe.capture_bgra(frame)
        assert wait_for(lambda: len(source.frames) > count)
        assert source.frames[count].frame_type == proto.ENCODED_FRAME_KEY
        assert pipe.fps_now() > 0 and not failures
    finally:
        pipe.close()


def test_real_encoded_source_accepts_the_frames():
    from livekit import rtc

    encoded = rtc.EncodedVideoSource(320, 180)
    pipe = nvenc.NvencPipe(encoded, 320, 180, 30, 1_000_000, codec="libx264")
    try:
        pipe.capture_bgra(bytes(320 * 180 * 4))
        assert wait_for(lambda: pipe.encoded >= 1)
    finally:
        pipe.close()
