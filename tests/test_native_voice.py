"""The native voice engine (vcvoice) driven through NativeVoice, with fake audio instead of real devices.
Skipped when the engine has not been built (cargo build --release in native/vcvoice)."""
import time
from pathlib import Path

import pytest

from app.client.core.audio import AudioSettings

ENGINE = Path(__file__).resolve().parents[1] / "native" / "vcvoice" / "target" / "release" / "vcvoice"
pytestmark = pytest.mark.skipif(not ENGINE.is_file(), reason="vcvoice is not built")


def wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def voice(monkeypatch):
    from app.client.core.native_voice import NativeVoice
    monkeypatch.setenv("VCVOICE_FAKE_AUDIO", "tone")
    problems = []
    v = NativeVoice(AudioSettings(), on_problem=problems.append, path=ENGINE)
    v.problems = problems
    yield v
    v.close()


def test_engine_paths(monkeypatch):
    from app.client.core import native_voice
    monkeypatch.setenv("VOICECHAT_NATIVE_VOICE", "0")
    assert native_voice.engine_path() is None
    monkeypatch.delenv("VOICECHAT_NATIVE_VOICE")
    monkeypatch.delenv("VOICECHAT_VCVOICE", raising=False)
    assert native_voice.engine_path() is None  # developing: the Python engine unless asked for
    monkeypatch.setenv("VOICECHAT_NATIVE_VOICE", "1")
    assert native_voice.engine_path() == ENGINE


def test_transmits_and_reports(voice):
    seen = []
    voice.on_transmitting = seen.append
    voice.start()
    assert wait(lambda: True in seen), "the gate never opened on the tone"
    stats = voice.stats()
    assert stats.get("frames", stats.get("captured_frames", 1)) > 0
    voice.set_level_reports(True)
    assert wait(lambda: voice.input_level_db > -40)
    voice.set_level_reports(False)
    voice.muted = True
    assert wait(lambda: False in seen)


def test_restarts_after_crash(voice):
    voice.start()
    voice._proc.kill()
    assert wait(lambda: "engine_restarted" in voice.problems, timeout=8)
    seen = []
    voice.on_transmitting = seen.append
    assert wait(lambda: voice.stats() != {}, timeout=5)
