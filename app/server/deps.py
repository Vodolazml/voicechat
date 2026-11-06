import hmac
from datetime import timezone

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import models
from .database import get_db
from .permissions import has_permission
from .security import decode_token, password_fingerprint


def user_from_token(db: Session, token: str) -> models.User:
    payload = decode_token(token)
    user = db.get(models.User, payload.user_id)
    if not user or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Учетная запись недоступна")
    if not hmac.compare_digest(payload.password_fingerprint, password_fingerprint(user.password_hash)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется повторный вход")
    session = db.get(models.LoginSession, payload.session_id) if payload.session_id else None
    if not session or session.user_id != user.id or session.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия завершена, войдите снова")
    return user


def token_session_id(token: str) -> int:
    return decode_token(token).session_id


def current_user(request: Request, authorization: str = Header(default=""), db: Session = Depends(get_db)) -> models.User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход")
    user = user_from_token(db, authorization.removeprefix("Bearer ").strip())
    temporary = db.get(models.TemporaryCredential, user.id) if user.must_change_password else None
    if temporary and temporary.expires_at.replace(tzinfo=timezone.utc) <= models.utcnow():
        raise HTTPException(status_code=401, detail="Временный пароль истёк")
    if user.must_change_password and request.url.path not in {"/me", "/me/password"}:
        raise HTTPException(status_code=403, detail="Сначала смените временный пароль")
    return user


def require_permission(permission: str):
    def dependency(user: models.User = Depends(current_user), db: Session = Depends(get_db)) -> models.User:
        if not has_permission(db, user.id, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
        return user

    return dependency


def ensure_channel_member(db: Session, user_id: int, channel_id: int) -> models.Channel:
    channel = db.get(models.Channel, channel_id)
    if not channel or channel.is_deleted:
        raise HTTPException(status_code=404, detail="Канал не найден")
    member = db.get(models.ChannelMember, {"channel_id": channel_id, "user_id": user_id})
    if not member:
        raise HTTPException(status_code=403, detail="Нет доступа к каналу")
    return channel


def channel_participant_ids(db: Session, channel_id: int) -> set[int]:
    """Active users who can see a channel (and therefore receive its events)."""
    return set(
        db.scalars(
            select(models.ChannelMember.user_id)
            .join(models.User, models.User.id == models.ChannelMember.user_id)
            .where(models.ChannelMember.channel_id == channel_id, models.User.status == "active")
        )
    )


# What only the space's creator (or the server owner / a global administrator) may do. Everything else that
# needs a manager is open to the space's moderators too.
CREATOR_ONLY = {"channels.create", "channels.delete", "spaces.delete", "spaces.avatar", "spaces.moderators"}


def is_space_admin(db: Session, user: models.User, space_id: int, permission: str = "spaces.update") -> bool:
    """The server owner, a global administrator, or the person who created the space."""
    if user.is_owner or has_permission(db, user.id, permission):
        return True
    space = db.get(models.Space, space_id)
    return bool(space and not space.is_deleted and space.created_by == user.id)


def is_space_moderator(db: Session, user_id: int, space_id: int) -> bool:
    return db.get(models.SpaceModerator, {"space_id": space_id, "user_id": user_id}) is not None


def space_role(db: Session, user: models.User, space_id: int) -> str:
    if is_space_admin(db, user, space_id):
        return "admin"
    return "moderator" if is_space_moderator(db, user.id, space_id) else "member"


def can_manage_space(db: Session, user: models.User, space_id: int, permission: str = "spaces.update") -> bool:
    """Admins of the space always; its moderators for everything except the creator-only actions."""
    if is_space_admin(db, user, space_id, permission):
        return True
    return permission not in CREATOR_ONLY and is_space_moderator(db, user.id, space_id)


def require_space_manager(db: Session, user: models.User, space_id: int, permission: str = "spaces.update") -> None:
    if not can_manage_space(db, user, space_id, permission):
        detail = ("Это может только создатель пространства" if permission in CREATOR_ONLY
                  else "Это могут создатель пространства и модераторы")
        raise HTTPException(status_code=403, detail=detail)
