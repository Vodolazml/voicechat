import asyncio
import logging

from aiohttp import ClientTimeout
from livekit import api

from .config import get_settings


async def remove_media_participant(channel_id, user_id):
    settings = get_settings()
    if not settings.livekit_api_secret:
        return
    async with api.LiveKitAPI(settings.livekit_internal_url, settings.livekit_api_key,
                             settings.livekit_api_secret, timeout=ClientTimeout(total=3)) as client:
        for identity in (str(user_id), f'{user_id}-audio'):
            try:
                await client.room.remove_participant(api.RoomParticipantIdentity(
                    room=f'channel-{channel_id}', identity=identity))
            except api.TwirpError as exc:
                if exc.code != 'not_found':
                    logging.getLogger(__name__).warning('Media removal failed: %s', exc.code)
            except (OSError, asyncio.TimeoutError):
                logging.getLogger(__name__).warning('Media service unavailable during disconnect')
