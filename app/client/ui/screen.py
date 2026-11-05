"""Screen capture for screen share, and the video item that shows a friend's stream.

Capture uses Desktop Duplication through `dxcam` when it is installed (fast, works with borderless
games) and falls back to `mss`. Grabbing and scaling run in a worker thread, so the media loop never
stalls; if the encoder is slower than the screen, frames are dropped rather than queued.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import Property, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtQuick import QQuickPaintedItem

logger = logging.getLogger("voicechat.screen")


@dataclass(frozen=True)
class Preset:
    key: str
    title: str
    height: int
    fps: int
    bitrate: int


PRESETS = [
    Preset("720p30", "720p · 30 к/с", 720, 30, 2_500_000),
    Preset("1080p30", "1080p · 30 к/с", 1080, 30, 4_500_000),
    Preset("1080p60", "1080p · 60 к/с", 1080, 60, 7_000_000),
    Preset("1440p60", "1440p · 60 к/с", 1440, 60, 10_000_000),
    Preset("2160p30", "4K · 30 к/с", 2160, 30, 14_000_000),
]
PRESETS_BY_KEY = {preset.key: preset for preset in PRESETS}


def list_monitors() -> list[dict]:
    try:
        import mss

        with mss.mss() as grabber:
            return [{"index": i, "title": f"Экран {i} · {m['width']}×{m['height']}", "width": m["width"],
                     "height": m["height"]} for i, m in enumerate(grabber.monitors) if i > 0]
    except Exception:
        logger.exception("Monitor listing failed")
        return []


def _target_size(width: int, height: int, max_height: int) -> tuple[int, int]:
    if height <= max_height:
        out_w, out_h = width, height
    else:
        out_h = max_height
        out_w = int(width * max_height / height)
    return out_w - out_w % 2, out_h - out_h % 2  # encoders want even sizes


class ScreenCapturer:
    PREVIEW_EVERY = 6  # frames; ~10 fps self-preview is plenty and costs almost nothing

    def __init__(self, monitor: int, preset: Preset, preview=None) -> None:
        self.preview = preview  # called with a small QImage so you can see what you are sharing
        monitors = list_monitors()
        info = next((m for m in monitors if m["index"] == monitor), monitors[0] if monitors else None)
        if info is None:
            raise RuntimeError("Не найден экран для демонстрации")
        self.monitor = info["index"]
        self.width, self.height = _target_size(info["width"], info["height"], preset.height)
        self.fps = preset.fps
        self.max_bitrate = preset.bitrate
        self._stop = threading.Event()

    def _grabber(self):
        try:
            import dxcam  # optional, Windows only

            camera = dxcam.create(output_idx=self.monitor - 1, output_color="BGRA")
            if camera is not None:
                def grab():
                    frame = camera.grab()
                    if frame is None:
                        return None
                    h, w = frame.shape[:2]
                    return QImage(frame.tobytes(), w, h, w * 4, QImage.Format_ARGB32)
                return grab, lambda: camera.release()
        except Exception:
            pass
        import mss

        grabber = mss.mss()
        region = grabber.monitors[self.monitor]

        def grab():
            shot = grabber.grab(region)
            return QImage(shot.raw, shot.width, shot.height, shot.width * 4, QImage.Format_RGB32).copy()
        return grab, grabber.close

    async def run(self, source) -> None:
        from livekit import rtc

        loop = asyncio.get_running_loop()
        latest: list = [None]
        ready = asyncio.Event()

        def worker() -> None:
            grab, close = self._grabber()
            interval = 1.0 / self.fps
            last = None
            count = 0
            try:
                while not self._stop.is_set():
                    started = time.perf_counter()
                    image = grab()
                    if image is None:  # dxcam: screen did not change, resend the last frame
                        image = last
                    if image is not None:
                        last = image
                        if image.width() != self.width or image.height() != self.height:
                            image = image.scaled(self.width, self.height, Qt.IgnoreAspectRatio,
                                                 Qt.SmoothTransformation)
                        image = image.convertToFormat(QImage.Format_RGBA8888)
                        count += 1
                        if self.preview is not None and count % self.PREVIEW_EVERY == 1:
                            self.preview(image.scaled(960, 540, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                        latest[0] = bytes(image.constBits())
                        loop.call_soon_threadsafe(ready.set)
                    time.sleep(max(0.0, interval - (time.perf_counter() - started)))
            except Exception:
                logger.exception("Screen capture stopped")
            finally:
                close()

        thread = threading.Thread(target=worker, name="screen-capture", daemon=True)
        thread.start()
        try:
            while True:
                await ready.wait()
                ready.clear()
                data, latest[0] = latest[0], None
                if data:
                    source.capture_frame(rtc.VideoFrame(self.width, self.height, rtc.VideoBufferType.RGBA, data))
        finally:
            self._stop.set()


class VideoHub(QObject):
    """Latest decoded frame per streamer, delivered to the UI thread."""

    frame = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._frames: dict[int, QImage] = {}
        self.frame.connect(self._noop)

    def _noop(self, user_id: int) -> None:
        pass

    def push(self, user_id: int, video_frame) -> None:
        """Called from the media thread with an RGBA rtc.VideoFrame."""
        width, height = video_frame.width, video_frame.height
        image = QImage(bytes(video_frame.data), width, height, width * 4, QImage.Format_RGBA8888)
        with self._lock:
            self._frames[user_id] = image
        self.frame.emit(user_id)

    def push_image(self, user_id: int, image: QImage) -> None:
        """Local preview of our own screen share (any thread)."""
        with self._lock:
            self._frames[user_id] = image
        self.frame.emit(user_id)

    def latest(self, user_id: int) -> QImage | None:
        with self._lock:
            return self._frames.get(user_id)

    def drop(self, user_id: int) -> None:
        with self._lock:
            self._frames.pop(user_id, None)


HUB: VideoHub | None = None


def video_hub() -> VideoHub:
    global HUB
    if HUB is None:
        HUB = VideoHub()
    return HUB


class VideoView(QQuickPaintedItem):
    """`VideoView { userId: 5 }` paints that user's stream, letterboxed and smoothly scaled."""

    userIdChanged = Signal()
    hasFrameChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._user_id = 0
        self._image: QImage | None = None
        self.setRenderTarget(QQuickPaintedItem.FramebufferObject)
        video_hub().frame.connect(self._on_frame)

    def _get_user_id(self) -> int:
        return self._user_id

    def _set_user_id(self, value: int) -> None:
        if value != self._user_id:
            self._user_id = value
            self._image = video_hub().latest(value)
            self.userIdChanged.emit()
            self.hasFrameChanged.emit()
            self.update()

    userId = Property(int, _get_user_id, _set_user_id, notify=userIdChanged)
    hasFrame = Property(bool, lambda self: self._image is not None, notify=hasFrameChanged)

    def _on_frame(self, user_id: int) -> None:
        if user_id == self._user_id:
            had = self._image is not None
            self._image = video_hub().latest(user_id)
            if not had:
                self.hasFrameChanged.emit()
            self.update()

    def paint(self, painter: QPainter) -> None:
        image = self._image
        if image is None:
            return
        bounds = self.boundingRect()
        scale = min(bounds.width() / image.width(), bounds.height() / image.height())
        w, h = image.width() * scale, image.height() * scale
        target = QRectF(bounds.x() + (bounds.width() - w) / 2, bounds.y() + (bounds.height() - h) / 2, w, h)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(target, image)
