"""Video in the native engine ("vcvoice --video") driven through NativeVoice and NativeMediaSession, and
the window's video hub reading the engine's shared memory.

The live part needs the built engine, `livekit-server --dev` (VOICECHAT_TEST_LIVEKIT_URL) and, for our own
share, an X display (Xvfb). The other side is the old Python video path (MediaSession + ScreenCapturer), so
this also proves old and new apps see each other's screens.
"""
import multiprocessing as mp
import os
import time
from pathlib import Path

import numpy as np
import pytest

from app.client.core.audio import AudioSettings

ENGINE = Path(__file__).resolve().parents[1] / "native" / "vcvoice" / "target" / "release" / "vcvoice"
LIVEKIT_URL = os.getenv("VOICECHAT_TEST_LIVEKIT_URL")


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.instance() or QGuiApplication([])


def wait(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_video_hub_reads_engine_shared_memory(qapp):
    from app.client.core.native_media import ShmFrame
    from app.client.ui.screen import VideoHub

    width, height = 64, 36
    size = width * height * 3 // 2
    name = f"vcvideo-test-{os.getpid()}"
    path = Path("/dev/shm") / name if Path("/dev/shm").is_dir() else Path("/tmp") / name
    block = np.zeros(size * 3, dtype=np.uint8)
    block[size:size + width * height] = 123  # slot 1: luma 123
    block[size + width * height:2 * size] = 128
    path.write_bytes(block.tobytes())
    try:
        hub = VideoHub()
        hub.push(9, ShmFrame(name, width, height, 1, size))
        frame = hub.latest(9)
        assert frame is not None and (frame.width(), frame.height()) == (width, height)
        image = frame.toImage()
        assert abs(image.pixelColor(10, 10).red() - 123) < 20  # gray: Y 123 with neutral colour
        assert hub.received(9)["width"] == width
        hub.push(9, None)
        assert hub.latest(9) is None
    finally:
        path.unlink(missing_ok=True)


def test_native_share_command(monkeypatch):
    from app.client.ui.screen import PRESETS_BY_KEY, NativeScreenShare

    monkeypatch.setenv("VOICECHAT_TEST_CAPTURE", "1")
    share = NativeScreenShare(2, PRESETS_BY_KEY["1080p60"])
    command = share.command()
    assert command["kind"] == "monitor" and command["ident"] == "2"
    assert command["rect"] == [1920, 0, 2560, 1440]
    assert (command["width"], command["height"], command["fps"]) == (1920, 1080, 60)
    share.engine_stats = {"encoded_fps": 59.0, "encoder": "NVIDIA H.264 Encoder MFT (видеокарта)"}
    assert share.stats()["encoded_fps"] == 59.0 and share.stats()["source_width"] == 2560


def test_video_switch(monkeypatch):
    from app.client.core.call import native_video_enabled

    class Engine:
        native = True

        def video_command(self, cmd, **args):
            pass

    assert native_video_enabled(Engine())
    monkeypatch.setenv("VOICECHAT_NATIVE_VIDEO", "0")
    assert not native_video_enabled(Engine())
    assert not native_video_enabled(object())


def _old_app_peer(room, keys, seconds, results):
    """An app on the old Python video path: shares a (test) screen and watches user 7."""
    os.environ["VOICECHAT_TEST_CAPTURE"] = "1"
    from app.client.core.dsp import Mixer
    from app.client.core.media import MediaCallbacks, MediaSession
    from app.client.ui.screen import PRESETS_BY_KEY, ScreenCapturer
    from livekit import api

    def credentials():
        jwt = (api.AccessToken("devkey", "secret").with_identity("u9")
               .with_grants(api.VideoGrants(room_join=True, room=room)).to_jwt())
        return {"url": LIVEKIT_URL, "token": jwt}

    got = {"frames": 0, "size": None}
    streams = []

    def on_frame(uid, frame):
        if uid == 7:
            got["frames"] += 1
            got["size"] = (frame.width, frame.height)

    states = []
    session = MediaSession(credentials=credentials, my_id=9, outgoing_key=lambda: tuple(keys[9]),
                           incoming_key=lambda uid: tuple(keys[uid]) if uid in keys else None, mixer=Mixer(),
                           callbacks=MediaCallbacks(on_state=states.append, on_video_frame=on_frame,
                                                    on_streams=streams.append),
                           voice=False)
    session.start()
    deadline = time.time() + 15
    while time.time() < deadline and "connected" not in states:
        time.sleep(0.1)
    session.start_screen(ScreenCapturer(1, PRESETS_BY_KEY["720p30"]))
    session.watch(7, True)
    time.sleep(seconds)
    session.stop()
    results.put((got, [sorted(s) for s in streams]))


@pytest.mark.skipif(not ENGINE.is_file() or not LIVEKIT_URL, reason="engine not built or no local LiveKit")
def test_new_and_old_apps_see_each_other(qapp, monkeypatch):
    from livekit import api

    from app.client.core.media import MediaCallbacks
    from app.client.core.native_media import NativeMediaSession
    from app.client.core.native_voice import NativeVoice
    from app.client.ui.screen import PRESETS_BY_KEY, NativeScreenShare, VideoHub

    monkeypatch.setenv("VCVOICE_FAKE_AUDIO", "silence")
    room = f"channel-video-{time.time_ns()}"
    keys = {7: ("aaaa0007", os.urandom(32)), 9: ("cccc0009", os.urandom(32))}
    ctx = mp.get_context("spawn")
    results = ctx.Queue()
    peer = ctx.Process(target=_old_app_peer, args=(room, keys, 14.0, results))
    peer.start()

    def credentials():
        jwt = (api.AccessToken("devkey", "secret").with_identity("u7")
               .with_grants(api.VideoGrants(room_join=True, room=room)).to_jwt())
        return {"url": LIVEKIT_URL, "token": jwt}

    engine = NativeVoice(AudioSettings(), path=ENGINE)
    hub = VideoHub()
    states, streams, problems = [], [], []
    try:
        engine.set_local_key(*keys[7])
        engine.set_remote_key(9, *keys[9])
        session = NativeMediaSession(
            credentials=credentials, my_id=7, outgoing_key=lambda: tuple(keys[7]),
            incoming_key=lambda uid: tuple(keys[uid]) if uid in keys else None, mixer=None, engine=engine,
            callbacks=MediaCallbacks(on_state=states.append, on_streams=streams.append, on_video_frame=hub.push,
                                     on_video_problem=lambda kind, text: problems.append((kind, text))))
        session.start()
        assert wait(lambda: session.connected, 15), states
        session.watch(9, True)
        share = None
        if os.environ.get("DISPLAY"):
            monkeypatch.setenv("VOICECHAT_TEST_CAPTURE", "1")
            share = NativeScreenShare(1, PRESETS_BY_KEY["720p30"])
            share._rect = (0, 0, 1280, 720)
            session.start_screen(share)
        assert wait(lambda: (hub.received(9) or {}).get("fps", 0) > 5, 20), "no picture from the old app"
        assert (hub.received(9)["width"], hub.received(9)["height"]) == (1280, 720)
        assert any(9 in s for s in streams)
        if share is not None:
            assert wait(lambda: share.stats().get("encoded_fps", 0) > 5, 15), share.stats()
            assert hub.latest(7) is not None, "no preview of our own share"
        got, peer_streams = results.get(timeout=40)
        if share is not None:
            assert got["frames"] > 20 and got["size"] == (1280, 720), got
            assert any(7 in s for s in peer_streams), peer_streams
        assert not problems
        session.stop()
        assert wait(lambda: engine._video_proc is None)
    finally:
        engine.close()
        peer.join(10)
