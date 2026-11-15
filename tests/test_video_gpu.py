"""Received video goes to the graphics card as YUV (I420), not converted to RGBA on the processor."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("PySide6.QtMultimedia")

from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat, QVideoSink  # noqa: E402

from app.client.ui import screen  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def app():
    return QGuiApplication.instance() or QGuiApplication([])


def _packed_i420(width, height):
    cw, ch = (width + 1) // 2, (height + 1) // 2
    y = (np.arange(width * height) % 251).astype(np.uint8)
    u = np.full(cw * ch, 90, np.uint8)
    v = np.full(cw * ch, 200, np.uint8)
    return np.concatenate([y, u, v]).tobytes(), y.reshape(height, width)


@pytest.mark.parametrize("size", [(64, 36), (5, 3), (1366, 769)])
def test_i420_planes_land_in_the_video_frame(size):
    width, height = size
    data, y = _packed_i420(width, height)
    frame = screen.i420_frame(width, height, data)
    assert frame.pixelFormat() == QVideoFrameFormat.PixelFormat.Format_YUV420P
    assert (frame.width(), frame.height()) == size
    assert frame.map(QVideoFrame.MapMode.ReadOnly)
    try:
        stride = frame.bytesPerLine(0)
        got = np.frombuffer(frame.bits(0), np.uint8)[:stride * height].reshape(height, stride)[:, :width]
        assert (got == y).all()
        cw = (width + 1) // 2
        u = np.frombuffer(frame.bits(1), np.uint8)
        assert (u[:cw] == 90).all()
        v = np.frombuffer(frame.bits(2), np.uint8)
        assert (v[:cw] == 200).all()
    finally:
        frame.unmap()


class _I420:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.data, _ = _packed_i420(width, height)


def test_feed_shows_the_persons_frames_in_the_sink_and_clears_on_drop():
    hub = screen.video_hub()
    sink = QVideoSink()
    feed = screen.VideoFeed()
    feed.videoSink = sink
    feed.userId = 77
    assert not feed.hasFrame
    hub.push(77, _I420(320, 180))
    assert feed.hasFrame
    assert sink.videoFrame().size().width() == 320
    assert hub.received(77)["width"] == 320
    hub.push(78, _I420(640, 360))  # someone else's frame does not reach this view
    assert sink.videoFrame().size().width() == 320
    hub.drop(77)
    assert not feed.hasFrame and not sink.videoFrame().isValid()


def test_local_preview_image_is_shown_too():
    hub = screen.video_hub()
    sink = QVideoSink()
    feed = screen.VideoFeed()
    feed.videoSink = sink
    feed.userId = 90
    image = QImage(200, 100, QImage.Format_RGBA8888)
    image.fill(0)
    hub.push_image(90, image)
    assert feed.hasFrame and sink.videoFrame().size().width() == 200
    hub.drop(90)
