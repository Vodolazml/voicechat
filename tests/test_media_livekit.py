"""MediaSession against a real LiveKit server.

Run `livekit-server --dev` and set VOICECHAT_TEST_LIVEKIT_URL=ws://127.0.0.1:7880. Each peer runs in
its own process, like real clients (the LiveKit SDK supports one connecting room per process).
"""
import multiprocessing as mp
import os
import time

import pytest

LIVEKIT_URL = os.getenv("VOICECHAT_TEST_LIVEKIT_URL")
pytestmark = pytest.mark.skipif(not LIVEKIT_URL, reason="local LiveKit server not configured")


def _peer(user_id, room, keys, speak_after, listen_seconds, results):
    import numpy as np

    from app.client.core.dsp import FRAME_SAMPLES, SAMPLE_RATE, Mixer
    from app.client.core.media import MediaCallbacks, MediaSession
    from livekit import api

    def credentials():
        jwt = (api.AccessToken("devkey", "secret").with_identity(f"u{user_id}")
               .with_grants(api.VideoGrants(room_join=True, room=room)).to_jwt())
        return {"url": LIVEKIT_URL, "token": jwt}

    mixer = Mixer()
    heard: dict[int, int] = {}
    original_put = mixer.put

    def put(uid, frame):
        if np.abs(np.frombuffer(frame, dtype=np.int16)).max() > 1000:
            heard[uid] = heard.get(uid, 0) + 1
        original_put(uid, frame)

    mixer.put = put
    states = []
    speakers_seen = set()
    session = MediaSession(
        credentials=credentials, my_id=user_id,
        outgoing_key=lambda: tuple(keys[user_id]),
        incoming_key=lambda uid: tuple(keys[uid]) if uid in keys else None,
        mixer=mixer, callbacks=MediaCallbacks(on_state=states.append, on_speakers=speakers_seen.update))
    session.start()
    deadline = time.time() + 15
    while time.time() < deadline and (not states or states[-1] != "connected"):
        time.sleep(0.1)
    time.sleep(max(0.0, speak_after))
    if speak_after >= 0:
        t = np.arange(FRAME_SAMPLES) / SAMPLE_RATE
        tone = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16).tobytes()
        for _ in range(200):
            session.push_mic_frame(tone)
            time.sleep(0.01)
    time.sleep(listen_seconds)
    session.stop()
    results.put((user_id, states, heard, sorted(speakers_seen)))


def run_peers(*specs):
    ctx = mp.get_context("spawn")
    results = ctx.Queue()
    procs = [ctx.Process(target=_peer, args=(*spec, results)) for spec in specs]
    for proc in procs:
        proc.start()
    out = {}
    for _ in procs:
        user_id, states, heard, speakers = results.get(timeout=60)
        out[user_id] = {"states": states, "heard": heard, "speakers": speakers}
    for proc in procs:
        proc.join(10)
    return out


def test_encrypted_voice_flows_between_two_clients():
    room = f"channel-test-{time.time_ns()}"
    keys = {1: ("aaaa0001", os.urandom(32)), 2: ("bbbb0002", os.urandom(32))}
    out = run_peers((1, room, keys, 2.0, 3.0), (2, room, keys, -1, 6.0))
    assert "connected" in out[1]["states"] and "connected" in out[2]["states"]
    assert out[2]["heard"].get(1, 0) > 50, out
    assert 1 in out[2]["speakers"], out


def test_wrong_key_means_silence():
    room = f"channel-test-{time.time_ns()}"
    alice_key = ("aaaa0001", os.urandom(32))
    out = run_peers((1, room, {1: alice_key}, 2.0, 3.0),
                    (3, room, {1: ("aaaa0001", os.urandom(32)), 3: ("cccc0003", os.urandom(32))}, -1, 6.0))
    assert out[3]["heard"].get(1, 0) == 0, out
