"""Rasterizes assets/logo.svg into assets/app_icon.ico (multi-size, Vista-style PNG frames).

Run manually with: python assets/build_icon.py
Only needed when the source logo changes; the generated .ico is committed to the repo.
"""
from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()
    return data


def build_ico(png_frames: list[tuple[int, bytes]]) -> bytes:
    count = len(png_frames)
    header = struct.pack("<HHH", 0, 1, count)
    directory = b""
    image_data = b""
    offset = 6 + count * 16
    for size, data in png_frames:
        wh = size if size < 256 else 0
        directory += struct.pack(
            "<BBBBHHII", wh, wh, 0, 0, 1, 32, len(data), offset
        )
        image_data += data
        offset += len(data)
    return header + directory + image_data


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(ROOT / "logo.svg"))
    frames = [(size, render_png(renderer, size)) for size in SIZES]
    (ROOT / "app_icon.ico").write_bytes(build_ico(frames))
    (ROOT / "app_icon_256.png").write_bytes(dict(frames)[256])
    print("Wrote", ROOT / "app_icon.ico")


if __name__ == "__main__":
    main()
