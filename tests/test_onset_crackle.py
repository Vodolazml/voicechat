"""No clicks where speech starts or stops: the mic stream never pauses, the voice fades in and out at the
gate, the limiter glides between gains, and a speaker who runs dry fades out instead of being cut."""
from types import SimpleNamespace

import numpy as np

from app.client.core.audio import AudioEngine, AudioSettings
from app.client.core.dsp import FRAME_BYTES, FRAME_SAMPLES, SILENCE, Limiter, Mixer

QUIET = SimpleNamespace(input_overflow=False)


def tone(amplitude, frequency=440.0):
    t = np.arange(FRAME_SAMPLES) / 48000
    return (np.sin(2 * np.pi * frequency * t + 0.7) * amplitude).astype(np.int16).tobytes()


def engine_with(sent):
    engine = AudioEngine(AudioSettings(noise="off", echo_cancellation=False, auto_gain=False, mode="ptt"),
                         on_frame=sent.append)
    return engine


def samples(frame):
    return np.frombuffer(frame, dtype=np.int16)


def test_mic_stream_carries_silence_between_words_and_fades_at_the_edges():
    sent = []
    engine = engine_with(sent)
    loud = tone(12000)
    engine._capture(loud, FRAME_SAMPLES, None, QUIET)            # key up: silence goes out, not nothing
    assert sent == [SILENCE]
    engine.ptt_pressed = True
    engine._capture(loud, FRAME_SAMPLES, None, QUIET)            # first frame of speech fades in
    engine._capture(loud, FRAME_SAMPLES, None, QUIET)            # then the voice as it is
    engine.ptt_pressed = False
    engine._capture(loud, FRAME_SAMPLES, None, QUIET)            # last one fades out
    engine._capture(loud, FRAME_SAMPLES, None, QUIET)
    first, middle, last, after = (samples(f) for f in sent[1:])
    assert abs(int(first[0])) < 50 and abs(int(first[-1])) > 5000
    assert abs(int(middle[0])) > 5000                             # not faded again (only high-passed)
    assert abs(int(last[0])) > 5000 and abs(int(last[-1])) < 50
    assert not after.any()
    assert engine.stats()["sent_frames"] == 2                    # the log still counts real speech only


def test_muted_mic_sends_nothing_at_all():
    sent = []
    engine = engine_with(sent)
    engine.muted = True
    engine.ptt_pressed = True
    engine._capture(tone(12000), FRAME_SAMPLES, None, QUIET)
    assert sent == []


def test_limiter_glides_instead_of_stepping_on_a_loud_onset():
    limiter = Limiter()
    limiter.process(np.full(FRAME_SAMPLES, 1000.0, dtype=np.float32))
    loud = np.full(FRAME_SAMPLES, 32000.0, dtype=np.float32)
    out = limiter.process(loud)
    steps = np.abs(np.diff(out))
    assert steps.max() < 200                                    # no jump inside the frame
    assert np.abs(out).max() <= 32767


def test_speaker_running_dry_fades_out_and_comes_back_faded_in():
    mixer = Mixer()
    mixer.normalize = False
    frame = tone(10000)
    for _ in range(2):
        mixer.put(1, frame)
    a = samples(mixer.mix())
    b = samples(mixer.mix())
    gap = samples(mixer.mix())                                   # nothing arrived in time
    assert abs(int(a[0])) < 400                                  # first frame fades in
    assert abs(int(gap[0]) - int(b[-1])) < 400                   # picks up where the voice stopped
    assert abs(int(gap[-1])) < 50                                # ...and ends in silence
    assert mixer.underrun_stats() == {1: 1}
    for _ in range(2):
        mixer.put(1, frame)
    back = samples(mixer.mix())
    assert abs(int(back[0])) < 400                               # resumes faded in, not with a step
    assert len(SILENCE) == FRAME_BYTES
