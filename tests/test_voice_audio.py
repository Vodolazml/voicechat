import asyncio

from app.client.voice_audio import VoiceAudioClient, make_voice_aad
from app.media_crypto import ENCRYPTED_FRAME_PREFIX, encrypt_frame


def make_client(*, user_id: int, key: bytes) -> VoiceAudioClient:
    return VoiceAudioClient(
        ws_url="ws://test",
        ws_headers={},
        channel_id=11,
        user_id=user_id,
        outgoing_media_key=key,
        media_key_for_sender=lambda sender_id: key,
        is_muted=lambda: False,
        is_deafened=lambda: False,
        is_locally_muted=lambda sender_id: False,
        local_volume=lambda sender_id: 100,
        noise_suppression=lambda: False,
        noise_threshold=lambda: 450,
    )


def test_voice_send_frame_uses_server_relay_packet_format() -> None:
    key = b"v" * 32
    client = make_client(user_id=5, key=key)
    sent: list[bytes] = []

    class FakeWebsocket:
        async def send(self, packet: bytes) -> None:
            sent.append(packet)
            client._stop.set()

    client.capture_queue.put_nowait(b"\x01\x02" * 320)

    asyncio.run(client._send_loop(FakeWebsocket()))

    assert sent
    assert sent[0].startswith(ENCRYPTED_FRAME_PREFIX)
    assert not sent[0].startswith((5).to_bytes(4, "big"))


def test_voice_receive_frame_decrypts_server_prefixed_packet() -> None:
    key = b"v" * 32
    sender_id = 5
    receiver = make_client(user_id=9, key=key)
    pcm = b"\x01\x02" * 320
    encrypted = encrypt_frame(key, pcm, make_voice_aad(11, sender_id, 0))
    packet = sender_id.to_bytes(4, "big") + encrypted

    class FakeWebsocket:
        def __aiter__(self):
            self._items = iter([packet])
            return self

        async def __anext__(self):
            try:
                return next(self._items)
            except StopIteration:
                raise StopAsyncIteration

    asyncio.run(receiver._receive_loop(FakeWebsocket()))

    assert receiver.playback_queue.get_nowait() == pcm
