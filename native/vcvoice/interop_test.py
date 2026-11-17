"""Interop test: two vcvoice engines (7-audio sends a tone, 8-audio silent) and an old-style Python client
"u9" (livekit 1.2.1, sends a tone, subscribes mics) in one E2EE room on a local dev LiveKit server."""
import asyncio, base64, json, os, subprocess, sys, threading, time

import numpy as np
from livekit import api, rtc

URL = "ws://127.0.0.1:7880"
ROOM = "channel-1"
BIN = sys.argv[1]
KEYS = {7: ("0a01aaaa", os.urandom(32)), 8: ("0b02bbbb", os.urandom(32)), 9: ("0c03cccc", os.urandom(32))}


def token(identity):
    return (api.AccessToken("devkey", "secret").with_identity(identity)
            .with_grants(api.VideoGrants(room_join=True, room=ROOM)).to_jwt())


def key_index(key_id):
    return int(key_id[:4], 16) % 16


class Engine:
    def __init__(self, uid, kind):
        env = dict(os.environ, VCVOICE_FAKE_AUDIO=kind)
        self.uid = uid
        self.p = subprocess.Popen([BIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env, text=True, bufsize=1)
        self.events = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._err, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.events.append(json.loads(line))

    def _err(self):
        for line in self.p.stderr:
            print(f"[{self.uid}] {line.rstrip()}")

    def send(self, **msg):
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def wait(self, pred, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            for e in list(self.events):
                if pred(e):
                    return e
            time.sleep(0.05)
        raise TimeoutError(f"engine {self.uid}: {self.events[-5:]}")


async def old_client(stop, heard):
    room = rtc.Room()
    opts = rtc.RoomOptions(auto_subscribe=False, encryption=rtc.E2EEOptions(
        key_provider_options=rtc.KeyProviderOptions(ratchet_window_size=0)))

    def apply_keys():
        kp = room.e2ee_manager.key_provider
        for p in room.remote_participants.values():
            uid = int(p.identity.removesuffix("-audio").lstrip("u"))
            kid, key = KEYS[uid]
            kp.set_key(p.identity, key, key_index(kid))

    @room.on("participant_connected")
    def _p(p):
        apply_keys()

    @room.on("track_published")
    def _pub(pub, p):
        if pub.kind == rtc.TrackKind.KIND_AUDIO:
            pub.set_subscribed(True)

    @room.on("track_subscribed")
    def _sub(track, pub, p):
        async def read():
            async for ev in rtc.AudioStream(track, sample_rate=48000, num_channels=1):
                a = np.frombuffer(ev.frame.data, dtype=np.int16)
                heard[p.identity] = max(heard.get(p.identity, 0), int(np.abs(a.astype(np.int32)).max()))
        asyncio.ensure_future(read())

    await room.connect(URL, token("u9"), options=opts)
    kid, key = KEYS[9]
    room.e2ee_manager.key_provider.set_key("u9", key, key_index(kid))
    apply_keys()
    for p in room.remote_participants.values():
        for pub in p.track_publications.values():
            if pub.kind == rtc.TrackKind.KIND_AUDIO:
                pub.set_subscribed(True)
    src = rtc.AudioSource(48000, 1)
    track = rtc.LocalAudioTrack.create_audio_track("microphone", src)
    await room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    for c in room.e2ee_manager.frame_cryptors():
        if c.participant_identity == "u9":
            c.set_key_index(key_index(kid))
    phase = 0
    while not stop.is_set():
        t = (np.arange(480) + phase) / 48000
        phase += 480
        frame = (6000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
        await src.capture_frame(rtc.AudioFrame(frame.tobytes(), 48000, 1, 480))
    await room.disconnect()


def main():
    a, b = Engine(7, "tone"), Engine(8, "silence")
    for e in (a, b):
        e.wait(lambda ev: ev.get("ev") == "ready")
        e.send(cmd="settings", settings={"noise": "standard", "echo_cancellation": True})
        e.send(cmd="start")
        kid, key = KEYS[e.uid]
        e.send(cmd="local_key", id=kid, key=base64.b64encode(key).decode())
        for uid, (kid2, key2) in KEYS.items():
            if uid != e.uid:
                e.send(cmd="remote_key", user_id=uid, id=kid2, key=base64.b64encode(key2).decode())
        e.send(cmd="connect", url=URL, token=token(f"{e.uid}-audio"), my_id=e.uid)
    for e in (a, b):
        e.wait(lambda ev: ev.get("ev") == "state" and ev.get("state") == "connected")
    stop = threading.Event()
    heard = {}
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=lambda: loop.run_until_complete(old_client(stop, heard)), daemon=True)
    t.start()
    time.sleep(3)
    for e in (a, b):
        e.send(cmd="stats", id=1)
    time.sleep(6)
    for e in (a, b):
        e.send(cmd="stats", id=2)
    sa = a.wait(lambda ev: ev.get("ev") == "stats" and ev.get("id") == 2)
    sb = b.wait(lambda ev: ev.get("ev") == "stats" and ev.get("id") == 2)
    print("A stats:", json.dumps(sa))
    print("B stats:", json.dumps(sb))
    print("old client heard peaks:", heard)
    print("speakers events B:", [ev for ev in b.events if ev.get("ev") == "speakers"][-3:])
    stop.set()
    t.join(5)
    for e in (a, b):
        e.send(cmd="quit")
    ok = (sb["heard"].get("7", 0) > 400 and sb["heard"].get("9", 0) > 400 and sb["played_peak_db"] > -30
          and heard.get("7-audio", 0) > 1000 and sa["heard"].get("9", 0) > 400)
    print("RESULT", "OK" if ok else "FAIL")


main()
