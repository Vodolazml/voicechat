"""In a call nothing may animate endlessly: one running QML animation, even in a hidden window, makes Qt
redraw every shown window at full speed (140 fps behind a game on a 60 Hz monitor, 2026-11-17)."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_nothing_animates_in_a_call(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    out = subprocess.run([sys.executable, str(ROOT / "tests" / "helpers" / "idle_animations.py"), str(ROOT)],
                         capture_output=True, text=True, timeout=60, env=env)
    lines = [line for line in out.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, out.stderr[-2000:]
    assert json.loads(lines[-1][7:]) == []
