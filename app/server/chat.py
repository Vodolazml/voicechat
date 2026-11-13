"""Text chat for every channel, including the chat inside a voice channel.

Messages are encrypted at rest with AES-256-GCM (key derived from VOICECHAT_SECRET_KEY, bound to the
message's channel), travel only over TLS, and are pushed to members through the event stream.
"""
from __future__ import annotations

import base64
import os
import re
from functools import lru_cache

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models, schemas
from .config import get_settings
from .database import get_db
from .deps import channel_participant_ids, current_user, ensure_channel_member
from .events import hub
from .limits import rate_limit
from .deps import can_manage_space
from .permissions import has_permission

router = APIRouter(tags=["chat"])
MENTION_RE = re.compile(r"@([A-Za-z0-9_.-]{3,64})")
CIPHER_PREFIX = "v1:"
EDIT_WINDOW_MESSAGES = 10_000


@lru_cache
def _message_key() -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"voicechat-chat-at-rest",
                info=b"messages-v1").derive(get_settings().secret_key.encode())


def seal(channel_id: int, text: str) -> str:
    nonce = os.urandom(12)
    sealed = AESGCM(_message_key()).encrypt(nonce, text.encode("utf-8"), f"channel:{channel_id}".encode())
    return CIPHER_PREFIX + base64.b64encode(nonce + sealed).decode()


def unseal(channel_id: int, stored: str) -> str:
    if not stored.startswith(CIPHER_PREFIX):
        return stored  # rows written before encryption at rest
    raw = base64.b64decode(stored[len(CIPHER_PREFIX):])
    try:
        return AESGCM(_message_key()).decrypt(raw[:12], raw[12:], f"channel:{channel_id}".encode()).decode("utf-8")
    except Exception:
        return "[сообщение не удалось расшифровать]"


def message_out(db: Session, message: models.Message, names: dict[int, str] | None = None) -> schemas.MessageOut:
    if names is not None and message.author_id in names:
        author = names[message.author_id]
    else:
        user = db.get(models.User, message.author_id)
        author = user.display_name if user else f"Пользователь {message.author_id}"
    body = "" if message.is_deleted else unseal(message.channel_id, message.body)
    attachment = None
    if message.attachment_id and not message.is_deleted:
        from .files import attachment_out

        row = db.get(models.Attachment, message.attachment_id)
        attachment = attachment_out(row) if row is not None and row.size > 0 else None
    return schemas.MessageOut(id=message.id, channel_id=message.channel_id, author_id=message.author_id,
                              author_name=author, body=body, created_at=message.created_at,
                              edited_at=message.edited_at, deleted=message.is_deleted,
                              mentions=_mentioned_ids(db, body), attachment=attachment)


def _mentioned_ids(db: Session, body: str) -> list[int]:
    names = set(MENTION_RE.findall(body))
    if not names:
        return []
    return sorted(db.scalars(select(models.User.id).where(models.User.username.in_(names))))


def _require(db: Session, user: models.User, permission: str) -> None:
    if not has_permission(db, user.id, permission):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def _publish(db: Session, channel_id: int, event: dict) -> None:
    hub.publish(channel_participant_ids(db, channel_id), event)


@router.get("/channels/{channel_id}/messages", response_model=list[schemas.MessageOut])
def list_messages(channel_id: int, before: int | None = Query(default=None, ge=1),
                  limit: int = Query(default=50, ge=1, le=100),
                  user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    ensure_channel_member(db, user.id, channel_id)
    _require(db, user, "text.read")
    query = select(models.Message).where(models.Message.channel_id == channel_id)
    if before:
        query = query.where(models.Message.id < before)
    rows = list(db.scalars(query.order_by(models.Message.id.desc()).limit(limit)))
    author_ids = {row.author_id for row in rows}
    names = dict(db.execute(select(models.User.id, models.User.display_name).where(
        models.User.id.in_(author_ids))).all()) if author_ids else {}
    return [message_out(db, row, names) for row in reversed(rows)]


@router.post("/channels/{channel_id}/messages", response_model=schemas.MessageOut)
def send_message(channel_id: int, payload: schemas.MessageCreateIn,
                 user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    ensure_channel_member(db, user.id, channel_id)
    _require(db, user, "text.send")
    rate_limit(f"chat:{user.id}", 20, 10)
    text = payload.body.strip()
    attachment = None
    if payload.attachment_id:
        attachment = db.get(models.Attachment, payload.attachment_id)
        already = db.scalar(select(models.Message.id).where(models.Message.attachment_id == payload.attachment_id))
        if (attachment is None or attachment.uploader_id != user.id or attachment.channel_id != channel_id
                or attachment.size == 0 or already):
            raise HTTPException(status_code=400, detail="Файл не найден, отправьте его ещё раз")
    if not text and attachment is None:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    message = models.Message(channel_id=channel_id, author_id=user.id, body=seal(channel_id, text),
                             created_at=models.utcnow(), attachment_id=attachment.id if attachment else None)
    db.add(message)
    db.flush()
    _mark_read(db, user.id, channel_id, message.id)
    db.commit()
    out = message_out(db, message)
    _publish(db, channel_id, {"type": "message.created", "message": out.model_dump(mode="json")})
    return out


@router.patch("/messages/{message_id}", response_model=schemas.MessageOut)
def edit_message(message_id: int, payload: schemas.MessageEditIn,
                 user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    message = db.get(models.Message, message_id)
    if not message or message.is_deleted:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    ensure_channel_member(db, user.id, message.channel_id)
    if message.author_id != user.id:
        raise HTTPException(status_code=403, detail="Можно исправлять только свои сообщения")
    _require(db, user, "text.edit_own")
    rate_limit(f"chat-edit:{user.id}", 30, 60)
    message.body = seal(message.channel_id, payload.body.strip())
    message.edited_at = models.utcnow()
    db.commit()
    out = message_out(db, message)
    _publish(db, message.channel_id, {"type": "message.updated", "message": out.model_dump(mode="json")})
    return out


@router.delete("/messages/{message_id}")
def delete_message(message_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    message = db.get(models.Message, message_id)
    if not message or message.is_deleted:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    channel = ensure_channel_member(db, user.id, message.channel_id)
    own = message.author_id == user.id and has_permission(db, user.id, "text.delete_own")
    if not own and not can_manage_space(db, user, channel.space_id, "text.delete_any"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    message.is_deleted = True
    message.body = ""
    if message.attachment_id:
        from .files import remove_file

        remove_file(db, message.attachment_id)
    db.commit()
    _publish(db, message.channel_id, {"type": "message.deleted", "channel_id": message.channel_id,
                                      "message_id": message_id})
    return {"status": "ok"}


def _mark_read(db: Session, user_id: int, channel_id: int, message_id: int) -> None:
    marker = db.get(models.ChannelRead, {"user_id": user_id, "channel_id": channel_id})
    if marker is None:
        db.add(models.ChannelRead(user_id=user_id, channel_id=channel_id, last_message_id=message_id))
    elif message_id > marker.last_message_id:
        marker.last_message_id = message_id


@router.post("/channels/{channel_id}/read")
def mark_read(channel_id: int, payload: schemas.ReadIn, user: models.User = Depends(current_user),
              db: Session = Depends(get_db)):
    ensure_channel_member(db, user.id, channel_id)
    _mark_read(db, user.id, channel_id, payload.message_id)
    db.commit()
    return {"status": "ok"}


@router.get("/spaces/{space_id}/unread", response_model=list[schemas.UnreadOut])
def unread(space_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """Unread counters for every channel of a space (capped at 99 per channel)."""
    if not db.get(models.SpaceMember, {"space_id": space_id, "user_id": user.id}):
        raise HTTPException(status_code=403, detail="Нет доступа к пространству")
    channels = db.scalars(
        select(models.Channel.id)
        .join(models.ChannelMember, models.ChannelMember.channel_id == models.Channel.id)
        .where(models.Channel.space_id == space_id, models.ChannelMember.user_id == user.id,
               models.Channel.is_deleted.is_(False)))
    result = []
    for channel_id in channels:
        marker = db.get(models.ChannelRead, {"user_id": user.id, "channel_id": channel_id})
        last = marker.last_message_id if marker else 0
        count = db.scalar(select(func.count()).select_from(
            select(models.Message.id).where(models.Message.channel_id == channel_id, models.Message.id > last,
                                            models.Message.is_deleted.is_(False),
                                            models.Message.author_id != user.id).limit(100).subquery())) or 0
        if count:
            result.append(schemas.UnreadOut(channel_id=channel_id, count=min(count, 99)))
    return result
