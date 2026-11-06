"""0.4.0 client pieces: pictures, the wake-up tune, window capture helpers, camera keys."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def gui_app():
    return QGuiApplication.instance() or QGuiApplication([])


def test_any_picture_becomes_a_small_square_jpeg(tmp_path):
    from app.client.ui.images import MAX_UPLOAD_BYTES, prepare_avatar, shaped

    source = QImage(1600, 900, QImage.Format_ARGB32)
    source.fill(QColor("#3a7bd5"))
    path = tmp_path / "wide.png"
    source.save(str(path))
    data = prepare_avatar(str(path))
    assert data.startswith(b"\xff\xd8\xff") and len(data) <= MAX_UPLOAD_BYTES
    picture = QImage.fromData(data)
    assert (picture.width(), picture.height()) == (256, 256)
    with pytest.raises(ValueError):
        prepare_avatar(str(tmp_path / "missing.png"))
    # A person's picture is a circle: the corners are transparent, the middle is not.
    round_picture = shaped(picture, 64, round_person=True)
    assert round_picture.pixelColor(1, 1).alpha() == 0 and round_picture.pixelColor(32, 32).alpha() == 255


def test_wake_tune_is_five_to_ten_seconds_of_whole_frames():
    from app.client.core.dsp import FRAME_BYTES, SAMPLE_RATE, Mixer
    from app.client.ui.backend import SOUNDS

    tune = SOUNDS["wake"]
    seconds = len(tune) / 2 / SAMPLE_RATE
    assert 5 <= seconds <= 10 and len(tune) % FRAME_BYTES == 0
    # The mixer keeps all of it (it used to cut effects at 6 s) and plays it even with the sound off.
    mixer = Mixer()
    mixer.deafened = True
    mixer.play_effect(tune, always=True)
    frames = len(tune) // FRAME_BYTES
    assert any(mixer.mix() != bytes(FRAME_BYTES) for _ in range(frames))


def test_cursor_is_drawn_and_windows_keep_their_shape():
    from app.client.ui.screen import _fit, draw_cursor, list_windows, monitor_for_rect

    image = QImage(200, 100, QImage.Format_RGB32)
    image.fill(0xFF000000)
    draw_cursor(image, 50, 20)
    assert image.pixelColor(52, 30) == QColor("white")
    # A window that became taller than the stream keeps its proportions with black bars.
    fitted = _fit(QImage(100, 200, QImage.Format_RGB32), 400, 200)
    assert (fitted.width(), fitted.height()) == (400, 200) and fitted.pixelColor(5, 100) == QColor("black")
    monitors = [{"left": 0, "top": 0, "width": 1920, "height": 1080},
                {"left": 1920, "top": 0, "width": 2560, "height": 1440}]
    assert monitor_for_rect((1800, 100, 2600, 700), monitors) is monitors[1]
    assert isinstance(list_windows(), list)


def test_camera_frames_have_their_own_key():
    from app.client.core.media import CAMERA_KEY_OFFSET, camera_key

    assert camera_key(7) == CAMERA_KEY_OFFSET + 7 and camera_key(7) != 7
