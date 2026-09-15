from __future__ import annotations

import asyncio
import threading
import queue
from time import monotonic

from livekit import rtc

from .voice_audio import VoiceAudioClient, SAMPLE_RATE, BLOCKSIZE, FRAME_BYTES


class LiveKitVoiceClient(VoiceAudioClient):
    """PortAudio devices with native WebRTC/Opus transport and per-user output mixing."""

    def __init__(self, *, media_credentials, **kwargs):
        super().__init__(**kwargs)
        self.media_credentials = media_credentials
        self._room = None
        self._apm_lock = threading.Lock()
        self._noise_enabled = bool(self.noise_suppression())
        self._apm = self._make_processor()

    def _make_processor(self):
        return rtc.AudioProcessingModule(echo_cancellation=True,
            noise_suppression=self._noise_enabled, high_pass_filter=True)

    def _capture_callback(self, indata, frames, time_info, status):
        try:
            with self._apm_lock:
                enabled = bool(self.noise_suppression())
                if enabled != self._noise_enabled:
                    self._noise_enabled = enabled
                    self._apm = self._make_processor()
                latency = sum(getattr(stream, 'latency', 0) for stream in
                              (self._input_stream, self._output_stream) if stream)
                self._apm.set_stream_delay_ms(min(500, max(0, int(latency * 1000))))
                processed = bytearray()
                data = bytes(indata)
                for offset in range(0, len(data), FRAME_BYTES // 2):
                    part = rtc.AudioFrame(data[offset:offset + FRAME_BYTES // 2], SAMPLE_RATE, 1, BLOCKSIZE // 2)
                    self._apm.process_stream(part)
                    processed.extend(part.data.cast('B'))
            super()._capture_callback(processed, frames, time_info, status)
        except Exception as exc:
            self._status(f"Ошибка обработки микрофона: {type(exc).__name__}")
            self.speaking = False

    def _playback_callback(self, outdata, frames, time_info, status):
        super()._playback_callback(outdata, frames, time_info, status)
        try:
            with self._apm_lock:
                data = bytes(outdata)
                for offset in range(0, len(data), FRAME_BYTES // 2):
                    self._apm.process_reverse_stream(rtc.AudioFrame(
                        data[offset:offset + FRAME_BYTES // 2], SAMPLE_RATE, 1, BLOCKSIZE // 2))
        except Exception as exc:
            self._status(f"Ошибка компенсации эха: {type(exc).__name__}")

    async def _close_websocket_async(self):
        if self._room:
            await self._room.disconnect()

    async def _socket_loop(self):
        while not self._stop.is_set():
            room = self._room = rtc.Room()
            tasks = set()
            keys = {}
            source = None

            @room.on('track_subscribed')
            def subscribed(track, publication, participant):
                if track.kind == rtc.TrackKind.KIND_AUDIO:
                    task = asyncio.create_task(self._receive_audio(track, int(participant.identity.split('-')[0])))
                    tasks.add(task)

            try:
                credentials = await asyncio.to_thread(self.media_credentials)
                if self._stop.is_set():
                    break
                await room.connect(credentials['url'], credentials['token'],
                                   options=rtc.RoomOptions(encryption=rtc.E2EEOptions(), connect_timeout=5))
                identity = f'{self.user_id}-audio'
                room.e2ee_manager.key_provider.set_key(identity, self.outgoing_media_key, 0)
                source = rtc.AudioSource(SAMPLE_RATE, 1, queue_size_ms=40)
                track = rtc.LocalAudioTrack.create_audio_track('Микрофон', source)
                await room.local_participant.publish_track(track, rtc.TrackPublishOptions(
                    source=rtc.TrackSource.SOURCE_MICROPHONE, dtx=True, red=True))
                self._drain(self.capture_queue)
                self._status('Голос подключён · WebRTC / Opus / E2EE')
                next_device_check = monotonic() + 2
                while not self._stop.is_set() and room.isconnected():
                    if monotonic() >= next_device_check:
                        next_device_check = monotonic() + 2
                        if self._devices_started and (self._input_stream is None or self._output_stream is None
                                or not self._input_stream.active or not self._output_stream.active):
                            try:
                                await asyncio.to_thread(self.restart_devices, self.input_device, self.output_device)
                            except Exception:
                                self._status('Аудиоустройство недоступно. Подключите гарнитуру или выберите другое устройство.')
                    for participant in room.remote_participants.values():
                        user_id = int(participant.identity.split('-')[0])
                        key = self.media_key_for_sender(user_id)
                        if key and keys.get(participant.identity) != key:
                            room.e2ee_manager.key_provider.set_key(participant.identity, key, 0)
                            keys[participant.identity] = key
                    for task in list(tasks):
                        if task.done():
                            tasks.remove(task)
                            task.result()
                    if self.is_muted() or self.is_deafened():
                        self._drain(self.capture_queue)
                        source.clear_queue()
                    else:
                        try:
                            frame = self.capture_queue.get_nowait()
                        except queue.Empty:
                            frame = None
                        if frame is not None:
                            await source.capture_frame(rtc.AudioFrame(frame, SAMPLE_RATE, 1, BLOCKSIZE))
                    await asyncio.sleep(0.002)
            except Exception as exc:
                if not self._stop.is_set():
                    self._status(f'Голос: восстановление ({type(exc).__name__})')
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if source:
                    await source.aclose()
                await room.disconnect()
                self._room = None
                self.mixer.clear()
            for _ in range(10):
                if self._stop.is_set():
                    break
                await asyncio.sleep(0.1)

    async def _receive_audio(self, track, user_id):
        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1,
                                 frame_size_ms=20, capacity=6)
        try:
            async for event in stream:
                if self._stop.is_set():
                    break
                if not self.is_deafened() and user_id != self.user_id:
                    self.mixer.put(user_id, bytes(event.frame.data))
        finally:
            await stream.aclose()
