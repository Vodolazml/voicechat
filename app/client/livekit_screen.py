from __future__ import annotations

import asyncio
import threading
from collections import deque
from time import monotonic, perf_counter

import mss
from livekit import rtc
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage


class LiveKitScreenClient(QObject):
    """WebRTC video on a dedicated loop; only the latest frame reaches Qt."""

    status_changed = Signal(str)
    stopped_received = Signal(int)

    def __init__(self, credentials, user_id, outgoing_key, key_for_sender):
        super().__init__()
        self.credentials = credentials
        self.user_id = user_id
        self.outgoing_key = outgoing_key
        self.key_for_sender = key_for_sender
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._frames = {}
        self._frame_times = {}
        self._share_spec = None
        self._viewer_interval = 1 / 60
        self._viewer_quality = "source"
        self._thread = None
        self._capture_stop = threading.Event()
        self._loop = None
        self._room = None
        self.connected = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="webrtc-video", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._capture_stop.set()
        if self._loop:
            self._loop.call_soon_threadsafe(lambda: None)
        if self._thread:
            self._thread.join(timeout=15)
            if self._thread.is_alive():
                self.status_changed.emit("Видео: завершение соединения задерживается")
        with self._lock:
            self._frames.clear()

    def start_share(self, width, height, fps):
        self._share_spec = (width, height, fps)

    def send_frame(self, frame):
        if frame == b"__STOP__":
            self._share_spec = None
            self._capture_stop.set()

    def send_viewer_settings(self, fps_interval_ms, quality_key):
        self._viewer_interval = 1 / 60 if fps_interval_ms == 17 else fps_interval_ms / 1000
        self._viewer_quality = quality_key

    def take_frames(self):
        with self._lock:
            result, self._frames = self._frames, {}
        return result

    def _put_frame(self, user_id, image):
        now = monotonic()
        with self._lock:
            times = self._frame_times.setdefault(user_id, deque(maxlen=120))
            times.append(now)
            fps = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 and times[-1] > times[0] else 0
            self._frames[user_id] = (image, fps)

    def _run(self):
        asyncio.run(self._main())

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        try:
            while not self._stop.is_set():
                try:
                    await self._session()
                except Exception as exc:
                    if not self._stop.is_set():
                        self.status_changed.emit(f"Видео: восстановление соединения ({type(exc).__name__})")
                for _ in range(20):
                    if self._stop.is_set():
                        break
                    await asyncio.sleep(0.1)
        finally:
            self._loop = None

    async def _session(self):
        room = self._room = rtc.Room()
        tasks = set()
        installed_keys = {}

        @room.on("track_subscribed")
        def subscribed(track, publication, participant):
            if track.kind != rtc.TrackKind.KIND_VIDEO:
                return
            task = asyncio.create_task(self._receive(track, int(participant.identity.split('-')[0])))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        @room.on("track_unsubscribed")
        def unsubscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_VIDEO:
                self.stopped_received.emit(int(participant.identity.split('-')[0]))

        try:
            credentials = await asyncio.to_thread(self.credentials)
            await room.connect(credentials["url"], credentials["token"],
                               options=rtc.RoomOptions(encryption=rtc.E2EEOptions(), connect_timeout=8))
            self.connected = True
            room.e2ee_manager.key_provider.set_key(str(self.user_id), self.outgoing_key, 0)
            self.status_changed.emit("Видео: подключено, WebRTC / E2EE")
            current = None
            publication = None
            capture_task = None
            source = None
            try:
                while not self._stop.is_set() and room.isconnected():
                    for participant in room.remote_participants.values():
                        key = self.key_for_sender(int(participant.identity.split('-')[0]))
                        if key and installed_keys.get(participant.identity) != key:
                            room.e2ee_manager.key_provider.set_key(participant.identity, key, 0)
                            installed_keys[participant.identity] = key
                        for pub in participant.track_publications.values():
                            if pub.kind == rtc.TrackKind.KIND_VIDEO and pub.simulcasted:
                                quality = (rtc.VideoQuality.VIDEO_QUALITY_LOW if self._viewer_quality == "540p"
                                           else rtc.VideoQuality.VIDEO_QUALITY_MEDIUM if self._viewer_quality == "720p"
                                           else rtc.VideoQuality.VIDEO_QUALITY_HIGH)
                                pub.set_video_quality(quality)
                    desired = self._share_spec
                    if desired != current:
                        self._capture_stop.set()
                        if capture_task:
                            await capture_task
                            capture_task = None
                        if publication:
                            await room.local_participant.unpublish_track(publication.sid)
                            publication = None
                        if source:
                            await source.aclose()
                            source = None
                        current = desired
                        if desired:
                            width, height, fps = desired
                            source = rtc.VideoSource(width, height, is_screencast=True)
                            track = rtc.LocalVideoTrack.create_video_track("Экран", source)
                            options = rtc.TrackPublishOptions(
                                source=rtc.TrackSource.SOURCE_SCREENSHARE,
                                video_codec=rtc.VideoCodec.H264,
                                simulcast=True,
                                video_encoding=rtc.VideoEncoding(max_framerate=fps,
                                    max_bitrate=40_000_000 if width > 1920 else 12_000_000),
                            )
                            publication = await room.local_participant.publish_track(track, options)
                            self._capture_stop.clear()
                            capture_task = asyncio.create_task(asyncio.to_thread(self._capture, source, desired))
                    if capture_task and capture_task.done():
                        capture_task.result()
                    await asyncio.sleep(0.1)
            finally:
                self._capture_stop.set()
                if capture_task:
                    await asyncio.gather(capture_task, return_exceptions=True)
                if source:
                    await source.aclose()
        finally:
            self.connected = False
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await room.disconnect()
            self._room = None

    def _capture(self, source, spec):
        width, height, fps = spec
        period = 1 / fps
        deadline = perf_counter()
        preview_at = 0.0
        with mss.mss(with_cursor=True) as capture:
            monitor = capture.monitors[1]
            while not self._capture_stop.is_set() and not self._stop.is_set():
                shot = capture.grab(monitor)
                image = QImage(shot.bgra, shot.width, shot.height, QImage.Format.Format_RGB32).copy()
                self._draw_cursor(image, monitor)
                if image.width() > width or image.height() > height:
                    from PySide6.QtCore import Qt
                    image = image.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                frame = rtc.VideoFrame(image.width(), image.height(), rtc.VideoBufferType.BGRA,
                                       bytes(image.constBits()))
                source.capture_frame(frame, timestamp_us=int(perf_counter() * 1_000_000))
                now = perf_counter()
                if now - preview_at >= 0.1:
                    self._put_frame(self.user_id, image)
                    preview_at = now
                deadline = max(deadline + period, now)
                self._capture_stop.wait(max(0, deadline - perf_counter()))

    @staticmethod
    def _draw_cursor(image, monitor):
        import sys
        if sys.platform != 'win32':
            return
        import ctypes
        from ctypes import wintypes
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QPainter, QColor, QPolygon, QPen
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return
        x, y = point.x - monitor['left'], point.y - monitor['top']
        painter = QPainter(image)
        painter.setPen(QPen(QColor('black'), 1))
        painter.setBrush(QColor('white'))
        painter.drawPolygon(QPolygon([QPoint(x, y), QPoint(x, y + 20), QPoint(x + 5, y + 15),
                                     QPoint(x + 9, y + 23), QPoint(x + 12, y + 21),
                                     QPoint(x + 8, y + 13), QPoint(x + 15, y + 13)]))
        painter.end()

    async def _receive(self, track, user_id):
        stream = rtc.VideoStream(track, capacity=1, format=rtc.VideoBufferType.BGRA)
        last = 0.0
        try:
            async for event in stream:
                if self._stop.is_set():
                    break
                now = monotonic()
                if now - last + 0.001 < self._viewer_interval:
                    continue
                last = now
                frame = event.frame
                image = QImage(bytes(frame.data), frame.width, frame.height,
                               QImage.Format.Format_RGB32).copy()
                self._put_frame(user_id, image)
        finally:
            await stream.aclose()
