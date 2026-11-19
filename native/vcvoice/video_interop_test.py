"""Video interop test: the engine's video process ("vcvoice --video", identity u7) and an old-style Python
client "u9" (livekit 1.2.1, the 0.5/0.6 video path) in one E2EE room on a local dev LiveKit server.

- u9 publishes a screen share (a moving gray level) and a camera; the engine watches both and must hand
  the pictures out through shared memory with the right brightness (proves decryption and decoding).
- The engine shares a screen (an X display; run under Xvfb); u9 must receive and decrypt it.

Run: livekit-server --dev; Xvfb :99 & DISPLAY=:99 python video_interop_test.py target/release/vcvoice
"""
import asyncio, base64, json, mmap, os, subprocess, sys, threading, time

import numpy as np
from livekit import api, rtc

URL = "ws://127.0.0.1:7880"
ROOM = "channel-1"
BIN = sys.argv[1]
KEYS = {7: ("0a01aaaa", os.urandom(32)), 9: ("0c03cccc", os.urandom(32))}
CAMERA = 1_000_000_000


def token(identity):
    return (api.AccessToken("devkey", "secret").with_identity(identity)
            .with_grants(api.VideoGrants(room_join=True, room=ROOM)).to_jwt())


def key_index(key_id):
    return int(key_id[:4], 16) % 16


class VideoEngine:
    def __init__(self):
        self.p = subprocess.Popen([BIN, "--video"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, bufsize=1)
        self.events = []
        self.frames = {}
        self.luma = {}
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._err, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            ev = json.loads(line)
            if ev.get("ev") == "vf":
                key = ev["key"]
                self.frames[key] = self.frames.get(key, 0) + 1
                with open(f"/dev/shm/{ev['name']}", "rb") as f:
                    m = mmap.mmap(f.fileno(), ev["size"] * 3, access=mmap.ACCESS_READ)
                    start = ev["slot"] * ev["size"]
                    y = np.frombuffer(m[start:start + ev["w"] * ev["h"]], dtype=np.uint8)
                    self.luma[key] = (ev["w"], ev["h"], int(y.mean()))
                    m.close()
            else:
                self.events.append(ev)

    def _err(self):
        for line in self.p.stderr:
            print(f"[engine] {line.rstrip()}")

    def send(self, **msg):
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def wait(self, pred, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            for e in list(self.events):
                if pred(e):
                    return e
            time.sleep(0.05)
        raise TimeoutError(f"engine: {self.events[-5:]}")


async def old_client(stop, got):
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
        if pub.kind == rtc.TrackKind.KIND_VIDEO:
            pub.set_subscribed(True)

    @room.on("track_subscribed")
    def _sub(track, pub, p):
        async def read():
            async for ev in rtc.VideoStream(track, format=rtc.VideoBufferType.I420):
                f = ev.frame
                y = np.frombuffer(f.data, dtype=np.uint8)[:f.width * f.height]
                got["frames"] = got.get("frames", 0) + 1
                got["size"] = (f.width, f.height)
                got["luma"] = int(y.mean())
        asyncio.ensure_future(read())

    await room.connect(URL, token("u9"), options=opts)
    kid, key = KEYS[9]
    room.e2ee_manager.key_provider.set_key("u9", key, key_index(kid))
    apply_keys()
    screen = rtc.VideoSource(1280, 720, is_screencast=True)
    camera = rtc.VideoSource(640, 360)
    await room.local_participant.publish_track(
        rtc.LocalVideoTrack.create_video_track("screen", screen),
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_SCREENSHARE, video_codec=rtc.VideoCodec.H264,
                               simulcast=False))  # as the app publishes a share
    await room.local_participant.publish_track(
        rtc.LocalVideoTrack.create_video_track("camera", camera),
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
    for c in room.e2ee_manager.frame_cryptors():
        if c.participant_identity == "u9":
            c.set_key_index(key_index(kid))
    for p in room.remote_participants.values():
        for pub in p.track_publications.values():
            if pub.kind == rtc.TrackKind.KIND_VIDEO:
                pub.set_subscribed(True)
    while not stop.is_set():
        # Gray 200 on the screen, gray 60 on the camera (I420: Y then U/V at 128).
        for src, (w, h, level) in ((screen, (1280, 720, 200)), (camera, (640, 360, 60))):
            buf = np.full(w * h * 3 // 2, 128, dtype=np.uint8)
            buf[:w * h] = level
            src.capture_frame(rtc.VideoFrame(w, h, rtc.VideoBufferType.I420, buf.tobytes()))
        await asyncio.sleep(1 / 30)
    await room.disconnect()


def main():
    e = VideoEngine()
    e.wait(lambda ev: ev.get("ev") == "ready")
    kid, key = KEYS[7]
    e.send(cmd="local_key", id=kid, key=base64.b64encode(key).decode())
    kid9, key9 = KEYS[9]
    e.send(cmd="remote_key", user_id=9, id=kid9, key=base64.b64encode(key9).decode())
    stop = threading.Event()
    got = {}
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=lambda: loop.run_until_complete(old_client(stop, got)), daemon=True)
    t.start()
    time.sleep(2)
    e.send(cmd="vconnect", url=URL, token=token("u7"), my_id=7)
    e.wait(lambda ev: ev.get("ev") == "vstate" and ev.get("state") == "connected")
    e.send(cmd="watch", user_id=9, on=True)
    e.send(cmd="share", kind="monitor", ident="1", rect=[0, 0, 1280, 720], width=1280, height=720, fps=30,
           bitrate=3_000_000, encoder="")
    time.sleep(10)
    stats = [ev for ev in e.events if ev.get("ev") == "vstats"]
    tracks = [ev for ev in e.events if ev.get("ev") == "vtracks"]
    problems = [ev for ev in e.events if ev.get("ev") in ("vproblem", "key_problem")]
    print("engine pictures:", e.frames)
    print("engine luma:", e.luma)
    print("engine tracks:", tracks[-1] if tracks else None)
    print("engine share stats:", stats[-1] if stats else None)
    print("problems:", problems)
    print("old client got:", got)
    e.send(cmd="share_stop")
    e.send(cmd="watch", user_id=9, on=False)
    time.sleep(2)
    gone = [ev for ev in e.events if ev.get("ev") == "vgone"]
    print("gone:", gone)
    e.send(cmd="quit")
    e.p.wait(10)
    stop.set()
    t.join(5)
    ok = (e.frames.get(9, 0) > 100 and e.frames.get(CAMERA + 9, 0) > 100
          and abs(e.luma[9][2] - 200) < 8 and abs(e.luma[CAMERA + 9][2] - 60) < 8
          and e.luma[9][:2] == (1280, 720) and got.get("frames", 0) > 100 and got.get("size") == (1280, 720)
          and e.frames.get(7, 0) > 50 and tracks and 7 in tracks[-1]["screen"] and 9 in tracks[-1]["camera"]
          and not problems and {g["key"] for g in gone} >= {7, 9})
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


main()
