"""Spaces: anyone creates their own, others join with its invite code.

The person who created a space manages it (channels, invite code, moderation inside it). The server
owner sees every space and channel on the server; everybody else sees only the spaces they joined.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import media_sessions, models, schemas
from .audit import write_audit
from .config import get_settings
from .database import get_db
from .deps import can_manage_space, current_user, require_space_manager
from .events import hub
from .limits import rate_limit
from .presence import grant_public_channels, leave_voice, space_member_ids

router = APIRouter(tags=["spaces"])
settings = get_settings()

# No 0/O or 1/I, so a code read aloud or copied by hand still works.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def format_code(code: str | None) -> str | None:
    return f"{code[:4]}-{code[4:]}" if code else None


def new_invite_code(db: Session) -> str:
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not db.scalar(select(models.Space.id).where(models.Space.invite_code == code)):
            return code


def space_by_code(db: Session, code: str) -> models.Space | None:
    normalized = normalize_code(code)
    if len(normalized) != CODE_LENGTH:
        return None
    space = db.scalar(select(models.Space).where(models.Space.invite_code == normalized))
    return space if space and not space.is_deleted else None


def owner_ids(db: Session) -> set[int]:
    return set(db.scalars(select(models.User.id).where(models.User.is_owner.is_(True),
                                                       models.User.status == "active")))


def grant_owner_access(db: Session, space_ids: list[int] | None = None) -> None:
    """The server owner is a member of every space and channel, so they see all of them live."""
    owners = owner_ids(db)
    if not owners:
        return
    if space_ids is None:
        space_ids = list(db.scalars(select(models.Space.id).where(models.Space.is_deleted.is_(False))))
    if not space_ids:
        return
    have_space = set(db.execute(select(models.SpaceMember.space_id, models.SpaceMember.user_id).where(
        models.SpaceMember.space_id.in_(space_ids), models.SpaceMember.user_id.in_(owners))).all())
    channels = list(db.execute(select(models.Channel.id, models.Channel.space_id).where(
        models.Channel.space_id.in_(space_ids), models.Channel.is_deleted.is_(False))).all())
    have_channel = set(db.execute(select(models.ChannelMember.channel_id, models.ChannelMember.user_id).where(
        models.ChannelMember.channel_id.in_([c[0] for c in channels] or [0]),
        models.ChannelMember.user_id.in_(owners))).all())
    for owner in owners:
        for space_id in space_ids:
            if (space_id, owner) not in have_space:
                db.add(models.SpaceMember(space_id=space_id, user_id=owner))
        for channel_id, _ in channels:
            if (channel_id, owner) not in have_channel:
                db.add(models.ChannelMember(channel_id=channel_id, user_id=owner))
    db.flush()


def join_space(db: Session, space: models.Space, user_id: int) -> bool:
    """Returns False when the user was already a member."""
    if db.get(models.SpaceMember, {"space_id": space.id, "user_id": user_id}):
        return False
    db.add(models.SpaceMember(space_id=space.id, user_id=user_id))
    db.flush()
    grant_public_channels(db, space.id, user_id)
    return True


def space_out(db: Session, user: models.User, space: models.Space) -> schemas.SpaceOut:
    manage = can_manage_space(db, user, space.id)
    if manage and not space.invite_code:
        space.invite_code = new_invite_code(db)
        db.commit()
    count = db.scalar(select(func.count()).select_from(models.SpaceMember).join(
        models.User, models.User.id == models.SpaceMember.user_id).where(
        models.SpaceMember.space_id == space.id, models.User.is_owner.is_(False))) or 0
    return schemas.SpaceOut(id=space.id, name=space.name, can_manage=manage, is_mine=space.created_by == user.id,
                            member_count=count, invite_code=format_code(space.invite_code) if manage else None)


def _publish_space(db: Session, space: models.Space, kind: str, extra: dict | None = None) -> None:
    hub.publish(space_member_ids(db, space.id), {"type": kind, "space_id": space.id, **(extra or {})})


@router.get("/spaces", response_model=list[schemas.SpaceOut])
def list_spaces(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    if user.is_owner:
        grant_owner_access(db)
        db.commit()
    rows = list(db.scalars(
        select(models.Space)
        .join(models.SpaceMember, models.SpaceMember.space_id == models.Space.id)
        .where(models.SpaceMember.user_id == user.id, models.Space.is_deleted.is_(False))
        .order_by(models.Space.id)
    ))
    return [space_out(db, user, space) for space in rows]


@router.post("/spaces", response_model=schemas.SpaceOut)
def create_space(payload: schemas.SpaceCreateIn, request: Request, actor: models.User = Depends(current_user),
                 db: Session = Depends(get_db)):
    rate_limit(f"space:{actor.id}", 10, 3600)
    if not actor.is_owner:
        mine = db.scalar(select(func.count()).select_from(models.Space).where(
            models.Space.created_by == actor.id, models.Space.is_deleted.is_(False))) or 0
        if mine >= settings.max_spaces_per_user:
            raise HTTPException(status_code=409, detail="Слишком много своих пространств, удалите ненужные")
    space = models.Space(name=payload.name.strip(), created_by=actor.id, invite_code=new_invite_code(db))
    db.add(space)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Пространство с таким названием уже есть, выберите другое") from exc
    db.add(models.SpaceMember(space_id=space.id, user_id=actor.id))
    voice = models.Channel(space_id=space.id, name="Общий", type="voice", created_by=actor.id, position=1)
    text = models.Channel(space_id=space.id, name="чат", type="text", created_by=actor.id, position=1)
    db.add_all([voice, text])
    db.flush()
    db.add_all([models.ChannelMember(channel_id=voice.id, user_id=actor.id),
                models.ChannelMember(channel_id=text.id, user_id=actor.id)])
    db.flush()
    grant_owner_access(db, [space.id])
    write_audit(db, actor_id=actor.id, action="spaces.create", target_type="space", target_id=space.id, request=request)
    db.commit()
    return space_out(db, actor, space)


@router.post("/spaces/join", response_model=schemas.SpaceOut)
def join_by_code(payload: schemas.SpaceJoinIn, request: Request, user: models.User = Depends(current_user),
                 db: Session = Depends(get_db)):
    rate_limit(f"space-join:{user.id}", 20, 3600)
    space = space_by_code(db, payload.code)
    if not space:
        raise HTTPException(status_code=404, detail="Код приглашения не подходит")
    if join_space(db, space, user.id):
        write_audit(db, actor_id=user.id, action="spaces.join", target_type="space", target_id=space.id,
                    request=request)
        db.commit()
        _publish_space(db, space, "space.members", {"user_id": user.id})
    return space_out(db, user, space)


@router.post("/spaces/{space_id}/invite-code", response_model=schemas.SpaceOut)
def rotate_invite_code(space_id: int, request: Request, user: models.User = Depends(current_user),
                       db: Session = Depends(get_db)):
    space = db.get(models.Space, space_id)
    if not space or space.is_deleted:
        raise HTTPException(status_code=404, detail="Пространство не найдено")
    require_space_manager(db, user, space_id)
    space.invite_code = new_invite_code(db)
    write_audit(db, actor_id=user.id, action="spaces.invite_code", target_type="space", target_id=space_id,
                request=request)
    db.commit()
    return space_out(db, user, space)


async def _drop_voice_in(db: Session, space_id: int, user_id: int | None = None) -> list[tuple[int, int]]:
    query = select(models.VoiceState).join(models.Channel, models.Channel.id == models.VoiceState.channel_id).where(
        models.Channel.space_id == space_id)
    if user_id is not None:
        query = query.where(models.VoiceState.user_id == user_id)
    return [leave_voice(db, state) for state in list(db.scalars(query))]


@router.delete("/spaces/{space_id}/members/me")
async def leave_space(space_id: int, request: Request, user: models.User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict[str, str]:
    space = db.get(models.Space, space_id)
    if not space or space.is_deleted or not db.get(models.SpaceMember, {"space_id": space_id, "user_id": user.id}):
        raise HTTPException(status_code=404, detail="Пространство не найдено")
    if space.created_by == user.id:
        raise HTTPException(status_code=400, detail="Создатель не может выйти из своего пространства, его можно удалить")
    if user.is_owner:
        raise HTTPException(status_code=400, detail="Владелец сервера видит все пространства")
    gone = await _drop_voice_in(db, space_id, user.id)
    channel_ids = select(models.Channel.id).where(models.Channel.space_id == space_id)
    db.query(models.ChannelMember).filter(models.ChannelMember.user_id == user.id,
                                          models.ChannelMember.channel_id.in_(channel_ids)).delete(
        synchronize_session=False)
    db.query(models.SpaceMember).filter_by(space_id=space_id, user_id=user.id).delete()
    write_audit(db, actor_id=user.id, action="spaces.leave", target_type="space", target_id=space_id, request=request)
    db.commit()
    _publish_space(db, space, "space.members", {"user_id": user.id})
    for channel_id, member_id in gone:
        await media_sessions.remove_media_participant(channel_id, member_id)
    return {"status": "ok"}


@router.delete("/spaces/{space_id}")
async def delete_space(space_id: int, request: Request, user: models.User = Depends(current_user),
                       db: Session = Depends(get_db)) -> dict[str, str]:
    space = db.get(models.Space, space_id)
    if not space or space.is_deleted:
        raise HTTPException(status_code=404, detail="Пространство не найдено")
    require_space_manager(db, user, space_id, "spaces.delete")
    recipients = space_member_ids(db, space_id)
    gone = await _drop_voice_in(db, space_id)
    space.is_deleted = True
    space.invite_code = None
    space.name = f"{space.name[:80]} (удалено {space.id})"
    write_audit(db, actor_id=user.id, action="spaces.delete", target_type="space", target_id=space_id, request=request)
    db.commit()
    hub.publish(recipients, {"type": "space.deleted", "space_id": space_id})
    for channel_id, member_id in gone:
        await media_sessions.remove_media_participant(channel_id, member_id)
    return {"status": "ok"}


@router.post("/spaces/{space_id}/members")
def add_space_member(space_id: int, payload: schemas.MemberIn, request: Request,
                     actor: models.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, str]:
    space = db.get(models.Space, space_id)
    if not space or space.is_deleted or not db.get(models.User, payload.user_id):
        raise HTTPException(status_code=404, detail="Пространство или пользователь не найден")
    require_space_manager(db, actor, space_id, "spaces.members.manage")
    join_space(db, space, payload.user_id)
    write_audit(db, actor_id=actor.id, action="spaces.members.add", target_type="space", target_id=space_id,
                request=request, details={"user_id": payload.user_id})
    db.commit()
    return {"status": "ok"}
