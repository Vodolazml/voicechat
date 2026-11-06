"""Screen capture for screen share, and the video item that shows a friend's stream.

Capture uses Desktop Duplication through `dxcam` when it is installed (fast, works with borderless
games) and falls back to `mss`. Grabbing and scaling run in a separate process (a thread if that fails),
so neither the media loop nor the audio callbacks stall; if the encoder is slower than the screen, frames
are dropped rather than queued.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import Property, QObject, QRectF, Qt, QTimer, Signal
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
    Preset("720p30", "720p · 30 к/с", 720, 30, 3_000_000),
    Preset("1080p30", "1080p · 30 к/с", 1080, 30, 5_000_000),
    Preset("1080p60", "1080p · 60 к/с", 1080, 60, 8_000_000),
    Preset("1440p60", "1440p · 60 к/с", 1440, 60, 12_000_000),
    Preset("2160p30", "4K · 30 к/с", 2160, 30, 16_000_000),
]
PRESETS_BY_KEY = {preset.key: preset for preset in PRESETS}


def list_monitors() -> list[dict]:
    if os.environ.get("VOICECHAT_TEST_CAPTURE") == "1":
        return [{"index": 1, "title": "Экран 1 · 1920×1080", "width": 1920, "height": 1080, "left": 0, "top": 0},
                {"index": 2, "title": "Экран 2 · 2560×1440", "width": 2560, "height": 1440, "left": 1920, "top": 0}]
    try:
        import mss

        with mss.mss() as grabber:
            return [{"index": i, "title": f"Экран {i} · {m['width']}×{m['height']}", "width": m["width"],
                     "height": m["height"], "left": m["left"], "top": m["top"]}
                    for i, m in enumerate(grabber.monitors) if i > 0]
    except Exception:
        logger.exception("Monitor listing failed")
        return []


# --- windows (Windows only) -----------------------------------------------------------------------

def _user32():
    import ctypes

    return ctypes.windll.user32  # type: ignore[attr-defined]


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Visible bounds of a window in physical screen pixels (left, top, right, bottom), without the invisible
    resize border Windows 10/11 adds around it. None if it is gone or minimised."""
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    try:
        ok = ctypes.windll.dwmapi.DwmGetWindowAttribute(  # DWMWA_EXTENDED_FRAME_BOUNDS
            wintypes.HWND(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0
    except Exception:
        ok = False
    if not ok and not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    if rect.right - rect.left < 2 or rect.bottom - rect.top < 2:
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def list_windows() -> list[dict]:
    """Windows you can show: visible, titled, top-level application windows (not this app's own)."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    dwm = ctypes.windll.dwmapi
    kernel32 = ctypes.windll.kernel32
    own_pid = os.getpid()
    found: list[dict] = []

    def app_name(pid: int) -> str:
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return os.path.splitext(os.path.basename(buffer.value))[0]
        finally:
            kernel32.CloseHandle(handle)
        return ""

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER: skip owned popups
                return True
            if user32.GetWindowLongW(hwnd, -20) & 0x80:  # WS_EX_TOOLWINDOW
                return True
            cloaked = wintypes.DWORD(0)
            dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))  # DWMWA_CLOAKED
            if cloaked.value:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == own_pid:
                return True
            minimized = bool(user32.IsIconic(hwnd))
            rect = window_rect(int(hwnd)) if not minimized else None
            if rect is None and not minimized:
                return True
            if rect and (rect[2] - rect[0] < 120 or rect[3] - rect[1] < 80):
                return True
            if title.value in ("Program Manager", "Windows Input Experience"):
                return True
            found.append({"hwnd": int(hwnd), "title": title.value, "app": app_name(pid.value),
                          "minimized": minimized})
        except Exception:
            logger.debug("Window skipped", exc_info=True)
        return True

    try:
        user32.EnumWindows(visit, 0)
    except Exception:
        logger.exception("Window listing failed")
    return found


def monitor_for_rect(rect: tuple[int, int, int, int], monitors: list[dict]) -> dict | None:
    """The monitor that holds most of the rectangle."""
    best, best_area = None, 0
    for monitor in monitors:
        left = max(rect[0], monitor["left"])
        top = max(rect[1], monitor["top"])
        right = min(rect[2], monitor["left"] + monitor["width"])
        bottom = min(rect[3], monitor["top"] + monitor["height"])
        area = max(0, right - left) * max(0, bottom - top)
        if area > best_area:
            best, best_area = monitor, area
    return best


def cursor_position() -> tuple[int, int] | None:
    """Where the mouse pointer is (physical screen pixels), or None if it is hidden (e.g. in a game)."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class CURSORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hCursor", wintypes.HANDLE),
                    ("ptScreenPos", wintypes.POINT)]

    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(info)
    try:
        if not _user32().GetCursorInfo(ctypes.byref(info)) or not info.flags & 1:  # CURSOR_SHOWING
            return None
    except Exception:
        return None
    return info.ptScreenPos.x, info.ptScreenPos.y


def draw_cursor(image: QImage, x: float, y: float, scale: float = 1.0) -> None:
    """A classic arrow pointer with a dark outline, its tip at (x, y) in image pixels."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QPen, QPolygonF

    if x < -40 or y < -40 or x > image.width() + 2 or y > image.height() + 2:
        return
    points = [(0, 0), (0, 17), (4.2, 13.2), (7, 19.5), (9.6, 18.4), (6.9, 12.2), (12.2, 12.2)]
    polygon = QPolygonF([QPointF(x + px * scale, y + py * scale) for px, py in points])
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(0, 0, 0, 230), max(1.0, 1.4 * scale)))
    painter.setBrush(QColor(255, 255, 255))
    painter.drawPolygon(polygon)
    painter.end()


def resolve_source(kind: str, ident: int) -> dict:
    """What to capture: a whole monitor, or one window (with the monitor it is on)."""
    monitors = list_monitors()
    if not monitors:
        raise RuntimeError("Не найден экран для демонстрации")
    if kind == "window":
        rect = window_rect(ident)
        if rect is None:
            raise RuntimeError("Окно свёрнуто или закрыто. Разверните его и попробуйте снова")
        monitor = monitor_for_rect(rect, monitors) or monitors[0]
        return {**monitor, "kind": "window", "hwnd": ident, "window_width": rect[2] - rect[0],
                "window_height": rect[3] - rect[1]}
    monitor = next((m for m in monitors if m["index"] == ident), monitors[0])
    return {**monitor, "kind": "monitor"}


def snapshot(kind: str, ident: int, width: int) -> QImage | None:
    """One quick screenshot of a monitor or a window, scaled to `width` (for the preview in the dialog)."""
    import mss

    if os.environ.get("VOICECHAT_TEST_CAPTURE") == "1":
        image = QImage(width, width * 9 // 16, QImage.Format_RGB32)
        image.fill(0xFF203040)
        return image
    with mss.mss() as grabber:
        if kind == "window":
            rect = window_rect(ident)
            if rect is None:
                return None
            region = {"left": rect[0], "top": rect[1], "width": rect[2] - rect[0], "height": rect[3] - rect[1]}
        else:
            if ident >= len(grabber.monitors):
                return None
            region = grabber.monitors[ident]
        shot = grabber.grab(region)
        image = QImage(shot.raw, shot.width, shot.height, shot.width * 4, QImage.Format_RGB32).copy()
        position = cursor_position()
        if position is not None:
            draw_cursor(image, position[0] - region["left"], position[1] - region["top"], max(1.0, shot.height / 1080))
    return image.scaledToWidth(max(16, width), Qt.SmoothTransformation)


def _target_size(width: int, height: int, max_height: int) -> tuple[int, int]:
    if height <= max_height:
        out_w, out_h = width, height
    else:
        out_h = max_height
        out_w = int(width * max_height / height)
    return out_w - out_w % 2, out_h - out_h % 2  # encoders want even sizes


_DXCAM_LINE = re.compile(r"Device\[(\d+)\]\s*Output\[(\d+)\]:\s*Res:\((\d+),\s*(\d+)\)\s*Rot:(\d+)\s*Primary:(\w+)")


def dxcam_target(info_text: str, monitor: dict, monitor_count: int) -> tuple[int, int] | None:
    """Which (device, output) of Desktop Duplication shows this monitor.

    A second screen often hangs off another GPU (for example the integrated one), so the output index alone
    is not enough. We match on resolution and on being the primary screen (at 0,0), and fall back to order.
    """
    outputs = []
    for match in _DXCAM_LINE.finditer(info_text):
        device, output, width, height, rotation, primary = match.groups()
        width, height = int(width), int(height)
        if int(rotation) in (90, 270, 1, 3):
            width, height = height, width
        outputs.append((int(device), int(output), width, height, primary.lower() == "true"))
    if not outputs:
        return None
    is_primary = monitor.get("left", 0) == 0 and monitor.get("top", 0) == 0
    size = (monitor["width"], monitor["height"])
    candidates = [o for o in outputs if (o[2], o[3]) == size and o[4] == is_primary] or \
                 [o for o in outputs if (o[2], o[3]) == size]
    if len(candidates) == 1 or (candidates and monitor_count == len(outputs)):
        return candidates[0][0], candidates[0][1]
    index = monitor["index"] - 1
    return (outputs[index][0], outputs[index][1]) if 0 <= index < len(outputs) else None


class _Rate:
    """Frames per second over the last second, measured, not configured."""

    def __init__(self) -> None:
        self._times: list[float] = []

    def tick(self) -> None:
        now = time.perf_counter()
        self._times.append(now)
        while self._times and now - self._times[0] > 1.0:
            self._times.pop(0)

    def value(self) -> float:
        now = time.perf_counter()
        while self._times and now - self._times[0] > 1.0:
            self._times.pop(0)
        return float(len(self._times))


def _make_grabber(info: dict, monitor_count: int, fps: int):
    """Returns (grab, close, paced, backend). grab() gives a QImage in BGRA memory order (RGB32) or None.

    For a window, the monitor it is on is captured and cut to the window's current bounds; the mouse
    pointer is painted in, because Desktop Duplication leaves it out."""
    grab, close, paced, backend = _make_monitor_grabber(info, monitor_count, fps)
    window = info.get("hwnd") if info.get("kind") == "window" else None
    origin_x, origin_y = info.get("left", 0), info.get("top", 0)
    cursor_scale = max(1.0, info.get("height", 1080) / 1080)
    last: list = [None]

    def grab_with_extras():
        image = grab()
        if image is None:
            return None
        left, top = origin_x, origin_y
        if window is not None:
            rect = window_rect(window)
            if rect is None:
                return last[0]  # minimised: keep showing the last picture
            x0, y0 = max(0, rect[0] - origin_x), max(0, rect[1] - origin_y)
            x1, y1 = min(image.width(), rect[2] - origin_x), min(image.height(), rect[3] - origin_y)
            if x1 - x0 < 2 or y1 - y0 < 2:
                return last[0]  # moved to another screen
            image = image.copy(x0, y0, x1 - x0, y1 - y0)
            left, top = origin_x + x0, origin_y + y0
        position = cursor_position()
        if position is not None:
            draw_cursor(image, position[0] - left, position[1] - top, cursor_scale)
        last[0] = image
        return image
    return grab_with_extras, close, paced, backend


def _make_monitor_grabber(info: dict, monitor_count: int, fps: int):
    if os.environ.get("VOICECHAT_TEST_CAPTURE") == "1":
        # Tests: a moving gradient instead of a real screen, so the whole capture path runs without a display.
        counter = [0]

        def fake():
            counter[0] += 1
            image = QImage(info["width"], info["height"], QImage.Format_RGB32)
            image.fill(0xFF000000 | (counter[0] * 40 % 256) << 8)
            return image
        return fake, lambda: None, False, "test"
    try:
        import dxcam  # optional, Windows only

        target = dxcam_target(dxcam.output_info(), info, monitor_count)
        if target is not None:
            camera = dxcam.create(device_idx=target[0], output_idx=target[1], output_color="BGRA")
            if camera is not None:
                # Desktop Duplication on its own thread at the wanted rate; when nothing on screen changes
                # it repeats the last frame, so the stream never stalls.
                camera.start(target_fps=fps, video_mode=True)

                def grab():
                    frame = camera.get_latest_frame()
                    if frame is None:
                        return None
                    h, w = frame.shape[:2]
                    # copy(): the image must own its pixels, it outlives this numpy buffer.
                    return QImage(frame.data, w, h, w * 4, QImage.Format_RGB32).copy()

                def close():
                    try:
                        camera.stop()
                    finally:
                        camera.release()
                return grab, close, True, "dxcam"
    except Exception:
        logger.warning("Fast screen capture (dxcam) unavailable, using the slow one", exc_info=True)
    import mss

    grabber = mss.mss()
    region = grabber.monitors[info["index"]]

    def grab():
        shot = grabber.grab(region)
        return QImage(shot.raw, shot.width, shot.height, shot.width * 4, QImage.Format_RGB32).copy()
    return grab, grabber.close, False, "mss"


def _fit(image: QImage, width: int, height: int) -> QImage:
    if image.width() != width or image.height() != height:
        if abs(image.width() / max(1, image.height()) - width / height) < 0.01:
            image = image.scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        else:  # a window changed its shape: keep its proportions, black bars around it
            scaled = image.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            canvas = QImage(width, height, QImage.Format_RGB32)
            canvas.fill(0xFF000000)
            painter = QPainter(canvas)
            painter.drawImage((width - scaled.width()) // 2, (height - scaled.height()) // 2, scaled)
            painter.end()
            image = canvas
    if image.format() != QImage.Format_RGB32:
        image = image.convertToFormat(QImage.Format_RGB32)
    return image


def capture_process_main(info: dict, monitor_count: int, width: int, height: int, fps: int, shm_name: str,
                         slots: int, conn, stop) -> None:
    """Runs in a child process. Grabbing and scaling a big screen 60 times a second keeps a CPU core busy
    and, inside the app, held Python's lock so long that the microphone and speaker callbacks missed their
    turn: the voice broke up or stopped while sharing. In its own process it cannot get in their way.
    Frames go through shared memory slots; only the slot number travels through the pipe."""
    from multiprocessing import shared_memory

    if os.name == "nt":
        try:  # same physical pixel coordinates as the app (windows, the mouse pointer)
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    frame_bytes = width * height * 4
    shm = shared_memory.SharedMemory(name=shm_name)
    try:
        grab, close, paced, backend = _make_grabber(info, monitor_count, fps)
    except Exception as exc:
        conn.send(("error", str(exc)))
        shm.close()
        return
    conn.send(("backend", backend))
    interval = 1.0 / fps
    slot = 0
    try:
        while not stop.is_set():
            started = time.perf_counter()
            image = grab()
            if image is not None:
                image = _fit(image, width, height)
                bits = image.constBits()
                if image.bytesPerLine() == width * 4:
                    shm.buf[slot * frame_bytes:(slot + 1) * frame_bytes] = bits[:frame_bytes]
                    spent = time.perf_counter() - started
                    conn.send(("frame", slot, spent * 1000))
                    slot = (slot + 1) % slots
            if not paced:
                time.sleep(max(0.0, interval - (time.perf_counter() - started)))
    except (EOFError, BrokenPipeError, OSError):
        pass  # the app closed the pipe: sharing stopped
    finally:
        try:
            close()
        except Exception:
            pass
        shm.close()


class ScreenCapturer:
    PREVIEW_FPS = 20  # your own preview: full size, sharp, but not so often that it costs the call anything
    SLOTS = 3

    def __init__(self, monitor: int, preset: Preset, preview=None, kind: str = "monitor") -> None:
        self.preview = preview  # called with a QImage so you can see what you are sharing
        info = resolve_source(kind, monitor)
        self._info = info
        self._monitor_count = len(list_monitors())
        self.monitor = info["index"]
        self.kind = info["kind"]
        source_w = info.get("window_width", info["width"])
        source_h = info.get("window_height", info["height"])
        self._source_size = (source_w, source_h)
        self.width, self.height = _target_size(source_w, source_h, preset.height)
        self.fps = preset.fps
        self.max_bitrate = preset.bitrate
        self._stop = threading.Event()
        self._captured = _Rate()
        self._sent = _Rate()
        self._grab_ms = 0.0
        self.backend = ""
        # Filled by the media session from WebRTC's own statistics of the encoded stream.
        self.encoder: dict = {}

    def stats(self) -> dict:
        """What really happens right now: captured and sent frames per second, the encoded size and rate."""
        return {"capture_fps": self._captured.value(), "sent_fps": self._sent.value(), "width": self.width,
                "source_width": self._source_size[0], "source_height": self._source_size[1],
                "height": self.height, "grab_ms": round(self._grab_ms, 1), "backend": self.backend,
                **self.encoder}

    def _grabber(self):
        grab, close, paced, backend = _make_grabber(self._info, self._monitor_count, self.fps)
        self.backend = backend
        return grab, close, paced

    async def run(self, source) -> None:
        try:
            await self._run_process(source)
        except _NoProcess as exc:
            logger.warning("Capture process unavailable (%s), capturing inside the app", exc)
            await self._run_thread(source)

    def _send(self, source, data, last_preview: list) -> None:
        from livekit import rtc

        # BGRA memory order: no colour conversion on our side, WebRTC converts straight to I420.
        source.capture_frame(rtc.VideoFrame(self.width, self.height, rtc.VideoBufferType.BGRA, data))
        self._sent.tick()
        now = time.perf_counter()
        if self.preview is not None and now - last_preview[0] >= 1.0 / self.PREVIEW_FPS:
            last_preview[0] = now
            self.preview(QImage(data, self.width, self.height, self.width * 4, QImage.Format_RGB32).copy())

    async def _run_process(self, source) -> None:
        import multiprocessing as mp
        from multiprocessing import shared_memory

        loop = asyncio.get_running_loop()
        frame_bytes = self.width * self.height * 4
        try:
            ctx = mp.get_context("spawn")
            shm = shared_memory.SharedMemory(create=True, size=frame_bytes * self.SLOTS)
        except Exception as exc:
            raise _NoProcess(exc) from exc
        parent, child = ctx.Pipe(duplex=False)
        stop = ctx.Event()
        process = ctx.Process(target=capture_process_main, name="screen-capture", daemon=True,
                              args=(self._info, self._monitor_count, self.width, self.height, self.fps,
                                    shm.name, self.SLOTS, child, stop))
        latest: list = [None]
        ready = asyncio.Event()
        started = threading.Event()
        failure: list = [None]

        def reader() -> None:
            """Blocking pipe reads on their own thread (they release Python's lock while waiting)."""
            try:
                while True:
                    message = parent.recv()
                    if message[0] == "frame":
                        latest[0] = message[1]
                        self._grab_ms = self._grab_ms * 0.9 + message[2] * 0.1
                        self._captured.tick()
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(ready.set)
                    elif message[0] == "backend":
                        self.backend = message[1]
                        started.set()
                    elif message[0] == "error":
                        failure[0] = message[1]
                        started.set()
                        return
            except (EOFError, OSError):
                started.set()

        try:
            process.start()
        except Exception as exc:
            shm.close()
            shm.unlink()
            raise _NoProcess(exc) from exc
        child.close()
        threading.Thread(target=reader, name="screen-capture-pipe", daemon=True).start()
        try:
            await asyncio.to_thread(started.wait, 15)
            if not self.backend:
                raise _NoProcess(failure[0] or "the capture process did not start")
            logger.info("Screen capture (own process): %s, %dx%d at %d fps", self.backend, self.width,
                        self.height, self.fps)
            last_preview = [0.0]
            while True:
                await ready.wait()
                ready.clear()
                slot, latest[0] = latest[0], None
                if slot is None:
                    continue
                if not process.is_alive():
                    raise RuntimeError("screen capture process stopped")
                data = bytes(shm.buf[slot * frame_bytes:(slot + 1) * frame_bytes])
                self._send(source, data, last_preview)
        finally:
            stop.set()
            await asyncio.to_thread(process.join, 2)
            if process.is_alive():
                process.terminate()
            parent.close()
            shm.close()
            try:
                shm.unlink()
            except FileNotFoundError:
                pass

    async def _run_thread(self, source) -> None:
        """Fallback: capture on a thread of this process (the old way)."""
        loop = asyncio.get_running_loop()
        latest: list = [None]
        ready = asyncio.Event()

        def wake() -> None:
            if not loop.is_closed():
                loop.call_soon_threadsafe(ready.set)

        def worker() -> None:
            grab, close, paced = self._grabber()
            logger.info("Screen capture: %s, %dx%d at %d fps", self.backend, self.width, self.height, self.fps)
            interval = 1.0 / self.fps
            try:
                while not self._stop.is_set():
                    started = time.perf_counter()
                    image = grab()
                    if image is not None:
                        image = _fit(image, self.width, self.height)
                        latest[0] = bytes(image.constBits())[:self.width * self.height * 4]
                        self._captured.tick()
                        wake()
                    spent = time.perf_counter() - started
                    self._grab_ms = self._grab_ms * 0.9 + spent * 1000 * 0.1
                    if not paced:
                        time.sleep(max(0.0, interval - spent))
            except Exception:
                logger.exception("Screen capture stopped")
            finally:
                try:
                    close()
                except Exception:
                    logger.debug("Screen capture close failed", exc_info=True)

        thread = threading.Thread(target=worker, name="screen-capture", daemon=True)
        thread.start()
        last_preview = [0.0]
        try:
            while True:
                await ready.wait()
                ready.clear()
                data, latest[0] = latest[0], None
                if data:
                    self._send(source, data, last_preview)
        finally:
            self._stop.set()


class _NoProcess(Exception):
    pass


class VideoHub(QObject):
    """Latest decoded frame per streamer, delivered to the UI thread."""

    frame = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._frames: dict[int, QImage] = {}
        self._rates: dict[int, _Rate] = {}
        self.frame.connect(self._noop)

    def _noop(self, user_id: int) -> None:
        pass

    def push(self, user_id: int, video_frame) -> None:
        """Called from the media thread with an RGBA rtc.VideoFrame."""
        width, height = video_frame.width, video_frame.height
        image = QImage(bytes(video_frame.data), width, height, width * 4, QImage.Format_RGBA8888)
        with self._lock:
            self._frames[user_id] = image
            self._rates.setdefault(user_id, _Rate()).tick()
        self.frame.emit(user_id)

    def received(self, user_id: int) -> dict | None:
        """Real size and frames per second of what we receive and decode from this person."""
        with self._lock:
            image = self._frames.get(user_id)
            rate = self._rates.get(user_id)
            if image is None or rate is None:
                return None
            return {"width": image.width(), "height": image.height(), "fps": rate.value()}

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
            self._rates.pop(user_id, None)


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
        self._last_paint = 0.0
        self._repaint_pending = False
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

    MAX_PAINT_FPS = 30

    def _on_frame(self, user_id: int) -> None:
        if user_id == self._user_id:
            had = self._image is not None
            self._image = video_hub().latest(user_id)
            if not had:
                self.hasFrameChanged.emit()
            # Scaling a big frame into the tile is real work on the interface thread, and while it runs the
            # audio callbacks wait. 30 repaints a second look smooth and leave them room.
            now = time.perf_counter()
            if now - self._last_paint >= 1.0 / self.MAX_PAINT_FPS:
                self._last_paint = now
                self.update()
            elif not self._repaint_pending:
                self._repaint_pending = True
                QTimer.singleShot(max(1, int((1.0 / self.MAX_PAINT_FPS - (now - self._last_paint)) * 1000)),
                                  self._deferred_update)

    def _deferred_update(self) -> None:
        self._repaint_pending = False
        self._last_paint = time.perf_counter()
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
