"""UI sound player: its own stream, joins/leaves announced once, button prompts cut each other short."""
import numpy as np

from app.client.core.dsp import FRAME_BYTES, FRAME_SAMPLES
from app.client.core.effects import SoundPlayer


class FakeStream:
    def __init__(self, device, fill, finished):
        self.device, self.fill, self.finished = device, fill, finished
        self.closed = False

    def abort(self, ignore_errors=True):
        pass

    def close(self, ignore_errors=True):
        self.closed = True


def make_player(device="Headset", volume=1.0, sink=None):
    opened = []
    settings = {"device": device, "volume": volume}

    def factory(name, fill, finished):
        stream = FakeStream(name, fill, finished)
        opened.append(stream)
        return stream

    player = SoundPlayer(lambda: settings["device"], lambda: settings["volume"], sink, stream_factory=factory)
    return player, opened, settings


def sound(frames, value=8000):
    return np.full(FRAME_SAMPLES * frames, value, dtype=np.int16).tobytes()


def near(samples, value):
    return np.all(np.abs(samples.astype(np.int32) - value) <= 1)


def pull(stream, frames):
    return np.frombuffer(stream.fill(FRAME_SAMPLES * frames), dtype=np.int16)


def test_friends_joining_together_hear_one_announcement():
    player, opened, _ = make_player()
    join = sound(3)
    assert player.play(join, lane="announce", key="join")
    assert not player.play(join, lane="announce", key="join")   # second friend, same moment
    pull(opened[0], 2)
    assert not player.play(join, lane="announce", key="join")   # a third one while it still plays
    out = pull(opened[0], 3)
    assert np.count_nonzero(out) == FRAME_SAMPLES                # the last frame of the one join, then quiet
    assert not player.is_playing("announce")
    assert player.play(join, lane="announce", key="join")       # a later join is announced again


def test_join_and_leave_play_one_after_another():
    player, opened, _ = make_player()
    player.play(sound(2, 1000), lane="announce", key="join")
    player.play(sound(2, 2000), lane="announce", key="leave")
    out = pull(opened[0], 4)
    first, second = out[:FRAME_SAMPLES * 2], out[FRAME_SAMPLES * 2:]
    assert near(first, 900) and near(second, 1800)


def test_button_prompt_cuts_the_previous_one_short():
    player, opened, _ = make_player()
    player.play(sound(5, 1000), lane="prompt", replace=True, gain=1.0)
    pull(opened[0], 1)
    player.play(sound(1, 3000), lane="prompt", replace=True, gain=1.0)
    out = pull(opened[0], 2)
    assert near(out[:FRAME_SAMPLES], 3000) and near(out[FRAME_SAMPLES:], 0)


def test_prompt_and_announcement_overlap_instead_of_waiting():
    player, opened, _ = make_player()
    player.play(sound(2, 1000), lane="announce", key="join", gain=1.0)
    player.play(sound(2, 500), lane="prompt", replace=True, gain=1.0)
    assert near(pull(opened[0], 1), 1500)


def test_volume_setting_applies_while_playing():
    player, opened, settings = make_player(volume=0.5)
    player.play(sound(2, 4000), gain=1.0)
    assert near(pull(opened[0], 1), 2000)
    settings["volume"] = 1.0
    assert near(pull(opened[0], 1), 4000)


def test_one_stream_for_many_sounds_and_a_new_one_for_a_new_device():
    player, opened, settings = make_player()
    player.play(sound(1), key="a")
    player.play(sound(1), key="b")
    assert len(opened) == 1
    settings["device"] = "Speakers"
    player.play(sound(1), key="c")
    assert len(opened) == 2 and opened[0].closed and opened[1].device == "Speakers"


def test_unplugged_device_reopens_on_the_next_sound():
    player, opened, _ = make_player()
    player.play(sound(1), key="a")
    opened[0].finished()
    player.play(sound(1), key="b")
    assert len(opened) == 2


def test_echo_canceller_gets_what_was_played_in_whole_frames():
    frames = []
    player, opened, _ = make_player(sink=frames.append)
    player.play(sound(2, 1000), gain=1.0)
    opened[0].fill(FRAME_SAMPLES // 2)
    assert frames == []
    opened[0].fill(FRAME_SAMPLES + FRAME_SAMPLES // 2)
    assert len(frames) == 2 and all(len(f) == FRAME_BYTES for f in frames)


def test_engine_adds_ui_sounds_to_the_echo_reference_only():
    from app.client.core.audio import AudioEngine, AudioSettings

    engine = AudioEngine(AudioSettings(noise="off", auto_gain=False), on_frame=lambda f: None)
    engine._running = True
    engine.add_reverse(sound(1, 1000))
    engine.add_reverse(b"short")                                 # not a whole frame: ignored
    assert len(engine._reverse_extra) == 1
    played = bytearray(FRAME_BYTES)
    engine._playback(played, FRAME_SAMPLES, None, None)
    assert bytes(played) == bytes(FRAME_BYTES)                   # the speakers get only the call
    assert len(engine._reverse_extra) == 0                       # ...the echo canceller got the sound


def test_stop_closes_the_stream():
    player, opened, _ = make_player()
    player.play(sound(1))
    player.stop()
    assert opened[0].closed and not player.is_playing()


def test_stream_stays_open_during_a_call_and_closes_after():
    player, opened, _ = make_player()
    player.keep_open = True
    player.warm()
    player._close_if_idle()                                      # the idle timer firing mid-call
    assert not opened[0].closed
    player.keep_open = False
    player._close_if_idle()
    assert opened[0].closed
    player.stop()
