"""Server-side control of LiveKit rooms: kick a user, change publish rights."""
import asyncio
import logging

from aiohttp import ClientTimeout
from livekit import api

from .config import get_settings

logger = logging.getLogger(__name__)


def room_name(channel_id: int) -> str:
    return f"channel-{channel_id}"


def media_identities(user_id: int) -> tuple[str, ...]:
    # "u<id>" is the single connection of new clients; "<id>" and "<id>-audio" are kept for 0.2.x clients.
    return (f"u{user_id}", str(user_id), f"{user_id}-audio")


def parse_identity(identity: str) -> int | None:
    raw = identity[1:] if identity.startswith("u") else identity.removesuffix("-audio")
    return int(raw) if raw.isdigit() else None


def presence_identity(identity: str) -> bool:
    """Which LiveKit connection represents 'this person is in the call'."""
    return identity.startswith("u") or identity.endswith("-audio")


def _client():
    settings = get_settings()
    return api.LiveKitAPI(settings.livekit_internal_url, settings.livekit_api_key,
                          settings.livekit_api_secret, timeout=ClientTimeout(total=3))


async def remove_media_participant(channel_id, user_id):
    settings = get_settings()
    if not settings.livekit_api_secret:
        return
    async with _client() as client:
        for identity in media_identities(user_id):
            try:
                await client.room.remove_participant(api.RoomParticipantIdentity(
                    room=room_name(channel_id), identity=identity))
            except api.TwirpError as exc:
                if exc.code != 'not_found':
                    logger.warning('Media removal failed: %s', exc.code)
            except (OSError, asyncio.TimeoutError):
                logger.warning('Media service unavailable during disconnect')


async def set_media_publish_allowed(channel_id: int, user_id: int, allowed: bool) -> None:
    """Server mute: LiveKit stops forwarding the user's microphone immediately."""
    settings = get_settings()
    if not settings.livekit_api_secret:
        return
    async with _client() as client:
        for identity in media_identities(user_id):
            try:
                await client.room.update_participant(api.UpdateParticipantRequest(
                    room=room_name(channel_id), identity=identity,
                    permission=api.ParticipantPermission(
                        can_subscribe=True, can_publish=allowed, can_publish_data=False,
                        can_publish_sources=(
                            [api.TrackSource.MICROPHONE, api.TrackSource.SCREEN_SHARE,
                             api.TrackSource.SCREEN_SHARE_AUDIO] if allowed else []),
                    )))
            except api.TwirpError as exc:
                if exc.code != 'not_found':
                    logger.warning('Media permission update failed: %s', exc.code)
            except (OSError, asyncio.TimeoutError):
                logger.warning('Media service unavailable during permission update')
