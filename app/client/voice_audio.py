from __future__ import annotations

import asyncio
import audioop
import queue
import threading
from dataclasses import dataclass
from time import monotonic
from collections.abc import Callable

import sounddevice as sd
import websockets


SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2
BLOCKSIZE = 320
FRAME_BYTES = BLOCKSIZE * CHANNELS * SAMPLE_WIDTH
SPEAKING_RMS = 450
SPEAKING_HOLD_SECONDS = 0.35


@dataclass(frozen=True)
class AudioDevice:
    id: int
    name: str
    inputs: int
    outputs: int


def audio_devices() -> tuple[list[AudioDevice], list[AudioDevice]]:
    inputs: list[AudioDevice] = []
    outputs: list[AudioDevice] = []
    for index, raw in enumerate(sd.query_devices()):
        name = str(raw["name"])
        device = AudioDevice(
            id=index,
            name=name,
            inputs=int(raw["max_input_channels"]),
            outputs=int(raw["max_output_channels"]),
        )
        if device.inputs > 0:
            inputs.append(device)
        if device.outputs > 0:
            outputs.append(device)
    return inputs, outputs


class VoiceAudioClient:
    def __init__(
        self,
        *,
        ws_url: str,
        is_muted: Callable[[], bool],
        is_deafened: Callable[[], bool],
        is_locally_muted: Callable[[int], bool],
        local_volume: Callable[[int], int],
        status_callback: Callable[[str], None] | None = None,
        input_device: int | None = None,
        output_device: int | None = None,
    ) -> None:
        self.ws_url = ws_url
        self.is_muted = is_muted
        self.is_deafened = is_deafened
        self.is_locally_muted = is_locally_muted
        self.local_volume = local_volume
        self.status_callback = status_callback
        self.input_device = input_device
        self.output_device = output_device
        self.capture_queue: queue.Queue[bytes] = queue.Queue(maxsize=40)
        self.playback_queue: queue.Queue[bytes] = queue.Queue(maxsize=80)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._input_stream: sd.RawInputStream | None = None
        self._output_stream: sd.RawOutputStream | None = None
        self.speaking = False
        self._last_voice_at = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._input_stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=BLOCKSIZE,
            device=self.input_device,
            callback=self._capture_callback,
        )
        self._output_stream = sd.RawOutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=BLOCKSIZE,
            device=self.output_device,
            callback=self._playback_callback,
        )
        self._input_stream.start()
        self._output_stream.start()
        self._thread = threading.Thread(target=self._run_loop, name="voice-audio", daemon=True)
        self._thread.start()
        self._status("audio connected")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        for stream in (self._input_stream, self._output_stream):
            if stream:
                try:
                    stream.stop()
                    stream.close()
                except sd.PortAudioError:
                    pass
        self._input_stream = None
        self._output_stream = None
        self._drain(self.capture_queue)
        self._drain(self.playback_queue)
        self._status("audio disconnected")

    def _capture_callback(self, indata, frames, time_info, status) -> None:
        if self._stop.is_set() or self.is_muted():
            self.speaking = False
            return
        data = bytes(indata)
        if not data:
            return
        now = monotonic()
        if audioop.rms(data, SAMPLE_WIDTH) >= SPEAKING_RMS:
            self._last_voice_at = now
            self.speaking = True
        elif now - self._last_voice_at > SPEAKING_HOLD_SECONDS:
            self.speaking = False
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
        try:
            asyncio.run(self._socket_loop())
        except Exception as exc:
            self._status(f"audio error: {exc}")

    async def _socket_loop(self) -> None:
        async with websockets.connect(self.ws_url, max_size=8192) as websocket:
            sender = asyncio.create_task(self._send_loop(websocket))
            receiver = asyncio.create_task(self._receive_loop(websocket))
            done, pending = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                task.result()

    async def _send_loop(self, websocket) -> None:
        while not self._stop.is_set():
            try:
                frame = self.capture_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.004)
                continue
            await websocket.send(frame)

    async def _receive_loop(self, websocket) -> None:
        async for message in websocket:
            if self._stop.is_set():
                break
            if not isinstance(message, bytes) or len(message) <= 4:
                continue
            user_id = int.from_bytes(message[:4], "big")
            if self.is_deafened() or self.is_locally_muted(user_id):
                continue
            pcm = message[4:]
            volume = max(0, min(200, self.local_volume(user_id)))
            if volume != 100:
                pcm = audioop.mul(pcm, SAMPLE_WIDTH, volume / 100)
            self._put_latest(self.playback_queue, pcm)

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

    def _drain(self, target: queue.Queue[bytes]) -> None:
        while True:
            try:
                target.get_nowait()
            except queue.Empty:
                return
