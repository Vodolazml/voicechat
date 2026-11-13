"""Incoming video is paced: frames beyond the display rate are released without being copied."""
import asyncio
import types

import pytest

pytest.importorskip("livekit.rtc")

from app.client.core import media  # noqa: E402


def _event(i, kind="frame"):
    ve = types.SimpleNamespace()
    if kind == "frame":
        ve.frame_received = types.SimpleNamespace(buffer=types.SimpleNamespace(handle=types.SimpleNamespace(id=i)),
                                                  timestamp_us=i, rotation=0)
    ve.HasField = lambda name, kind=kind: (name == "frame_received") == (kind == "frame")
    return types.SimpleNamespace(video_stream_event=ve)


def _run_paced(monkeypatch, times):
    import livekit.rtc._ffi_client as ffi
    import livekit.rtc.video_frame as vf

    disposed, built = [], []

    class FakeHandle:
        def __init__(self, handle):
            self.handle = handle

        def dispose(self):
            disposed.append(self.handle)

    monkeypatch.setattr(ffi, "FfiHandle", FakeHandle)
    monkeypatch.setattr(vf.VideoFrame, "_from_owned_info", staticmethod(lambda info: built.append(info.handle.id) or info))
    cls = media._paced_stream_class()

    events = [_event(i) for i in range(len(times))] + [_event(0, "eos")]

    class FakeQueue:
        async def wait_for(self, _):
            return events.pop(0)

    clock = iter(times)
    monkeypatch.setattr(media.time, "perf_counter", lambda: next(clock))
    monkeypatch.setattr(ffi.FfiClient, "instance", types.SimpleNamespace(queue=types.SimpleNamespace(unsubscribe=lambda q: None)), raising=False)

    stream = cls.__new__(cls)
    stream.min_interval = 1 / 30
    stream.decoded = stream.shown = 0
    stream._ffi_queue = FakeQueue()
    put = []
    stream._queue = types.SimpleNamespace(put=put.append)
    stream._is_event = lambda e: True
    asyncio.run(stream._run())
    return stream, built, disposed, put


def test_paced_stream_releases_frames_between(monkeypatch):
    stream, built, disposed, put = _run_paced(monkeypatch, [0.0, 0.01, 0.02, 0.04, 0.05, 0.07, 0.08, 0.105, 0.11, 0.2])
    assert stream.decoded == 10
    assert built == [0, 3, 5, 7, 9]  # 30 fps: one frame per ~33 ms
    assert sorted(disposed + built) == list(range(10))  # every frame released exactly once
    assert len(put) == 5


def test_paced_stream_keeps_30fps_when_35_arrive(monkeypatch):
    times = [i / 35 for i in range(350)]  # 10 s at 35 fps
    stream, built, disposed, put = _run_paced(monkeypatch, times)
    assert 290 <= len(built) <= 302


def test_share_frame_rate_steps_down_and_back_up():
    cap = types.SimpleNamespace(fps=60, frame_limit=0)
    t = 0.0
    for _ in range(10):  # encoder makes 20 of 60 fps for 10 s
        media._adapt_frame_rate(cap, 20.0, t)
        t += 1
    assert cap.frame_limit == 45
    for _ in range(10):
        media._adapt_frame_rate(cap, 20.0, t)
        t += 1
    assert cap.frame_limit == 30
    for _ in range(50):  # keeps up with 30
        media._adapt_frame_rate(cap, 30.0, t)
        t += 1
    assert cap.frame_limit == 45
    for _ in range(50):
        media._adapt_frame_rate(cap, 44.0, t)
        t += 1
    assert cap.frame_limit == 0  # back to all 60
