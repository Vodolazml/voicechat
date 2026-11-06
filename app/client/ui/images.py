"""Pictures for QML: avatars from the server and screenshots for the screen share preview.

`image://avatar/user/5/3` is user 5's picture, version 3 (a new upload gets a new version, so the URL changes
and nothing stale is shown). People get a round picture, spaces a rounded square. `image://shot/monitor/1/<n>`
and `image://shot/window/<hwnd>/<n>` are fresh screenshots of a screen or a window; <n> only defeats caching.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
from PySide6.QtQuick import QQuickImageProvider

logger = logging.getLogger("voicechat.images")

AVATAR_UPLOAD_SIZE = 256
MAX_UPLOAD_BYTES = 58_000


def square_crop(image: QImage) -> QImage:
    side = min(image.width(), image.height())
    return image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side)


def shaped(image: QImage, size: int, round_person: bool) -> QImage:
    """Centre-cropped and scaled, clipped to a circle (people) or a rounded square (spaces)."""
    source = square_crop(image).scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    out = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    if round_person:
        path.addEllipse(QRectF(0, 0, size, size))
    else:
        path.addRoundedRect(QRectF(0, 0, size, size), size * 0.3, size * 0.3)
    painter.setClipPath(path)
    painter.drawImage(0, 0, source)
    painter.end()
    return out


def prepare_avatar(path: str) -> bytes:
    """Any picture file -> a square 256×256 JPEG small enough for the server."""
    image = QImage(path)
    if image.isNull():
        raise ValueError("Не получилось открыть картинку. Подойдут PNG, JPEG, BMP, WebP")
    square = square_crop(image).scaled(AVATAR_UPLOAD_SIZE, AVATAR_UPLOAD_SIZE, Qt.IgnoreAspectRatio,
                                       Qt.SmoothTransformation)
    flat = QImage(square.size(), QImage.Format_RGB32)
    flat.fill(QColor("#1a1b24"))  # transparent parts get the app's background, JPEG has no transparency
    painter = QPainter(flat)
    painter.drawImage(0, 0, square)
    painter.end()
    for quality in (90, 80, 70, 55, 40):
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        flat.save(buffer, "JPEG", quality)
        buffer.close()
        if data.size() <= MAX_UPLOAD_BYTES:
            return bytes(data)
    raise ValueError("Картинка получилась слишком большой")


class AvatarProvider(QQuickImageProvider):
    def __init__(self, fetch: Callable[[str, int], bytes]) -> None:
        super().__init__(QQuickImageProvider.Image, QQuickImageProvider.ForceAsynchronousImageLoading)
        self._fetch = fetch
        self._lock = threading.Lock()
        self._cache: dict[str, QImage] = {}

    def requestImage(self, ident: str, size: QSize, requested: QSize) -> QImage:  # noqa: N802 (Qt API)
        parts = ident.split("/")
        side = max(requested.width(), requested.height(), 32) if requested.isValid() else 128
        side = min(256, side)
        key = f"{ident}@{side}"
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            size.setWidth(cached.width()), size.setHeight(cached.height())
            return cached
        image = QImage()
        try:
            kind, item_id = parts[0], int(parts[1])
            if kind in ("user", "space"):
                image = QImage.fromData(self._fetch(kind, item_id))
                if not image.isNull():
                    image = shaped(image, side, kind == "user")
        except Exception:
            logger.debug("Avatar %s not loaded", ident, exc_info=True)
        if image.isNull():
            image = QImage(side, side, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
        else:
            with self._lock:
                self._cache[key] = image
        size.setWidth(image.width()), size.setHeight(image.height())
        return image


class ShotProvider(QQuickImageProvider):
    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.Image, QQuickImageProvider.ForceAsynchronousImageLoading)

    def requestImage(self, ident: str, size: QSize, requested: QSize) -> QImage:  # noqa: N802 (Qt API)
        from .screen import snapshot

        parts = ident.split("/")
        width = requested.width() if requested.isValid() and requested.width() > 0 else 480
        image = None
        try:
            image = snapshot(parts[0], int(parts[1]), width)
        except Exception:
            logger.debug("Snapshot %s failed", ident, exc_info=True)
        if image is None or image.isNull():
            image = QImage(16, 9, QImage.Format_RGB32)
            image.fill(QColor("#101118"))
        size.setWidth(image.width()), size.setHeight(image.height())
        return image
