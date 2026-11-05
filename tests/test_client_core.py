"""New client core: audio processing, key trust, and call control without devices or network."""
import threading
import time

import numpy as np

from app.client.core.audio import AudioSettings
from app.client.core.call import CONNECTED, IDLE, CallController, CallListener
from app.client.core.dsp import FRAME_BYTES, FRAME_SAMPLES, Limiter, Mixer, VoiceGate
from app.client.core.keys import KeyManager, safety_code
from app.client.e2ee import E2EEIdentity


def tone(amplitude, frequency=300.0):
    t = np.arange(FRAME_SAMPLES) / 48000
    return (np.sin(2 * np.pi * frequency * t) * amplitude).astype(np.int16).tobytes()


def test_three_hundred_percent_volume_does_not_clip():
    mixer = Mixer()
    mixer.normalize = False
    mixer.user_volume[1] = 3.0
    peaks = []
    for _ in range(20):
        mixer.put(1, tone(20000))
        mixer.put(2, tone(20000, 500))
        out = np.frombuffer(mixer.mix(), dtype=np.int16)
        peaks.append(int(np.abs(out).max()))
    assert max(peaks) < 32767
    assert max(peaks) > 25000  # loud, not squashed


def test_quiet_friend_is_levelled_up():
    mixer = Mixer()
    levels = []
    for _ in range(300):
        mixer.put(1, tone(600))
        levels.append(float(np.abs(np.frombuffer(mixer.mix(), dtype=np.int16)).max()))
    assert levels[-1] > levels[5] * 2.5


def test_jitter_buffer_cannot_grow_latency():
    mixer = Mixer()
    for _ in range(100):
        mixer.put(1, tone(1000))
    assert len(mixer._speakers[1].frames) <= Mixer.MAX_FRAMES


def test_deafen_and_local_mute_silence_output():
    mixer = Mixer()
    mixer.put(1, tone(5000))
    mixer.put(1, tone(5000))
    mixer.muted_users.add(1)
    assert mixer.mix() == bytes(FRAME_BYTES)


def test_voice_gate_opens_for_speech_and_holds_word_endings():
    gate = VoiceGate()
    noise = np.random.default_rng(1).normal(0, 30, FRAME_SAMPLES).astype(np.float32)
    for _ in range(200):
        assert not gate.process(noise)
    speech = np.frombuffer(tone(6000), dtype=np.int16).astype(np.float32)
    assert gate.process(speech)
    for _ in range(VoiceGate.HANGOVER_FRAMES - 1):
        assert gate.process(noise)


def test_limiter_never_exceeds_full_scale():
    limiter = Limiter()
    samples = np.frombuffer(tone(32000), dtype=np.int16).astype(np.float32) * 4
    assert np.abs(limiter.process(samples)).max() <= 32767


def test_safety_code_is_the_same_on_both_sides():
    assert safety_code("AA:BB", "CC:DD") == safety_code("CC:DD", "AA:BB")
    assert len(safety_code("AA:BB", "CC:DD").replace(" ", "")) == 20


class FakeApi:
    def __init__(self):
        self.calls = []
        self.e2ee_users = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args))
            if name == "e2ee_state":
                return {"users": self.e2ee_users, "key_sets": []}
            if name == "media_token":
                return {"url": "wss://x", "token": "t"}
            return {}
        return call


def make_keys(api, settings=None, changed=None):
    settings = settings if settings is not None else {}
    return KeyManager(api, E2EEIdentity(settings), settings, my_id=1, save_settings=lambda: None,
                      on_identity_changed=lambda uid, old, new: changed.append(uid) if changed is not None else None)


def test_changed_friend_key_raises_a_warning():
    api = FakeApi()
    changed = []
    keys = make_keys(api, changed=changed)
    api.e2ee_users = [{"user_id": 2, "public_key": "A" * 43, "fingerprint": "AA:AA"}]
    keys._check_trust(api.e2ee_users)
    api.e2ee_users = [{"user_id": 2, "public_key": "B" * 43, "fingerprint": "BB:BB"}]
    keys._check_trust(api.e2ee_users)
    assert changed == [2]


def test_rotation_creates_a_new_key():
    keys = make_keys(FakeApi())
    first = keys.outgoing(5)
    keys.rotate(5)
    assert keys.outgoing(5) != first


class FakeMedia:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = self.stopped = False
        self.muted = None
        self.key_refreshes = 0
        FakeMedia.instances.append(self)

    def start(self):
        self.started = True
        self.kwargs["callbacks"].on_state("connected")

    def stop(self):
        self.stopped = True

    def set_muted(self, muted):
        self.muted = muted

    def refresh_keys(self):
        self.key_refreshes += 1

    def push_mic_frame(self, frame):
        pass


class FakeAudio:
    def __init__(self, settings, on_frame, on_problem):
        self.mixer = Mixer()
        self.running = False
        self.muted = False
        self.transmitting = False
        self.ptt_pressed = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def restart(self):
        pass

    def apply_settings(self, settings):
        pass


def make_call():
    api = FakeApi()
    states = []
    notices = []
    listener = CallListener(on_state=lambda s, c: states.append((s, c)),
                            on_notice=lambda level, text: notices.append(text))
    call = CallController(api, make_keys(api), AudioSettings(), listener,
                          media_factory=FakeMedia, audio_factory=FakeAudio)
    return call, api, states, notices


def wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_join_switch_and_leave():
    call, api, states, _ = make_call()
    call.join(10)
    assert wait_for(lambda: call.state == CONNECTED and call.channel_id == 10)
    first_media = call.media
    call.join(11)
    assert wait_for(lambda: call.channel_id == 11 and call.state == CONNECTED)
    assert first_media.stopped, "old room is closed before the new one opens"
    call.leave()
    assert wait_for(lambda: call.state == IDLE)
    assert ("disconnect", (11,)) in api.calls
    assert not call.audio.running


def test_kicked_by_moderator_leaves_without_popup():
    call, api, states, notices = make_call()
    call.join(10)
    assert wait_for(lambda: call.state == CONNECTED)
    call.handle_event({"type": "voice.kicked", "channel_id": 10, "by": "Админ"})
    assert wait_for(lambda: call.state == IDLE)
    assert any("Админ" in n for n in notices)


def test_someone_leaving_rotates_our_key():
    call, api, states, _ = make_call()
    call.join(10)
    assert wait_for(lambda: call.state == CONNECTED)
    before = call.keys.outgoing(10)
    call.handle_event({"type": "voice.left", "channel_id": 10, "user_id": 2})
    assert wait_for(lambda: call.keys.outgoing(10) != before)


def test_mute_reaches_media_and_server():
    call, api, states, _ = make_call()
    call.join(10)
    assert wait_for(lambda: call.state == CONNECTED)
    call.set_muted(True)
    assert call.media.muted is True
    assert wait_for(lambda: ("update_voice", (10, True, False, False)) in api.calls)
    call.set_deafened(True)
    assert call.audio.mixer.deafened


def test_missing_media_server_is_reported_once_instead_of_endless_reconnecting():
    call, api, states, notices = make_call()
    call.join(10)
    assert wait_for(lambda: call.state == CONNECTED)
    call._on_media_state("unavailable")
    call._on_media_state("unavailable")
    assert call.state == "unavailable"
    assert sum("LiveKit" in n for n in notices) == 1
