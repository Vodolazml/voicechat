"""One LiveKit connection per call: microphone out, everyone's voice in, screen share on demand.

- `auto_subscribe` is off: voices are subscribed explicitly, a screen stream only after "Смотреть",
  so nobody downloads video they are not looking at (including their own stream).
- Every track is end-to-end encrypted with per-sender keys from KeyManager.
- Microphone frames are pushed the moment the audio engine produces them (no polling loop).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from .dsp import FRAME_SAMPLES, SAMPLE_RATE

logger = logging.getLogger("voicechat.media")

MAX_MIC_BACKLOG = 4  # 40 ms: older frames are dropped instead of adding delay


def key_index(key_id: str) -> int:
    """Both sides derive the key slot from the key id, so a rotated key never collides with the old one."""
    try:
        return int(key_id[:4], 16) % 16
    except ValueError:
        return 0


def user_id_from_identity(identity: str) -> int | None:
    raw = identity[1:] if identity.startswith("u") else identity.removesuffix("-audio")
    return int(raw) if raw.isdigit() else None


@dataclass
class MediaCallbacks:
    on_state: Callable[[str], None] = lambda state: None                  # connecting/connected/reconnecting/closed
    on_speakers: Callable[[set[int]], None] = lambda speakers: None
    on_quality: Callable[[int, int], None] = lambda user_id, quality: None  # 0 lost .. 3 excellent
    on_streams: Callable[[set[int]], None] = lambda users: None            # who is sharing a screen now
    on_video_frame: Callable[[int, Any], None] = lambda user_id, frame: None
    on_participants: Callable[[set[int]], None] = lambda users: None


class MediaSession:
    def __init__(self, *, credentials: Callable[[], dict], my_id: int,
                 outgoing_key: Callable[[], tuple[str, bytes]],
                 incoming_key: Callable[[int], tuple[str, bytes] | None],
                 mixer, callbacks: MediaCallbacks) -> None:
        self.credentials = credentials
        self.my_id = my_id
        self.outgoing_key = outgoing_key
        self.incoming_key = incoming_key
        self.mixer = mixer
        self.cb = callbacks
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop: asyncio.Event | None = None
        self._ready = threading.Event()
        self._room = None
        self._mic_queue: asyncio.Queue[bytes] | None = None
        self._mic_track = None
        self._applied_keys: dict[str, str] = {}
        self._tasks: set[asyncio.Task] = set()
        self._watching: set[int] = set()
        self._streams: set[int] = set()
        self._screen = None
        self.muted = False
        self.connected = False

    # --- thread plumbing ------------------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="media", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def stop(self, timeout: float = 8.0) -> None:
        loop = self._loop
        if loop and self._stop:
            loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._stop = asyncio.Event()
        self._mic_queue = asyncio.Queue()
        self._ready.set()
        try:
            loop.run_until_complete(self._main())
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self._loop = None

    def _call(self, func, *args) -> None:
        loop = self._loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(func, *args)

    # --- public API (any thread) ----------------------------------------------------------------

    def push_mic_frame(self, frame: bytes) -> None:
        """Called from the audio callback thread for every 10 ms frame that should be sent."""
        self._call(self._enqueue_mic, frame)

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self._call(self._apply_mute)

    def refresh_keys(self) -> None:
        self._call(self._apply_keys)

    def watch(self, user_id: int, watching: bool) -> None:
        if watching:
            self._watching.add(user_id)
        else:
            self._watching.discard(user_id)
        self._call(self._apply_subscriptions)

    def start_screen(self, capturer) -> None:
        """capturer: object with async `run(source)` that feeds rtc.VideoSource until cancelled."""
        loop = self._loop
        if loop:
            asyncio.run_coroutine_threadsafe(self._start_screen(capturer), loop)

    def stop_screen(self) -> None:
        loop = self._loop
        if loop:
            asyncio.run_coroutine_threadsafe(self._stop_screen(), loop)

    # --- event loop side ------------------------------------------------------------------------

    def _enqueue_mic(self, frame: bytes) -> None:
        queue = self._mic_queue
        if queue is None:
            return
        while queue.qsize() >= MAX_MIC_BACKLOG:
            queue.get_nowait()
        queue.put_nowait(frame)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _main(self) -> None:
        from livekit import rtc

        backoff = 1.0
        while not self._stop.is_set():
            self.cb.on_state("connecting")
            room = rtc.Room()
            self._room = room
            self._applied_keys.clear()
            self._wire(room)
            try:
                creds = await asyncio.to_thread(self.credentials)
                if self._stop.is_set():
                    break
                key_id, key = self.outgoing_key()
                options = rtc.RoomOptions(auto_subscribe=False, dynacast=True, connect_timeout=10,
                                          encryption=rtc.E2EEOptions())
                await room.connect(creds["url"], creds["token"], options=options)
                identity = room.local_participant.identity
                room.e2ee_manager.key_provider.set_key(identity, key, key_index(key_id))
                self._applied_keys[identity] = key_id
                await self._publish_mic(room)
                self._use_local_key_index(key_index(key_id))
                self._apply_keys()
                self._apply_subscriptions()
                self.connected = True
                self.cb.on_state("connected")
                self._report_participants()
                backoff = 1.0
                pump = asyncio.create_task(self._pump_mic())
                closed = asyncio.create_task(self._wait_closed(room))
                stop = asyncio.create_task(self._stop.wait())
                await asyncio.wait({closed, stop}, return_when=asyncio.FIRST_COMPLETED)
                for task in (pump, closed, stop):
                    task.cancel()
            except Exception as exc:
                logger.warning("Media connection failed: %s", exc)
            finally:
                self.connected = False
                await self._teardown(room)
            if self._stop.is_set():
                break
            self.cb.on_state("reconnecting")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 10.0)
        self.cb.on_state("closed")

    async def _wait_closed(self, room) -> None:
        while room.isconnected():
            await asyncio.sleep(0.5)

    async def _teardown(self, room) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._screen is not None:
            await self._stop_screen()
        self._mic_track = None
        try:
            await room.disconnect()
        except Exception:
            pass
        self._room = None
        self.mixer.clear()
        self._streams.clear()
        self.cb.on_streams(set())
        self.cb.on_speakers(set())

    async def _publish_mic(self, room) -> None:
        from livekit import rtc

        self._source = rtc.AudioSource(SAMPLE_RATE, 1, queue_size_ms=60)
        track = rtc.LocalAudioTrack.create_audio_track("microphone", self._source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE, dtx=True, red=True)
        await room.local_participant.publish_track(track, options)
        self._mic_track = track
        self._apply_mute()

    async def _pump_mic(self) -> None:
        from livekit import rtc

        while True:
            frame = await self._mic_queue.get()
            if self.muted:
                continue
            try:
                await self._source.capture_frame(rtc.AudioFrame(frame, SAMPLE_RATE, 1, FRAME_SAMPLES))
            except Exception:
                logger.debug("Mic frame dropped", exc_info=True)

    def _apply_mute(self) -> None:
        track = self._mic_track
        if track is None:
            return
        if self.muted:
            track.mute()
            while self._mic_queue and not self._mic_queue.empty():
                self._mic_queue.get_nowait()
        else:
            track.unmute()

    def _apply_keys(self) -> None:
        room = self._room
        if room is None or not room.isconnected():
            return
        provider = room.e2ee_manager.key_provider
        local = room.local_participant.identity
        key_id, key = self.outgoing_key()
        if self._applied_keys.get(local) != key_id:
            # Rotation: install the new key now, start encrypting with it a moment later so friends
            # have fetched it by then (the old key stays in their key ring meanwhile).
            provider.set_key(local, key, key_index(key_id))
            self._applied_keys[local] = key_id
            asyncio.get_running_loop().call_later(1.5, self._use_local_key_index, key_index(key_id))
        for participant in room.remote_participants.values():
            user_id = user_id_from_identity(participant.identity)
            found = self.incoming_key(user_id) if user_id is not None else None
            if found and self._applied_keys.get(participant.identity) != found[0]:
                provider.set_key(participant.identity, found[1], key_index(found[0]))
                self._applied_keys[participant.identity] = found[0]

    def _use_local_key_index(self, index: int) -> None:
        room = self._room
        if room is None or not room.isconnected():
            return
        local = room.local_participant.identity
        for cryptor in room.e2ee_manager.frame_cryptors():
            if cryptor.participant_identity == local and cryptor.key_index != index:
                cryptor.set_key_index(index)

    def _apply_subscriptions(self) -> None:
        from livekit import rtc

        room = self._room
        if room is None:
            return
        for participant in room.remote_participants.values():
            user_id = user_id_from_identity(participant.identity)
            for publication in participant.track_publications.values():
                if publication.kind == rtc.TrackKind.KIND_AUDIO:
                    wanted = publication.source == rtc.TrackSource.SOURCE_MICROPHONE or user_id in self._watching
                else:
                    wanted = user_id in self._watching
                if publication.subscribed != wanted:
                    publication.set_subscribed(wanted)

    def _report_streams(self) -> None:
        from livekit import rtc

        room = self._room
        streams = set()
        if room is not None:
            for participant in room.remote_participants.values():
                user_id = user_id_from_identity(participant.identity)
                if user_id is None:
                    continue
                if any(p.source == rtc.TrackSource.SOURCE_SCREENSHARE for p in participant.track_publications.values()):
                    streams.add(user_id)
        if self._screen is not None:
            streams.add(self.my_id)
        if streams != self._streams:
            self._streams = streams
            self.cb.on_streams(set(streams))

    def _report_participants(self) -> None:
        room = self._room
        if room is None:
            return
        users = {uid for p in room.remote_participants.values()
                 if (uid := user_id_from_identity(p.identity)) is not None}
        self.cb.on_participants(users)

    def _wire(self, room) -> None:
        from livekit import rtc

        @room.on("participant_connected")
        def _joined(participant):
            self._apply_keys()
            self._report_participants()

        @room.on("participant_disconnected")
        def _left(participant):
            user_id = user_id_from_identity(participant.identity)
            if user_id is not None:
                self.mixer.remove(user_id)
            self._applied_keys.pop(participant.identity, None)
            self._report_participants()
            self._report_streams()

        @room.on("track_published")
        def _published(publication, participant):
            self._apply_subscriptions()
            self._report_streams()

        @room.on("track_unpublished")
        def _unpublished(publication, participant):
            self._report_streams()

        @room.on("track_subscribed")
        def _subscribed(track, publication, participant):
            user_id = user_id_from_identity(participant.identity)
            if user_id is None:
                return
            if track.kind == rtc.TrackKind.KIND_AUDIO and publication.source == rtc.TrackSource.SOURCE_MICROPHONE:
                self._spawn(self._receive_audio(track, user_id))
            elif track.kind == rtc.TrackKind.KIND_VIDEO:
                self._spawn(self._receive_video(track, user_id))

        @room.on("active_speakers_changed")
        def _speakers(speakers):
            ids = set()
            for participant in speakers:
                user_id = user_id_from_identity(participant.identity)
                if user_id is not None:
                    ids.add(user_id)
            self.cb.on_speakers(ids)

        @room.on("connection_quality_changed")
        def _quality(participant, quality):
            user_id = user_id_from_identity(participant.identity)
            if user_id is not None:
                self.cb.on_quality(user_id, int(quality))

        @room.on("reconnecting")
        def _reconnecting():
            self.cb.on_state("reconnecting")

        @room.on("reconnected")
        def _reconnected():
            self.cb.on_state("connected")

    async def _receive_audio(self, track, user_id: int) -> None:
        from livekit import rtc

        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1, frame_size_ms=10, capacity=8)
        try:
            async for event in stream:
                self.mixer.put(user_id, bytes(event.frame.data.cast("B")))
        finally:
            await stream.aclose()
            self.mixer.remove(user_id)

    async def _receive_video(self, track, user_id: int) -> None:
        from livekit import rtc

        stream = rtc.VideoStream(track, capacity=2, format=rtc.VideoBufferType.RGBA)
        try:
            async for event in stream:
                self.cb.on_video_frame(user_id, event.frame)
        finally:
            await stream.aclose()

    async def _start_screen(self, capturer) -> None:
        from livekit import rtc

        room = self._room
        if room is None or not room.isconnected():
            raise RuntimeError("not connected")
        if self._screen is not None:
            await self._stop_screen()
        source = rtc.VideoSource(capturer.width, capturer.height, is_screencast=True)
        track = rtc.LocalVideoTrack.create_video_track("screen", source)
        options = rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_SCREENSHARE,
            video_encoding=rtc.VideoEncoding(max_bitrate=capturer.max_bitrate, max_framerate=capturer.fps),
            simulcast=False)
        publication = await room.local_participant.publish_track(track, options)
        self._use_local_key_index(key_index(self.outgoing_key()[0]))
        task = asyncio.create_task(capturer.run(source))
        self._screen = (publication, task, capturer)
        self._report_streams()

    async def _stop_screen(self) -> None:
        if self._screen is None:
            return
        publication, task, capturer = self._screen
        self._screen = None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        room = self._room
        if room is not None and room.isconnected():
            try:
                await room.local_participant.unpublish_track(publication.sid)
            except Exception:
                pass
        self._report_streams()
