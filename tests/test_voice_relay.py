import asyncio

from app.server.voice_relay import VoiceRelay


class FakeWebSocket:
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[bytes] = []

    async def close(self, code: int = 1000) -> None:
        self.closed = True

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)


def test_old_voice_socket_leave_does_not_remove_new_connection() -> None:
    async def scenario() -> None:
        relay = VoiceRelay()
        old = FakeWebSocket()
        new = FakeWebSocket()

        await relay.join(7, 1, old)  # type: ignore[arg-type]
        await relay.join(7, 1, new)  # type: ignore[arg-type]
        await relay.leave(7, 1, old)  # type: ignore[arg-type]
        await relay.broadcast_audio(7, 2, b"voice")

        assert old.closed is True
        assert new.sent == [(2).to_bytes(4, "big") + b"voice"]

    asyncio.run(scenario())
