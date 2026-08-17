import hmac

from fastapi import Depends, Header, HTTPException, WebSocket, status
from sqlalchemy.orm import Session
from . import models
from .database import get_db, SessionLocal
from .permissions import has_permission
from .security import decode_token, password_fingerprint


def user_from_token(db: Session, token: str) -> models.User:
    payload = decode_token(token)
    user = db.get(models.User, payload.user_id)
    if not user or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Учетная запись недоступна")
    if not hmac.compare_digest(payload.password_fingerprint, password_fingerprint(user.password_hash)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется повторный вход")
    return user


def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> models.User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход")
    return user_from_token(db, authorization.removeprefix("Bearer ").strip())


def require_permission(permission: str):
    def dependency(user: models.User = Depends(current_user), db: Session = Depends(get_db)) -> models.User:
        if not has_permission(db, user.id, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
        return user

    return dependency


def websocket_user(token: str) -> models.User:
    db = SessionLocal()
    try:
        return user_from_token(db, token)
    finally:
        db.close()


def ensure_channel_member(db: Session, user_id: int, channel_id: int) -> models.Channel:
    channel = db.get(models.Channel, channel_id)
    if not channel or channel.is_deleted:
        raise HTTPException(status_code=404, detail="Канал не найден")
    member = db.get(models.ChannelMember, {"channel_id": channel_id, "user_id": user_id})
    if not member:
        raise HTTPException(status_code=403, detail="Нет доступа к каналу")
    return channel


async def close_ws_policy_violation(ws: WebSocket, reason: str) -> None:
    await ws.close(code=1008, reason=reason)
