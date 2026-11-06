"""Channels, voice presence, moderation, media tokens and the live event stream.

Who is in a voice channel is decided by the media server: LiveKit webhooks remove a person the moment
their connection closes (app closed, crash, network loss), so nobody can stay as a hidden listener.
Heartbeats are only a fallback for when webhooks are not configured.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import media_sessions, models, schemas
from .audit import write_audit
from .config import get_settings
from .database import SessionLocal, get_db
from .deps import (can_manage_space, channel_participant_ids, current_user, ensure_channel_member,
                   is_space_admin, require_space_manager, user_from_token)
from .events import hub
from .limits import rate_limit
from .permissions import has_permission
from .security import decode_token

logger = logging.getLogger("voicechat.presence")
router = APIRouter()
settings = get_settings()

EVENTS_AUTH_TIMEOUT_SECONDS = 5
EVENTS_RECHECK_SECONDS = 60
JANITOR_INTERVAL_SECONDS = 10
WAKE_COOLDOWN_SECONDS = 60
# Last time each person was woken up. One cooldown per person shared by everybody: after anyone wakes
# them, nobody can do it again for a minute. (One API process, so memory is enough.)
_last_wake: dict[int, float] = {}


# --- serialisation and event helpers -------------------------------------------------------------

def channel_out(channel: models.Channel) -> schemas.ChannelOut:
    return schemas.ChannelOut.model_validate(channel)


def voice_state_out(db: Session, state: models.VoiceState) -> schemas.VoiceStateOut:
    user = db.get(models.User, state.user_id)
    return schemas.VoiceStateOut(
        user_id=state.user_id,
        display_name=user.display_name if user else f"Пользователь {state.user_id}",
        channel_id=state.channel_id,
        muted=state.muted,
        deafened=state.deafened,
        speaking=state.speaking,
        server_muted=state.server_muted,
        status=state.status,
        avatar_version=user.avatar_version if user else 0,
    )


def publish_state(db: Session, state: models.VoiceState) -> None:
    hub.publish(channel_participant_ids(db, state.channel_id),
                {"type": "voice.state", "state": voice_state_out(db, state).model_dump()})


def publish_left(db: Session, channel_id: int, user_id: int) -> None:
    hub.publish(channel_participant_ids(db, channel_id) | {user_id},
                {"type": "voice.left", "channel_id": channel_id, "user_id": user_id})


def space_member_ids(db: Session, space_id: int) -> set[int]:
    return set(db.scalars(select(models.SpaceMember.user_id).where(models.SpaceMember.space_id == space_id)))


def publish_channel(db: Session, channel: models.Channel) -> None:
    recipients = channel_participant_ids(db, channel.id)
    hub.publish(recipients, {"type": "channel.updated", "channel": channel_out(channel).model_dump()})


def leave_voice(db: Session, state: models.VoiceState) -> tuple[int, int]:
    """Delete a voice state and tell the channel. Returns (channel_id, user_id) for the media kick."""
    gone = (state.channel_id, state.user_id)
    db.delete(state)
    db.flush()
    publish_left(db, *gone)
    return gone


def grant_public_channels(db: Session, space_id: int, user_id: int) -> None:
    channels = db.scalars(select(models.Channel).where(
        models.Channel.space_id == space_id, models.Channel.is_deleted.is_(False),
        models.Channel.is_private.is_(False)))
    for channel in channels:
        if not db.get(models.ChannelMember, {"channel_id": channel.id, "user_id": user_id}):
            db.add(models.ChannelMember(channel_id=channel.id, user_id=user_id))


# --- channels ------------------------------------------------------------------------------------

@router.get("/spaces/{space_id}/channels", response_model=list[schemas.ChannelOut], tags=["channels"])
def list_channels(space_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    if not db.get(models.SpaceMember, {"space_id": space_id, "user_id": user.id}):
        raise HTTPException(status_code=403, detail="Нет доступа к пространству")
    rows = db.scalars(
        select(models.Channel)
        .join(models.ChannelMember, models.ChannelMember.channel_id == models.Channel.id)
        .where(models.Channel.space_id == space_id, models.ChannelMember.user_id == user.id,
               models.Channel.is_deleted.is_(False))
        .order_by(models.Channel.type, models.Channel.position, models.Channel.name)
    )
    return list(rows)


@router.post("/channels", response_model=schemas.ChannelOut, tags=["channels"])
def create_channel(payload: schemas.ChannelCreateIn, request: Request,
                   actor: models.User = Depends(current_user),
                   db: Session = Depends(get_db)):
    if not db.get(models.SpaceMember, {"space_id": payload.space_id, "user_id": actor.id}):
        raise HTTPException(status_code=403, detail="Нет доступа к пространству")
    require_space_manager(db, actor, payload.space_id, "channels.create")
    position = (db.scalar(select(func.max(models.Channel.position)).where(
        models.Channel.space_id == payload.space_id)) or 0) + 1
    channel = models.Channel(space_id=payload.space_id, name=payload.name, type=payload.type,
                             created_by=actor.id, position=position, user_limit=payload.user_limit,
                             is_private=payload.is_private)
    db.add(channel)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Канал с таким названием уже существует") from exc
    owners = set(db.scalars(select(models.User.id).where(models.User.is_owner.is_(True))))
    members = ({actor.id} if channel.is_private else space_member_ids(db, payload.space_id)) | owners
    for member_id in members:
        db.add(models.ChannelMember(channel_id=channel.id, user_id=member_id))
    write_audit(db, actor_id=actor.id, action="channels.create", target_type="channel", target_id=channel.id,
                request=request)
    db.commit()
    publish_channel(db, channel)
    return channel


@router.patch("/channels/{channel_id}", response_model=schemas.ChannelOut, tags=["channels"])
def update_channel(channel_id: int, payload: schemas.ChannelUpdateIn, request: Request,
                   actor: models.User = Depends(current_user),
                   db: Session = Depends(get_db)):
    channel = ensure_channel_member(db, actor.id, channel_id)
    require_space_manager(db, actor, channel.space_id, "channels.update")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(channel, key, value)
    if changes.get("is_private") is False:
        for member_id in space_member_ids(db, channel.space_id):
            if not db.get(models.ChannelMember, {"channel_id": channel.id, "user_id": member_id}):
                db.add(models.ChannelMember(channel_id=channel.id, user_id=member_id))
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Канал с таким названием уже существует") from exc
    write_audit(db, actor_id=actor.id, action="channels.update", target_type="channel", target_id=channel_id,
                request=request, details={k: v for k, v in changes.items() if k != "name"})
    db.commit()
    publish_channel(db, channel)
    return channel


@router.put("/spaces/{space_id}/channels/order", response_model=list[schemas.ChannelOut], tags=["channels"])
def reorder_channels(space_id: int, payload: schemas.ChannelOrderIn, request: Request,
                     actor: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """The manager puts channels in the order they want; ids not listed keep their place after them."""
    if not db.get(models.SpaceMember, {"space_id": space_id, "user_id": actor.id}):
        raise HTTPException(status_code=403, detail="Нет доступа к пространству")
    require_space_manager(db, actor, space_id, "channels.update")
    channels = {c.id: c for c in db.scalars(select(models.Channel).where(
        models.Channel.space_id == space_id, models.Channel.is_deleted.is_(False)))}
    ordered = [cid for cid in dict.fromkeys(payload.channel_ids) if cid in channels]
    rest = sorted((c for cid, c in channels.items() if cid not in ordered), key=lambda c: (c.position, c.id))
    changed = []
    for position, channel in enumerate([channels[cid] for cid in ordered] + rest, start=1):
        if channel.position != position:
            channel.position = position
            changed.append(channel)
    write_audit(db, actor_id=actor.id, action="channels.reorder", target_type="space", target_id=space_id,
                request=request)
    db.commit()
    for channel in changed:
        publish_channel(db, channel)
    return sorted(channels.values(), key=lambda c: (c.type, c.position, c.id))


@router.delete("/channels/{channel_id}", tags=["channels"])
async def delete_channel(channel_id: int, request: Request,
                         actor: models.User = Depends(current_user),
                         db: Session = Depends(get_db)) -> dict[str, str]:
    channel = ensure_channel_member(db, actor.id, channel_id)
    require_space_manager(db, actor, channel.space_id, "channels.delete")
    recipients = channel_participant_ids(db, channel_id)
    gone = [leave_voice(db, state) for state in
            db.scalars(select(models.VoiceState).where(models.VoiceState.channel_id == channel_id))]
    channel.is_deleted = True
    channel.name = f"{channel.name[:80]} (удалён {channel.id})"
    write_audit(db, actor_id=actor.id, action="channels.delete", target_type="channel", target_id=channel_id,
                request=request)
    db.commit()
    hub.publish(recipients, {"type": "channel.deleted", "channel_id": channel_id, "space_id": channel.space_id})
    for gone_channel, gone_user in gone:
        await media_sessions.remove_media_participant(gone_channel, gone_user)
    return {"status": "ok"}


@router.post("/channels/{channel_id}/members", tags=["channels"])
def add_channel_member(channel_id: int, payload: schemas.MemberIn, request: Request,
                       actor: models.User = Depends(current_user),
                       db: Session = Depends(get_db)) -> dict[str, str]:
    channel = db.get(models.Channel, channel_id)
    user = db.get(models.User, payload.user_id)
    if not channel or channel.is_deleted or not user:
        raise HTTPException(status_code=404, detail="Канал или пользователь не найден")
    require_space_manager(db, actor, channel.space_id, "channels.members.manage")
    if not db.get(models.SpaceMember, {"space_id": channel.space_id, "user_id": payload.user_id}):
        db.add(models.SpaceMember(space_id=channel.space_id, user_id=payload.user_id))
    if not db.get(models.ChannelMember, {"channel_id": channel_id, "user_id": payload.user_id}):
        db.add(models.ChannelMember(channel_id=channel_id, user_id=payload.user_id))
    write_audit(db, actor_id=actor.id, action="channels.members.add", target_type="channel",
                target_id=channel_id, request=request, details={"user_id": payload.user_id})
    db.commit()
    publish_channel(db, channel)
    return {"status": "ok"}


# --- voice presence ------------------------------------------------------------------------------

@router.post("/channels/{channel_id}/connect", response_model=schemas.VoiceStateOut, tags=["voice"])
async def connect_channel(channel_id: int, payload: schemas.VoiceJoinIn, request: Request,
                          user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(f"voice:{user.id}", 30, 60)
    channel = ensure_channel_member(db, user.id, channel_id)
    if channel.type != "voice":
        raise HTTPException(status_code=400, detail="Подключаться можно только к голосовому каналу")
    if not has_permission(db, user.id, "voice.join"):
        raise HTTPException(status_code=403, detail="Нет права подключаться к голосовым каналам")
    existing = db.get(models.VoiceState, user.id)
    if channel.user_limit and not can_manage_space(db, user, channel.space_id, "voice.moderate"):
        inside = db.scalar(select(func.count()).select_from(models.VoiceState).where(
            models.VoiceState.channel_id == channel_id, models.VoiceState.user_id != user.id)) or 0
        if inside >= channel.user_limit:
            raise HTTPException(status_code=409, detail="Канал заполнен")
    previous_channel_id = existing.channel_id if existing and existing.channel_id != channel_id else None
    now = models.utcnow()
    if existing:
        if previous_channel_id is not None:
            publish_left(db, previous_channel_id, user.id)
            existing.joined_at = now
            existing.server_muted = False
        existing.channel_id = channel_id
        existing.muted = payload.muted
        existing.deafened = payload.deafened
        existing.speaking = False
        existing.status = "connected"
        existing.updated_at = now
        state = existing
    else:
        state = models.VoiceState(user_id=user.id, channel_id=channel_id, muted=payload.muted,
                                  deafened=payload.deafened, updated_at=now, joined_at=now)
        db.add(state)
    write_audit(db, actor_id=user.id, action="voice.connect", target_type="channel", target_id=channel_id,
                request=request)
    db.commit()
    publish_state(db, state)
    if previous_channel_id is not None:
        # Switching channels: the old room must not keep a hidden listener.
        await media_sessions.remove_media_participant(previous_channel_id, user.id)
    return voice_state_out(db, state)


@router.put("/channels/{channel_id}/voice-state", response_model=schemas.VoiceStateOut, tags=["voice"])
def update_voice_state(channel_id: int, payload: schemas.VoiceStateIn,
                       user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    state = db.get(models.VoiceState, user.id)
    if not state or state.channel_id != channel_id:
        raise HTTPException(status_code=404, detail="Вы не подключены к этому каналу")
    visible_change = (state.muted, state.deafened, state.status) != (payload.muted, payload.deafened, payload.status)
    state.muted = payload.muted
    state.deafened = payload.deafened
    state.speaking = payload.speaking
    state.status = payload.status
    state.updated_at = models.utcnow()
    db.commit()
    if visible_change:
        publish_state(db, state)
    return voice_state_out(db, state)


@router.post("/channels/{channel_id}/disconnect", tags=["voice"])
async def disconnect_channel(channel_id: int, request: Request,
                             user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    state = db.get(models.VoiceState, user.id)
    if state and state.channel_id == channel_id:
        leave_voice(db, state)
        write_audit(db, actor_id=user.id, action="voice.disconnect", target_type="channel",
                    target_id=channel_id, request=request)
        db.commit()
        await media_sessions.remove_media_participant(channel_id, user.id)
    return {"status": "ok"}


@router.get("/channels/{channel_id}/voice", response_model=list[schemas.VoiceStateOut], tags=["voice"])
async def voice_states(channel_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    ensure_channel_member(db, user.id, channel_id)
    rows = db.scalars(select(models.VoiceState).where(models.VoiceState.channel_id == channel_id)
                      .order_by(models.VoiceState.joined_at, models.VoiceState.user_id))
    return [voice_state_out(db, row) for row in rows]


@router.get("/spaces/{space_id}/voice", response_model=list[schemas.VoiceStateOut], tags=["voice"])
def space_voice_states(space_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """Everyone in every voice channel the user can see, in one request (for the channel list)."""
    if not db.get(models.SpaceMember, {"space_id": space_id, "user_id": user.id}):
        raise HTTPException(status_code=403, detail="Нет доступа к пространству")
    visible = select(models.ChannelMember.channel_id).where(models.ChannelMember.user_id == user.id)
    rows = db.scalars(
        select(models.VoiceState)
        .join(models.Channel, models.Channel.id == models.VoiceState.channel_id)
        .where(models.Channel.space_id == space_id, models.Channel.is_deleted.is_(False),
               models.VoiceState.channel_id.in_(visible))
        .order_by(models.VoiceState.channel_id, models.VoiceState.joined_at, models.VoiceState.user_id)
    )
    return [voice_state_out(db, row) for row in rows]


# --- moderation ----------------------------------------------------------------------------------

def _target_state(db: Session, actor: models.User, user_id: int) -> models.VoiceState:
    state = db.get(models.VoiceState, user_id)
    if not state:
        raise HTTPException(status_code=404, detail="Пользователь не в голосовом канале")
    channel = ensure_channel_member(db, actor.id, state.channel_id)
    require_space_manager(db, actor, channel.space_id, "voice.moderate")
    target = db.get(models.User, user_id)
    if target and target.is_owner and not actor.is_owner:
        raise HTTPException(status_code=403, detail="Нельзя модерировать владельца сервера")
    if target and is_space_admin(db, target, channel.space_id) and not is_space_admin(db, actor, channel.space_id):
        raise HTTPException(status_code=403, detail="Модератор не может модерировать создателя пространства")
    return state


@router.post("/voice/{user_id}/move", response_model=schemas.VoiceStateOut, tags=["moderation"])
async def move_user(user_id: int, payload: schemas.MoveIn, request: Request,
                    actor: models.User = Depends(current_user),
                    db: Session = Depends(get_db)):
    state = _target_state(db, actor, user_id)
    target_channel = ensure_channel_member(db, actor.id, payload.channel_id)
    if target_channel.type != "voice":
        raise HTTPException(status_code=400, detail="Переместить можно только в голосовой канал")
    if not db.get(models.ChannelMember, {"channel_id": payload.channel_id, "user_id": user_id}):
        raise HTTPException(status_code=403, detail="У пользователя нет доступа к этому каналу")
    old_channel = state.channel_id
    if old_channel == payload.channel_id:
        return voice_state_out(db, state)
    publish_left(db, old_channel, user_id)
    state.channel_id = payload.channel_id
    state.joined_at = models.utcnow()
    state.updated_at = models.utcnow()
    write_audit(db, actor_id=actor.id, action="voice.move", target_type="user", target_id=user_id,
                request=request, details={"from": old_channel, "to": payload.channel_id})
    db.commit()
    publish_state(db, state)
    hub.publish([user_id], {"type": "voice.moved", "channel_id": payload.channel_id, "by": actor.display_name})
    await media_sessions.remove_media_participant(old_channel, user_id)
    return voice_state_out(db, state)


@router.post("/voice/{user_id}/kick", tags=["moderation"])
async def kick_user(user_id: int, request: Request,
                    actor: models.User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict[str, str]:
    state = _target_state(db, actor, user_id)
    channel_id, _ = leave_voice(db, state)
    write_audit(db, actor_id=actor.id, action="voice.kick", target_type="user", target_id=user_id,
                request=request, details={"channel_id": channel_id})
    db.commit()
    hub.publish([user_id], {"type": "voice.kicked", "channel_id": channel_id, "by": actor.display_name})
    await media_sessions.remove_media_participant(channel_id, user_id)
    return {"status": "ok"}


@router.post("/voice/{user_id}/server-mute", response_model=schemas.VoiceStateOut, tags=["moderation"])
async def server_mute(user_id: int, payload: schemas.ServerMuteIn, request: Request,
                      actor: models.User = Depends(current_user),
                      db: Session = Depends(get_db)):
    state = _target_state(db, actor, user_id)
    state.server_muted = payload.muted
    write_audit(db, actor_id=actor.id, action="voice.server_mute", target_type="user", target_id=user_id,
                request=request, details={"muted": payload.muted})
    db.commit()
    publish_state(db, state)
    await media_sessions.set_media_publish_allowed(state.channel_id, user_id, not payload.muted)
    return voice_state_out(db, state)


# --- waking someone up ---------------------------------------------------------------------------

@router.post("/voice/{user_id}/wake", tags=["voice"])
def wake_user(user_id: int, request: Request, actor: models.User = Depends(current_user),
              db: Session = Depends(get_db)) -> dict:
    """Plays a short tune for someone sitting in voice with the microphone or the sound off."""
    import time

    state = db.get(models.VoiceState, user_id)
    if not state:
        raise HTTPException(status_code=404, detail="Пользователь не в голосовом канале")
    ensure_channel_member(db, actor.id, state.channel_id)
    if user_id == actor.id:
        raise HTTPException(status_code=400, detail="Себя разбудить нельзя")
    if not (state.muted or state.deafened or state.server_muted):
        raise HTTPException(status_code=409, detail="У него всё включено, он и так вас слышит")
    rate_limit(f"wake:{actor.id}", 10, 600)
    now = time.time()
    last = _last_wake.get(user_id, 0.0)
    if now - last < WAKE_COOLDOWN_SECONDS:
        wait = int(WAKE_COOLDOWN_SECONDS - (now - last)) + 1
        raise HTTPException(status_code=429, detail=f"Его только что будили, попробуйте через {wait} с")
    _last_wake[user_id] = now
    until = now + WAKE_COOLDOWN_SECONDS
    write_audit(db, actor_id=actor.id, action="voice.wake", target_type="user", target_id=user_id, request=request)
    db.commit()
    event = {"type": "voice.wake", "user_id": user_id, "channel_id": state.channel_id, "by": actor.display_name,
             "by_id": actor.id, "until": until}
    hub.publish(channel_participant_ids(db, state.channel_id) | {user_id}, event)
    return {"status": "ok", "until": until}


# --- media tokens --------------------------------------------------------------------------------

def _media_token(db: Session, user: models.User, channel_id: int, identity: str, sources: list[str],
                 can_publish: bool, can_subscribe: bool) -> dict[str, str]:
    from livekit import api as livekit_api

    if not settings.livekit_url or not settings.livekit_api_secret:
        raise HTTPException(status_code=503, detail="Медиасервер ещё не настроен")
    token = (livekit_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
             .with_identity(identity).with_name(user.display_name)
             .with_ttl(timedelta(minutes=10))
             .with_grants(livekit_api.VideoGrants(
                 room_join=True, room=media_sessions.room_name(channel_id),
                 can_publish=can_publish, can_subscribe=can_subscribe, can_publish_data=False,
                 can_publish_sources=sources if can_publish else [])).to_jwt())
    return {"url": settings.livekit_url, "token": token, "identity": identity}


def _require_in_channel(db: Session, user: models.User, channel_id: int) -> models.VoiceState:
    ensure_channel_member(db, user.id, channel_id)
    state = db.get(models.VoiceState, user.id)
    if not state or state.channel_id != channel_id:
        raise HTTPException(status_code=403, detail="Сначала войдите в канал")
    return state


@router.post("/channels/{channel_id}/media-token", tags=["video"])
def media_token(channel_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """One LiveKit connection per client: microphone, screen and screen audio."""
    state = _require_in_channel(db, user, channel_id)
    sources = []
    if has_permission(db, user.id, "voice.speak"):
        sources.append("microphone")
    if has_permission(db, user.id, "screen_share.start"):
        sources += ["screen_share", "screen_share_audio"]
        space = db.get(models.Space, db.get(models.Channel, channel_id).space_id)
        if space is not None and space.camera_allowed:
            sources.append("camera")
    can_publish = bool(sources) and not state.server_muted
    can_subscribe = has_permission(db, user.id, "voice.join")
    return _media_token(db, user, channel_id, f"u{user.id}", sources, can_publish, can_subscribe)


@router.post("/channels/{channel_id}/video-token", tags=["video"])
def video_token(channel_id: int, audio: bool = False, user: models.User = Depends(current_user),
                db: Session = Depends(get_db)):
    """Two-connection scheme used by 0.2.x clients."""
    state = _require_in_channel(db, user, channel_id)
    can_publish = has_permission(db, user.id, "voice.speak" if audio else "screen_share.start")
    if audio and state.server_muted:
        can_publish = False
    can_subscribe = has_permission(db, user.id, "voice.join" if audio else "screen_share.view")
    if not can_publish and not can_subscribe:
        raise HTTPException(status_code=403, detail="Нет доступа к видео")
    identity = f"{user.id}-audio" if audio else str(user.id)
    return _media_token(db, user, channel_id, identity, ["microphone" if audio else "screen_share"],
                        can_publish, can_subscribe)


# --- LiveKit webhooks ----------------------------------------------------------------------------

@router.post("/livekit/webhook", include_in_schema=False)
async def livekit_webhook(request: Request) -> dict[str, str]:
    from livekit import api as livekit_api

    if not settings.livekit_api_secret:
        raise HTTPException(status_code=404)
    body = (await request.body()).decode("utf-8", errors="replace")
    try:
        receiver = livekit_api.WebhookReceiver(
            livekit_api.TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret))
        event = receiver.receive(body, request.headers.get("authorization", ""))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Bad webhook signature") from exc
    if event.event != "participant_left":
        return {"status": "ignored"}
    identity = event.participant.identity
    user_id = media_sessions.parse_identity(identity)
    room = event.room.name
    if user_id is None or not media_sessions.presence_identity(identity) or not room.startswith("channel-"):
        return {"status": "ignored"}
    channel_id = int(room.removeprefix("channel-"))
    await asyncio.to_thread(_drop_state_if_in, channel_id, user_id)
    return {"status": "ok"}


def _drop_state_if_in(channel_id: int, user_id: int) -> None:
    with SessionLocal() as db:
        state = db.get(models.VoiceState, user_id)
        # A participant_left for the old room after a channel switch must not end the new call.
        if state and state.channel_id == channel_id:
            leave_voice(db, state)
            db.commit()


# --- stale state janitor -------------------------------------------------------------------------

def collect_stale_voice_states() -> list[tuple[int, int]]:
    cutoff = models.utcnow() - timedelta(seconds=settings.voice_state_timeout_seconds)
    with SessionLocal() as db:
        stale = list(db.scalars(select(models.VoiceState).where(models.VoiceState.updated_at < cutoff)))
        gone = [leave_voice(db, row) for row in stale]
        db.commit()
    return gone


async def presence_janitor() -> None:
    while True:
        await asyncio.sleep(JANITOR_INTERVAL_SECONDS)
        try:
            for channel_id, user_id in await asyncio.to_thread(collect_stale_voice_states):
                await media_sessions.remove_media_participant(channel_id, user_id)
        except Exception:
            logger.exception("Presence janitor failed")


# --- event stream --------------------------------------------------------------------------------

def _session_still_valid(token: str) -> bool:
    try:
        with SessionLocal() as db:
            user_from_token(db, token)
        return True
    except HTTPException:
        return False


def _websocket_origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    return not origin or origin in settings.cors_origins


@router.websocket("/events")
async def events(websocket: WebSocket) -> None:
    """First message must be {"type": "auth", "token": "<access token>"}; tokens never go into URLs."""
    if not _websocket_origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    host = websocket.client.host if websocket.client else "unknown"
    try:
        rate_limit(f"events:{host}", 60, 60)
    except HTTPException:
        await websocket.close(code=1013)
        return
    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=EVENTS_AUTH_TIMEOUT_SECONDS)
        message = json.loads(raw) if len(raw) < 8192 else {}
        token = str(message.get("token", "")) if message.get("type") == "auth" else ""
        user_id = decode_token(token).user_id
        if not await asyncio.to_thread(_session_still_valid, token):
            raise HTTPException(status_code=401)
    except (asyncio.TimeoutError, ValueError, HTTPException, WebSocketDisconnect, json.JSONDecodeError):
        try:
            await websocket.close(code=4401)
        except RuntimeError:
            pass
        return

    subscriber = hub.subscribe(user_id)
    await websocket.send_json({"type": "hello", "user_id": user_id})

    async def pump_out() -> None:
        while True:
            try:
                event = await asyncio.wait_for(subscriber.queue.get(), timeout=EVENTS_RECHECK_SECONDS)
            except asyncio.TimeoutError:
                if not await asyncio.to_thread(_session_still_valid, token):
                    # Access tokens are short-lived: the client reconnects with a fresh one.
                    await websocket.send_json({"type": "reauth"})
                    return
                await websocket.send_json({"type": "ping"})
                continue
            if event is None or subscriber.overflowed:
                await websocket.send_json({"type": "resync"})
                return
            await websocket.send_json(event)
            if event.get("type") == "session.ended":
                return

    async def pump_in() -> None:
        while True:
            text = await websocket.receive_text()
            if len(text) > 1024:
                return

    sender = asyncio.create_task(pump_out())
    receiver = asyncio.create_task(pump_in())
    try:
        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)
        hub.unsubscribe(subscriber)
        try:
            await websocket.close(code=4000)
        except RuntimeError:
            pass
