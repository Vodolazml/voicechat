"""Opt-in real SFU test: VOICECHAT_TEST_LIVEKIT_URL=ws://127.0.0.1:7880."""
import asyncio
import os
import secrets

import pytest


@pytest.mark.skipif(not os.getenv('VOICECHAT_TEST_LIVEKIT_URL'), reason='local SFU not configured')
def test_encrypted_h264_frame_crosses_real_sfu():
    from livekit import rtc, api

    async def scenario():
        room_name = 'integration-' + secrets.token_hex(8)
        def token(identity):
            return (api.AccessToken('devkey', 'secret').with_identity(identity)
                    .with_grants(api.VideoGrants(room_join=True, room=room_name)).to_jwt())
        sender, receiver = rtc.Room(), rtc.Room()
        key = secrets.token_bytes(32)
        got_frame = asyncio.Event()
        tasks = []
        async def consume(track):
            stream = rtc.VideoStream(track, capacity=1, format=rtc.VideoBufferType.BGRA)
            try:
                async for event in stream:
                    assert event.frame.width == 320
                    assert event.frame.height == 180
                    assert any(event.frame.data)
                    got_frame.set()
                    return
            finally:
                await stream.aclose()
        @receiver.on('track_subscribed')
        def subscribed(track, publication, participant):
            tasks.append(asyncio.create_task(consume(track)))
        source = None
        try:
            options = rtc.RoomOptions(encryption=rtc.E2EEOptions())
            await sender.connect(os.environ['VOICECHAT_TEST_LIVEKIT_URL'], token('1'), options=options)
            sender.e2ee_manager.key_provider.set_key('1', key, 0)
            await receiver.connect(os.environ['VOICECHAT_TEST_LIVEKIT_URL'], token('2'), options=options)
            receiver.e2ee_manager.key_provider.set_key('1', key, 0)
            source = rtc.VideoSource(320, 180, is_screencast=True)
            track = rtc.LocalVideoTrack.create_video_track('screen', source)
            await sender.local_participant.publish_track(track, rtc.TrackPublishOptions(
                source=rtc.TrackSource.SOURCE_SCREENSHARE, video_codec=rtc.VideoCodec.H264,
                video_encoding=rtc.VideoEncoding(max_framerate=30, max_bitrate=1_000_000)))
            frame = rtc.VideoFrame(320, 180, rtc.VideoBufferType.BGRA, b'\x20\x80\xf0\xff' * (320 * 180))
            for _ in range(300):
                source.capture_frame(frame)
                if got_frame.is_set():
                    break
                await asyncio.sleep(1 / 30)
            assert got_frame.is_set(), 'No decoded video received from SFU'
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await sender.disconnect()
            await receiver.disconnect()
            if source:
                await source.aclose()
    asyncio.run(asyncio.wait_for(scenario(), timeout=30))


@pytest.mark.skipif(not os.getenv('VOICECHAT_TEST_LIVEKIT_URL'), reason='local SFU not configured')
def test_voice_clients_exchange_opus_after_rejoin():
    from livekit import api
    from app.client.livekit_voice import LiveKitVoiceClient
    from app.client.voice_audio import FRAME_BYTES
    import struct

    async def scenario():
        room_name = 'voice-' + secrets.token_hex(8)
        keys = {1: secrets.token_bytes(32), 2: secrets.token_bytes(32)}
        def client(user_id):
            def credentials():
                token = (api.AccessToken('devkey', 'secret').with_identity(f'{user_id}-audio')
                    .with_grants(api.VideoGrants(room_join=True, room=room_name)).to_jwt())
                return {'url': os.environ['VOICECHAT_TEST_LIVEKIT_URL'], 'token': token}
            return LiveKitVoiceClient(media_credentials=credentials,
                ws_url='', ws_headers={}, channel_id=1, user_id=user_id,
                outgoing_media_key=keys[user_id], media_key_for_sender=lambda uid: keys.get(uid),
                is_muted=lambda: False, is_deafened=lambda: False,
                is_locally_muted=lambda uid: False, local_volume=lambda uid: 100,
                noise_suppression=lambda: False, noise_threshold=lambda: 450)
        receiver = client(2)
        receive_task = asyncio.create_task(receiver._socket_loop())
        try:
            for _ in range(2):
                keys[1] = secrets.token_bytes(32)
                sender = client(1)
                send_task = asyncio.create_task(sender._socket_loop())
                heard = False
                try:
                    # Use a tone, not DC: Opus removes constant DC energy.
                    import math
                    pcm = b''.join(struct.pack('<h', int(6000 * math.sin(2 * math.pi * 440 * n / 16000)))
                                   for n in range(320))
                    for _ in range(400):
                        sender._put_latest(sender.capture_queue, pcm)
                        output = bytearray(FRAME_BYTES)
                        receiver._playback_callback(output, 320, None, None)
                        if 1 in receiver.consume_audible_users():
                            heard = True
                            break
                        await asyncio.sleep(0.02)
                    assert heard, 'No audible decoded Opus after join/rejoin'
                finally:
                    sender._stop.set()
                    await asyncio.wait_for(send_task, 10)
                receiver.mixer.clear()
        finally:
            receiver._stop.set()
            await asyncio.wait_for(receive_task, 10)
    asyncio.run(asyncio.wait_for(scenario(), 35))
