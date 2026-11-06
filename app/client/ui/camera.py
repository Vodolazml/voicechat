"""Webcam for calls, through Qt Multimedia (already part of the app, no extra packages).

The camera delivers frames on the interface thread; each one is converted to BGRA, scaled to the size we
send and handed to the media thread. At most 30 frames a second and 720p, so it costs the call little.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QImage

logger = logging.getLogger("voicechat.camera")

MAX_HEIGHT = 720
FPS = 30
BITRATE = 1_700_000


def list_cameras() -> list[dict]:
    try:
        from PySide6.QtMultimedia import QMediaDevices

        default = QMediaDevices.defaultVideoInput()
        return [{"id": bytes(device.id()).decode(errors="replace"), "title": device.description(), "index": index,
                 "isDefault": device.id() == default.id()}
                for index, device in enumerate(QMediaDevices.videoInputs())]
    except Exception:
        logger.exception("Camera listing failed")
        return []


def _pick_format(device):
    """The best format up to 720p that runs at 24 frames a second or more."""
    best, best_score = None, None
    for fmt in device.videoFormats():
        size = fmt.resolution()
        if size.height() > MAX_HEIGHT or fmt.maxFrameRate() < 24:
            continue
        score = (size.height(), min(fmt.maxFrameRate(), FPS))
        if best_score is None or score > best_score:
            best, best_score = fmt, score
    return best


class CameraCapturer(QObject):
    def __init__(self, camera_id: str, preview: Callable[[QImage], None] | None = None,
                 on_error: Callable[[str], None] = lambda text: None) -> None:
        super().__init__()
        from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices, QVideoSink

        devices = QMediaDevices.videoInputs()
        if not devices:
            raise RuntimeError("Камера не найдена")
        device = next((d for d in devices if bytes(d.id()).decode(errors="replace") == camera_id),
                      QMediaDevices.defaultVideoInput() if not QMediaDevices.defaultVideoInput().isNull() else devices[0])
        self.title = device.description()
        self.preview = preview
        self.on_error = on_error
        self._camera = QCamera(device)
        fmt = _pick_format(device)
        if fmt is not None:
            self._camera.setCameraFormat(fmt)
            size = fmt.resolution()
            width, height = size.width(), size.height()
        else:
            width, height = 1280, 720
        self.width, self.height = width - width % 2, height - height % 2
        self.fps = FPS
        self.max_bitrate = BITRATE
        self._session = QMediaCaptureSession()
        self._sink = QVideoSink()
        self._session.setCamera(self._camera)
        self._session.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._camera.errorOccurred.connect(lambda _err, text: self.on_error(text or "Камера недоступна"))
        self._source = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_sent = 0.0
        self._last_preview = 0.0

    def start(self) -> None:
        self._camera.start()

    def stop(self) -> None:
        try:
            self._sink.videoFrameChanged.disconnect(self._on_frame)
        except (RuntimeError, TypeError):
            pass
        self._camera.stop()
        self._source = None

    def _on_frame(self, frame) -> None:
        now = time.perf_counter()
        if now - self._last_sent < 1.0 / (self.fps + 2):
            return
        self._last_sent = now
        image = frame.toImage()
        if image.isNull():
            return
        if image.width() != self.width or image.height() != self.height:
            image = image.scaled(self.width, self.height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        if image.format() != QImage.Format_RGB32:
            image = image.convertToFormat(QImage.Format_RGB32)
        if self.preview is not None and now - self._last_preview >= 1 / 20:
            self._last_preview = now
            self.preview(image)
        source, loop = self._source, self._loop
        if source is None or loop is None or loop.is_closed():
            return
        data = bytes(image.constBits())[:self.width * self.height * 4]
        loop.call_soon_threadsafe(self._send, source, data)

    def _send(self, source, data: bytes) -> None:
        from livekit import rtc

        try:
            source.capture_frame(rtc.VideoFrame(self.width, self.height, rtc.VideoBufferType.BGRA, data))
        except Exception:
            logger.debug("Camera frame dropped", exc_info=True)

    async def run(self, source) -> None:
        """Runs on the media thread for as long as the camera track is published."""
        self._loop = asyncio.get_running_loop()
        self._source = source
        try:
            await asyncio.Event().wait()
        finally:
            self._source = None
