from __future__ import annotations

import asyncio
import audioop
import queue
import re
import threading
from collections import deque
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from time import monotonic
from collections.abc import Callable

import sounddevice as sd
import websockets

from app.media_crypto import decrypt_frame, encrypt_frame


SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2
BLOCKSIZE = 320
FRAME_BYTES = BLOCKSIZE * CHANNELS * SAMPLE_WIDTH
SPEAKING_RMS = 450
SPEAKING_HOLD_SECONDS = 0.8
VOICE_PREROLL_FRAMES = 8
MIC_TEST_DELAY_FRAMES = 18
RECONNECT_DELAY_SECONDS = 0.75


def make_voice_aad(channel_id: int, sender_id: int, recipient_id: int) -> bytes:
    """Создаёт AAD, привязанный к channel_id, sender_id и recipient_id.
    
    Это предотвращает пересылку зашифрованных кадров между разными каналами
    или между разными парами отправитель/получатель.
    """
    return f"private-voicechat:voice:v2:{channel_id}:{sender_id}:{recipient_id}".encode()


@dataclass(frozen=True)
class AudioDevice:
    id: int
    name: str
    inputs: int
    outputs: int
    simple_name: str
    advanced: bool


ADVANCED_MARKERS = (
    "переназначение",
    "первичный драйвер",
    "первичный звуковой",
    "primary sound",
    "stereo mix",
    "стерео микшер",
    "mapper",
    "output (",
    "line input",
    "лин. вход",
)


def simplify_device_name(name: str) -> str:
    result = name.strip()
    for suffix in (
        " - Input",
        " - Output",
        " (input)",
        " (output)",
        " input",
        " output",
    ):
        result = result.replace(suffix, "")
    while "  " in result:
        result = result.replace("  ", " ")
    result = result.strip(" -")
    if result.count("(") > result.count(")"):
        result += ")"
    return result


def device_key(name: str) -> str:
    simplified = simplify_device_name(name).lower().replace("ё", "е")
    simplified = re.sub(r"\b(input|output|audio|вход|выход)\b", "", simplified)
    simplified = re.sub(r"[^0-9a-zа-я]+", "", simplified)
    return simplified


def add_unique_device(devices: list[AudioDevice], index_by_key: dict[str, int], key: str, device: AudioDevice) -> None:
    existing_index = index_by_key.get(key)
    if existing_index is None:
        index_by_key[key] = len(devices)
        devices.append(device)
        return
    existing = devices[existing_index]
    if len(device.simple_name) > len(existing.simple_name):
        devices[existing_index] = device


def is_advanced_device(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in ADVANCED_MARKERS)


def audio_devices(*, include_advanced: bool = False) -> tuple[list[AudioDevice], list[AudioDevice]]:
    inputs: list[AudioDevice] = []
    outputs: list[AudioDevice] = []
    seen_inputs: dict[str, int] = {}
    seen_outputs: dict[str, int] = {}
    for index, raw in enumerate(sd.query_devices()):
        name = str(raw["name"])
        simple_name = simplify_device_name(name)
        advanced = is_advanced_device(name)
        device = AudioDevice(
            id=index,
            name=name,
            inputs=int(raw["max_input_channels"]),
            outputs=int(raw["max_output_channels"]),
            simple_name=simple_name,
            advanced=advanced,
        )
        input_key = device_key(simple_name)
        output_key = device_key(simple_name)
        if device.inputs > 0 and (include_advanced or not advanced):
            add_unique_device(inputs, seen_inputs, input_key, device)
        if device.outputs > 0 and (include_advanced or not advanced):
            add_unique_device(outputs, seen_outputs, output_key, device)
    return inputs, outputs


def device_display_name(device_id: int) -> str:
    try:
        raw = sd.query_devices(device_id)
    except Exception:
        return f"устройство {device_id}"
    return simplify_device_name(str(raw["name"]))


class MicTestMonitor:
    def __init__(
        self,
        input_device: int | None = None,
        output_device: int | None = None,
        threshold: int = SPEAKING_RMS,
        playback: bool = True,
    ) -> None:
        self.input_device = input_device
        self.output_device = output_device
        self.threshold = threshold
        self.playback = playback
        self.level = 0
        self.speaking = False
        self._last_voice_at = 0.0
        self._delay_queue: queue.Queue[bytes] = queue.Queue(maxsize=MIC_TEST_DELAY_FRAMES * 3)
        self._stream: sd.RawInputStream | None = None
        self._output_stream: sd.RawOutputStream | None = None

    def start(self) -> None:
        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=BLOCKSIZE,
            device=self.input_device,
            callback=self._callback,
        )
        if self.playback:
            self._output_stream = sd.RawOutputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=BLOCKSIZE,
                device=self.output_device,
                callback=self._playback_callback,
            )
        self._stream.start()
        if self._output_stream:
            self._output_stream.start()

    def stop(self) -> None:
        for stream in (self._stream, self._output_stream):
            if not stream:
                continue
            try:
                stream.stop()
                stream.close()
            except sd.PortAudioError:
                pass
        self._stream = None
        self._output_stream = None

    def _callback(self, indata, frames, time_info, status) -> None:
        data = bytes(indata)
        rms = audioop.rms(data, SAMPLE_WIDTH) if data else 0
        self.level = max(0, min(100, int(rms / 30)))
        now = monotonic()
        if rms >= self.threshold:
            self._last_voice_at = now
            self.speaking = True
        elif now - self._last_voice_at > SPEAKING_HOLD_SECONDS:
            self.speaking = False
        if not self.playback:
            return
        try:
            self._delay_queue.put_nowait(data)
        except queue.Full:
            try:
                self._delay_queue.get_nowait()
                self._delay_queue.put_nowait(data)
            except queue.Empty:
                pass

    def set_threshold(self, threshold: int) -> None:
        self.threshold = threshold

    def _playback_callback(self, outdata, frames, time_info, status) -> None:
        if self._delay_queue.qsize() < MIC_TEST_DELAY_FRAMES:
            outdata[:] = b"\x00" * len(outdata)
            return
        try:
            data = self._delay_queue.get_nowait()
        except queue.Empty:
            data = b"\x00" * len(outdata)
        if len(data) < len(outdata):
            data += b"\x00" * (len(outdata) - len(data))
        outdata[:] = data[: len(outdata)]


class VoiceAudioClient:
    def __init__(
        self,
        *,
        ws_url: str,
        ws_headers: dict[str, str],
        channel_id: int,
        user_id: int,
        outgoing_media_key: bytes,
        media_key_for_sender: Callable[[int], bytes | None],
        is_muted: Callable[[], bool],
        is_deafened: Callable[[], bool],
        is_locally_muted: Callable[[int], bool],
        local_volume: Callable[[int], int],
        noise_suppression: Callable[[], bool],
        noise_threshold: Callable[[], int],
        status_callback: Callable[[str], None] | None = None,
        input_device: int | None = None,
        output_device: int | None = None,
    ) -> None:
        self.ws_url = ws_url
        self.ws_headers = ws_headers
        self.channel_id = channel_id
        self.user_id = user_id
        self.outgoing_media_key = outgoing_media_key
        self.media_key_for_sender = media_key_for_sender
        self.is_muted = is_muted
        self.is_deafened = is_deafened
        self.is_locally_muted = is_locally_muted
        self.local_volume = local_volume
        self.noise_suppression = noise_suppression
        self.noise_threshold = noise_threshold
        self.status_callback = status_callback
        self.input_device = input_device
        self.output_device = output_device
        self.capture_queue: queue.Queue[bytes] = queue.Queue(maxsize=40)
        self.playback_queue: queue.Queue[bytes] = queue.Queue(maxsize=80)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._websocket = None
        self._input_stream: sd.RawInputStream | None = None
        self._output_stream: sd.RawOutputStream | None = None
        self._stream_lock = threading.RLock()
        self.speaking = False
        self._last_voice_at = 0.0
        self._preroll_frames: deque[bytes] = deque(maxlen=VOICE_PREROLL_FRAMES)
        self._audible_users: set[int] = set()
        self._key_problem_users: set[int] = set()
        self._audible_lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._reset_capture_state()
        self._drain(self.capture_queue)
        self._drain(self.playback_queue)
        self._input_stream, self._output_stream = self._open_streams(self.input_device, self.output_device)
        self._thread = threading.Thread(target=self._run_loop, name="voice-audio", daemon=True)
        self._thread.start()
        self._status("audio connected")

    def _open_streams(
        self,
        input_device: int | None,
        output_device: int | None,
    ) -> tuple[sd.RawInputStream, sd.RawOutputStream]:
        input_stream: sd.RawInputStream | None = None
        output_stream: sd.RawOutputStream | None = None
        try:
            input_stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=BLOCKSIZE,
                device=input_device,
                callback=self._capture_callback,
            )
            output_stream = sd.RawOutputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=BLOCKSIZE,
                device=output_device,
                callback=self._playback_callback,
            )
            input_stream.start()
            output_stream.start()
            return input_stream, output_stream
        except Exception:
            self._close_streams(input_stream, output_stream)
            raise

    def restart_devices(self, input_device: int | None, output_device: int | None) -> None:
        with self._stream_lock:
            new_input, new_output = self._open_streams(input_device, output_device)
            old_input, old_output = self._input_stream, self._output_stream
            self._input_stream, self._output_stream = new_input, new_output
            self.input_device = input_device
            self.output_device = output_device
        self._close_streams(old_input, old_output)
        self._status("audio devices changed")

    def _close_streams(self, *streams) -> None:
        for stream in streams:
            if not stream:
                continue
            try:
                stream.stop()
                stream.close()
            except sd.PortAudioError:
                pass

    def stop(self) -> None:
        self._stop.set()
        self._close_active_websocket()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        with self._stream_lock:
            input_stream, output_stream = self._input_stream, self._output_stream
            self._input_stream = None
            self._output_stream = None
        self._close_streams(input_stream, output_stream)
        self._drain(self.capture_queue)
        self._drain(self.playback_queue)
        self.consume_audible_users()
        self._reset_capture_state()
        self._status("audio disconnected")

    def _capture_callback(self, indata, frames, time_info, status) -> None:
        if self._stop.is_set() or self.is_muted():
            self.speaking = False
            self._preroll_frames.clear()
            return
        data = bytes(indata)
        if not data:
            return
        now = monotonic()
        noise_enabled = self.noise_suppression()
        rms = audioop.rms(data, SAMPLE_WIDTH)
        voice_detected = rms >= self.noise_threshold()
        was_speaking = self.speaking

        if voice_detected:
            self._last_voice_at = now
            self.speaking = True
        elif now - self._last_voice_at > SPEAKING_HOLD_SECONDS:
            self.speaking = False

        if not noise_enabled:
            self._preroll_frames.clear()
            self._put_latest(self.capture_queue, data)
            return

        self._preroll_frames.append(data)
        if not self.speaking:
            return

        if voice_detected and not was_speaking:
            for frame in tuple(self._preroll_frames):
                self._put_latest(self.capture_queue, frame)
            self._preroll_frames.clear()
            return

        self._put_latest(self.capture_queue, data)

    def _playback_callback(self, outdata, frames, time_info, status) -> None:
        if self._stop.is_set() or self.is_deafened():
            outdata[:] = b"\x00" * len(outdata)
            return
        try:
            data = self.playback_queue.get_nowait()
        except queue.Empty:
            data = b"\x00" * len(outdata)
        if len(data) < len(outdata):
            data += b"\x00" * (len(outdata) - len(data))
        outdata[:] = data[: len(outdata)]

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self._socket_loop())
        finally:
            self._loop = None
            loop.close()

    async def _socket_loop(self) -> None:
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.ws_url, max_size=8192, additional_headers=self.ws_headers) as websocket:
                    self._websocket = websocket
                    self._status("audio connected")
                    sender = asyncio.create_task(self._send_loop(websocket))
                    receiver = asyncio.create_task(self._receive_loop(websocket))
                    try:
                        done, pending = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        for task in done:
                            task.result()
                    finally:
                        if self._websocket is websocket:
                            self._websocket = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stop.is_set():
                    break
                self._status(f"audio reconnecting: {exc}")
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _send_loop(self, websocket) -> None:
        while not self._stop.is_set():
            try:
                frame = self.capture_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.004)
                continue
            # AAD привязан к channel_id и user_id для защиты от пересылки между каналами
            aad = make_voice_aad(self.channel_id, self.user_id, 0)
            encrypted = encrypt_frame(self.outgoing_media_key, frame, aad)
            # Добавляем sender_id в начало пакета
            await websocket.send(encrypted)

    async def _receive_loop(self, websocket) -> None:
        async for message in websocket:
            if self._stop.is_set():
                break
            if not isinstance(message, bytes) or len(message) <= 4:
                continue
            sender_id = int.from_bytes(message[:4], "big")
            # Проверяем что пакет не от нас самих
            if sender_id == self.user_id:
                continue
            
            # Извлекаем зашифрованную часть (пропускаем 4 байта sender_id)
            encrypted_data = message[4:]
            
            if len(encrypted_data) <= len(b"PVCM1") + 12:
                continue
            
            if self.is_deafened() or self.is_locally_muted(sender_id):
                continue
            media_key = self.media_key_for_sender(sender_id)
            if not media_key:
                self._mark_key_problem(sender_id)
                continue
            # AAD привязан к channel_id, sender_id и recipient_id
            aad = make_voice_aad(self.channel_id, sender_id, 0)
            try:
                pcm = decrypt_frame(media_key, encrypted_data, aad)
            except ValueError:
                self._mark_key_problem(sender_id)
                continue
            volume = max(0, min(200, self.local_volume(sender_id)))
            if volume <= 0:
                continue
            if volume != 100:
                pcm = audioop.mul(pcm, SAMPLE_WIDTH, volume / 100)
            if audioop.rms(pcm, SAMPLE_WIDTH) <= 0:
                continue
            self._put_latest(self.playback_queue, pcm)
            self._mark_audible(sender_id)

    def consume_audible_users(self) -> set[int]:
        with self._audible_lock:
            users = set(self._audible_users)
            self._audible_users.clear()
            return users

    def consume_key_problem_users(self) -> set[int]:
        with self._audible_lock:
            users = set(self._key_problem_users)
            self._key_problem_users.clear()
            return users

    def _mark_audible(self, user_id: int) -> None:
        with self._audible_lock:
            self._audible_users.add(user_id)

    def _mark_key_problem(self, user_id: int) -> None:
        with self._audible_lock:
            self._key_problem_users.add(user_id)

    def _put_latest(self, target: queue.Queue[bytes], data: bytes) -> None:
        try:
            target.put_nowait(data)
        except queue.Full:
            try:
                target.get_nowait()
            except queue.Empty:
                pass
            try:
                target.put_nowait(data)
            except queue.Full:
                pass

    def _status(self, text: str) -> None:
        if self.status_callback:
            self.status_callback(text)

    async def _close_websocket_async(self) -> None:
        websocket = self._websocket
        if websocket is not None:
            await websocket.close()

    def _close_active_websocket(self) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._close_websocket_async(), loop)
            future.result(timeout=1)
        except (RuntimeError, FutureTimeoutError):
            pass

    def _drain(self, target: queue.Queue[bytes]) -> None:
        while True:
            try:
                target.get_nowait()
            except queue.Empty:
                return

    def _reset_capture_state(self) -> None:
        self.speaking = False
        self._last_voice_at = 0.0
        self._preroll_frames.clear()
