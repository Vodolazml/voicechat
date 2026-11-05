"""Screen capture in its own process: frames arrive through shared memory, at the wanted size."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("PySide6")

from app.client.ui import screen  # noqa: E402
from app.client.ui.screen import PRESETS_BY_KEY, ScreenCapturer  # noqa: E402


class FakeSource:
    def __init__(self) -> None:
        self.frames = []

    def capture_frame(self, frame) -> None:
        self.frames.append((frame.width, frame.height, len(bytes(frame.data))))


def _capturer(monkeypatch) -> ScreenCapturer:
    monkeypatch.setenv("VOICECHAT_TEST_CAPTURE", "1")
    monkeypatch.setattr(screen, "list_monitors", lambda: [
        {"index": 1, "title": "test", "width": 2560, "height": 1440, "left": 0, "top": 0}])
    return ScreenCapturer(1, PRESETS_BY_KEY["1080p30"], preview=lambda image: previews.append(image.size()))


previews: list = []


async def _run_for(capturer: ScreenCapturer, source: FakeSource, seconds: float) -> None:
    task = asyncio.create_task(capturer.run(source))
    await asyncio.sleep(seconds)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_capture_runs_in_a_child_process_and_scales(monkeypatch):
    pytest.importorskip("livekit.rtc")
    capturer = _capturer(monkeypatch)
    source = FakeSource()
    asyncio.run(_run_for(capturer, source, 6.0))
    assert capturer.backend == "test"
    assert len(source.frames) >= 10
    assert all(frame == (1920, 1080, 1920 * 1080 * 4) for frame in source.frames)
    assert previews, "the local preview gets frames too"
    assert capturer.stats()["source_width"] == 2560


def test_falls_back_to_a_thread_when_no_process(monkeypatch):
    pytest.importorskip("livekit.rtc")
    capturer = _capturer(monkeypatch)

    async def no_process(source):
        raise screen._NoProcess("blocked")
    monkeypatch.setattr(capturer, "_run_process", no_process)
    source = FakeSource()
    asyncio.run(_run_for(capturer, source, 1.5))
    assert len(source.frames) >= 5 and source.frames[0][:2] == (1920, 1080)

