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


def test_paced_stream_releases_frames_between(monkeypatch):
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

    events = [_event(i) for i in range(10)] + [_event(0, "eos")]

    class FakeQueue:
        async def wait_for(self, _):
            return events.pop(0)

    clock = iter([0.0, 0.01, 0.02, 0.04, 0.05, 0.07, 0.08, 0.10, 0.11, 0.2])
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

    assert stream.decoded == 10
    assert built == [0, 3, 6, 9]  # 30 fps: one frame per ~33 ms
    assert sorted(disposed + built) == list(range(10))  # every frame released exactly once
    assert len(put) == 4
