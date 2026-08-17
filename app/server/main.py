from collections import defaultdict, deque
from datetime import timedelta
import json
from time import monotonic

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from .config import get_settings
from . import models, schemas
from .audit import write_audit
from .database import Base, SessionLocal, engine, get_db
from ..media_crypto import ENCRYPTED_FRAME_PREFIX
from .deps import current_user, ensure_channel_member, require_permission, user_from_token
from .permissions import seed_permissions, has_permission, user_permissions
from .security import create_token, encode_secret, hash_password, media_key_for_channel, media_key_id, validate_password_strength, verify_password
from .screen_relay import screen_relay
from .voice_relay import voice_relay


settings = get_settings()
SCREEN_FRAME_LIMIT_BYTES = 2_800_000
SCREEN_ALLOWED_VIEWER_INTERVALS_MS = {17, 33, 67, 100, 200, 500}
MAX_WS_TEXT_CHARS = 2048

app = FastAPI(title=settings.app_name)
if settings.allowed_hosts and "*" not in settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
    )
_rate_limits: dict[str, deque[float]] = defaultdict(deque)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            body_size = int(content_length)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Некорректный размер запроса"})
        if body_size > settings.max_http_body_bytes:
            return JSONResponse(status_code=413, content={"detail": "Запрос слишком большой"})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), display-capture=()")
    response.headers.setdefault("Cache-Control", "no-store")
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def rate_limit(key: str, limit: int, window_seconds: int) -> None:
    now = monotonic()
    bucket = _rate_limits[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(status_code=429, detail="Слишком много действий. Попробуйте позже.")
    bucket.append(now)


def websocket_origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    return "*" in settings.cors_origins or origin in settings.cors_origins


def websocket_token(websocket: WebSocket, query_token: str) -> str:
    authorization = websocket.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return query_token.strip()


def seed_database() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_permissions(db)
        db.query(models.VoiceState).delete()
        admin = db.scalar(select(models.User).where(models.User.username == "admin"))
        admin_role = db.scalar(select(models.Role).where(models.Role.name == "Owner/System Admin"))
        if not admin:
            admin = models.User(
                username="admin",
                display_name="Администратор",
                password_hash=hash_password(settings.bootstrap_password),
                must_change_password=True,
            )
            db.add(admin)
            db.flush()
            if admin_role:
                db.add(models.UserRole(user_id=admin.id, role_id=admin_role.id))
            db.add(models.Space(name="Основное пространство", created_by=admin.id))
            db.flush()
            space = db.scalar(select(models.Space).where(models.Space.name == "Основное пространство"))
            if space:
                db.add(models.SpaceMember(space_id=space.id, user_id=admin.id))
                lobby = models.Channel(space_id=space.id, name="Лобби", type="voice", created_by=admin.id)
                planning = models.Channel(space_id=space.id, name="Планирование", type="voice", created_by=admin.id)
                news = models.Channel(space_id=space.id, name="Новости", type="text", created_by=admin.id)
                db.add_all([lobby, planning, news])
                db.flush()
                for channel in (lobby, planning, news):
                    db.add(models.ChannelMember(channel_id=channel.id, user_id=admin.id))
            write_audit(
                db,
                actor_id=None,
                action="system.bootstrap",
                target_type="user",
                target_id=admin.id,
                details={"username": "admin"},
            )
        db.commit()


@app.on_event("startup")
def on_startup() -> None:
    seed_database()


@app.get("/health", tags=["service"])
def health() -> dict[str, str]:
    return {"status": "ok", "encoding": "utf-8"}


@app.post("/auth/login", response_model=schemas.TokenOut, tags=["auth"])
def login(payload: schemas.LoginIn, request: Request, db: Session = Depends(get_db)) -> schemas.TokenOut:
    username = payload.username.strip()
    rate_limit(f"login:{request.client.host if request.client else 'local'}:{username}", 5, 300)
    user = db.scalar(select(models.User).where(models.User.username == username))
    if not user or user.status != "active" or not verify_password(payload.password, user.password_hash):
        write_audit(
            db,
            actor_id=user.id if user else None,
            action="auth.login_failed",
            target_type="user",
            target_id=payload.username,
            request=request,
            result="failed",
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    write_audit(db, actor_id=user.id, action="auth.login", target_type="user", target_id=user.id, request=request)
    db.commit()
    return schemas.TokenOut(access_token=create_token(user.id, user.password_hash), must_change_password=user.must_change_password)


@app.get("/me", response_model=schemas.MeOut, tags=["auth"])
def me(user: models.User = Depends(current_user), db: Session = Depends(get_db)) -> schemas.MeOut:
    return schemas.MeOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        must_change_password=user.must_change_password,
        permissions=sorted(user_permissions(db, user.id)),
    )


@app.post("/me/password", tags=["auth"])
def change_password(
    payload: schemas.PasswordChangeIn,
    request: Request,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    rate_limit(f"password:{user.id}", 5, 300)
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Старый пароль указан неверно")
    validate_password_strength(payload.new_password)
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    write_audit(db, actor_id=user.id, action="auth.password_changed", target_type="user", target_id=user.id, request=request)
    db.commit()
    return {"status": "ok"}


@app.get("/users", response_model=list[schemas.UserOut], tags=["admin"])
def list_users(
    _: models.User = Depends(require_permission("users.create")),
    db: Session = Depends(get_db),
) -> list[models.User]:
    return list(db.scalars(select(models.User).where(models.User.status != "deleted").order_by(models.User.username)))


@app.post("/users", response_model=schemas.UserOut, tags=["admin"])
def create_user(
    payload: schemas.UserCreateIn,
    request: Request,
    actor: models.User = Depends(require_permission("users.create")),
    db: Session = Depends(get_db),
) -> models.User:
    validate_password_strength(payload.temporary_password)
    role_name = "Owner/System Admin" if payload.is_admin else "User"
    role = db.scalar(select(models.Role).where(models.Role.name == role_name))
    user = models.User(
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.temporary_password),
        must_change_password=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Пользователь с таким логином уже существует") from exc
    if role:
        db.add(models.UserRole(user_id=user.id, role_id=role.id))
    write_audit(db, actor_id=actor.id, action="users.create", target_type="user", target_id=user.id, request=request)
    db.commit()
    return user


@app.get("/spaces", response_model=list[schemas.SpaceOut], tags=["spaces"])
def list_spaces(user: models.User = Depends(current_user), db: Session = Depends(get_db)) -> list[models.Space]:
    rows = db.scalars(
        select(models.Space)
        .join(models.SpaceMember, models.SpaceMember.space_id == models.Space.id)
        .where(models.SpaceMember.user_id == user.id, models.Space.is_deleted.is_(False))
        .order_by(models.Space.name)
    )
    return list(rows)


@app.post("/spaces", response_model=schemas.SpaceOut, tags=["spaces"])
def create_space(
    payload: schemas.SpaceCreateIn,
    request: Request,
    actor: models.User = Depends(require_permission("spaces.create")),
    db: Session = Depends(get_db),
) -> models.Space:
    space = models.Space(name=payload.name, created_by=actor.id)
    db.add(space)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Пространство с таким названием уже существует") from exc
    db.add(models.SpaceMember(space_id=space.id, user_id=actor.id))
    write_audit(db, actor_id=actor.id, action="spaces.create", target_type="space", target_id=space.id, request=request)
    db.commit()
    return space


@app.post("/spaces/{space_id}/members", tags=["spaces"])
def add_space_member(
    space_id: int,
    payload: schemas.MemberIn,
    request: Request,
    actor: models.User = Depends(require_permission("spaces.members.manage")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if not db.get(models.Space, space_id) or not db.get(models.User, payload.user_id):
        raise HTTPException(status_code=404, detail="Пространство или пользователь не найден")
    if not db.get(models.SpaceMember, {"space_id": space_id, "user_id": payload.user_id}):
        db.add(models.SpaceMember(space_id=space_id, user_id=payload.user_id))
    write_audit(
        db,
        actor_id=actor.id,
        action="spaces.members.add",
        target_type="space",
        target_id=space_id,
        request=request,
        details={"user_id": payload.user_id},
    )
    db.commit()
    return {"status": "ok"}


@app.get("/spaces/{space_id}/channels", response_model=list[schemas.ChannelOut], tags=["channels"])
def list_channels(space_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)) -> list[models.Channel]:
    if not db.get(models.SpaceMember, {"space_id": space_id, "user_id": user.id}):
        raise HTTPException(status_code=403, detail="Нет доступа к пространству")
    rows = db.scalars(
        select(models.Channel)
        .join(models.ChannelMember, models.ChannelMember.channel_id == models.Channel.id)
        .where(
            models.Channel.space_id == space_id,
            models.ChannelMember.user_id == user.id,
            models.Channel.is_deleted.is_(False),
        )
        .order_by(models.Channel.type, models.Channel.name)
    )
    return list(rows)


@app.get("/channels/{channel_id}/media-key", response_model=schemas.MediaKeyOut, tags=["security"])
def channel_media_key(
    channel_id: int,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
) -> schemas.MediaKeyOut:
    channel = ensure_channel_member(db, user.id, channel_id)
    if channel.type != "voice":
        raise HTTPException(status_code=400, detail="Медиа-ключ доступен только для голосового канала")
    if not (
        has_permission(db, user.id, "voice.join")
        or has_permission(db, user.id, "screen_share.view")
        or has_permission(db, user.id, "screen_share.start")
    ):
        raise HTTPException(status_code=403, detail="Нет права получать медиа-ключ")
    return schemas.MediaKeyOut(
        key_id=media_key_id(channel_id),
        key=encode_secret(media_key_for_channel(channel_id)),
    )


@app.post("/channels", response_model=schemas.ChannelOut, tags=["channels"])
def create_channel(
    payload: schemas.ChannelCreateIn,
    request: Request,
    actor: models.User = Depends(require_permission("channels.create")),
    db: Session = Depends(get_db),
) -> models.Channel:
    if not db.get(models.SpaceMember, {"space_id": payload.space_id, "user_id": actor.id}):
        raise HTTPException(status_code=403, detail="Нет доступа к пространству")
    channel = models.Channel(space_id=payload.space_id, name=payload.name, type=payload.type, created_by=actor.id)
    db.add(channel)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Канал с таким названием уже существует") from exc
    db.add(models.ChannelMember(channel_id=channel.id, user_id=actor.id))
    write_audit(db, actor_id=actor.id, action="channels.create", target_type="channel", target_id=channel.id, request=request)
    db.commit()
    return channel


@app.post("/channels/{channel_id}/members", tags=["channels"])
def add_channel_member(
    channel_id: int,
    payload: schemas.MemberIn,
    request: Request,
    actor: models.User = Depends(require_permission("channels.members.manage")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    channel = db.get(models.Channel, channel_id)
    user = db.get(models.User, payload.user_id)
    if not channel or not user:
        raise HTTPException(status_code=404, detail="Канал или пользователь не найден")
    if not db.get(models.SpaceMember, {"space_id": channel.space_id, "user_id": payload.user_id}):
        db.add(models.SpaceMember(space_id=channel.space_id, user_id=payload.user_id))
    if not db.get(models.ChannelMember, {"channel_id": channel_id, "user_id": payload.user_id}):
        db.add(models.ChannelMember(channel_id=channel_id, user_id=payload.user_id))
    write_audit(
        db,
        actor_id=actor.id,
        action="channels.members.add",
        target_type="channel",
        target_id=channel_id,
        request=request,
        details={"user_id": payload.user_id},
    )
    db.commit()
    return {"status": "ok"}


@app.post("/channels/{channel_id}/connect", response_model=schemas.VoiceStateOut, tags=["voice"])
def connect_channel(
    channel_id: int,
    payload: schemas.VoiceJoinIn,
    request: Request,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
) -> schemas.VoiceStateOut:
    rate_limit(f"voice:{user.id}", 30, 60)
    channel = ensure_channel_member(db, user.id, channel_id)
    if channel.type != "voice":
        raise HTTPException(status_code=400, detail="Подключаться можно только к голосовому каналу")
    if not has_permission(db, user.id, "voice.join"):
        raise HTTPException(status_code=403, detail="Нет права подключаться к голосовым каналам")
    existing = db.get(models.VoiceState, user.id)
    if existing:
        existing.channel_id = channel_id
        existing.muted = payload.muted
        existing.deafened = payload.deafened
        existing.speaking = False
        existing.status = "connected"
        existing.updated_at = models.utcnow()
        state = existing
    else:
        state = models.VoiceState(user_id=user.id, channel_id=channel_id, muted=payload.muted, deafened=payload.deafened)
        db.add(state)
    write_audit(db, actor_id=user.id, action="voice.connect", target_type="channel", target_id=channel_id, request=request)
    db.commit()
    return voice_state_out(db, state)


@app.put("/channels/{channel_id}/voice-state", response_model=schemas.VoiceStateOut, tags=["voice"])
def update_voice_state(
    channel_id: int,
    payload: schemas.VoiceStateIn,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
) -> schemas.VoiceStateOut:
    state = db.get(models.VoiceState, user.id)
    if not state or state.channel_id != channel_id:
        raise HTTPException(status_code=404, detail="Вы не подключены к этому каналу")
    state.muted = payload.muted
    state.deafened = payload.deafened
    state.speaking = payload.speaking
    state.status = payload.status
    state.updated_at = models.utcnow()
    db.commit()
    return voice_state_out(db, state)


@app.post("/channels/{channel_id}/disconnect", tags=["voice"])
def disconnect_channel(
    channel_id: int,
    request: Request,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    state = db.get(models.VoiceState, user.id)
    if state and state.channel_id == channel_id:
        db.delete(state)
        write_audit(db, actor_id=user.id, action="voice.disconnect", target_type="channel", target_id=channel_id, request=request)
        db.commit()
    return {"status": "ok"}


@app.get("/channels/{channel_id}/voice", response_model=list[schemas.VoiceStateOut], tags=["voice"])
def voice_states(
    channel_id: int,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[schemas.VoiceStateOut]:
    ensure_channel_member(db, user.id, channel_id)
    cutoff = models.utcnow() - timedelta(seconds=45)
    stale_rows = list(db.scalars(select(models.VoiceState).where(models.VoiceState.updated_at < cutoff)))
    for stale in stale_rows:
        db.delete(stale)
    if stale_rows:
        db.commit()
    rows = db.scalars(select(models.VoiceState).where(models.VoiceState.channel_id == channel_id).order_by(models.VoiceState.user_id))
    return [voice_state_out(db, row) for row in rows]


@app.get("/audit", response_model=list[schemas.AuditOut], tags=["admin"])
def audit_log(
    _: models.User = Depends(require_permission("audit.view")),
    db: Session = Depends(get_db),
) -> list[models.AuditLog]:
    return list(db.scalars(select(models.AuditLog).order_by(models.AuditLog.id.desc()).limit(100)))


def voice_state_out(db: Session, state: models.VoiceState) -> schemas.VoiceStateOut:
    user = db.get(models.User, state.user_id)
    return schemas.VoiceStateOut(
        user_id=state.user_id,
        display_name=user.display_name if user else f"Пользователь {state.user_id}",
        channel_id=state.channel_id,
        muted=state.muted,
        deafened=state.deafened,
        speaking=state.speaking,
        status=state.status,
    )


@app.websocket("/voice/ws/{channel_id}")
async def voice_websocket(websocket: WebSocket, channel_id: int, token: str = "") -> None:
    if not websocket_origin_allowed(websocket):
        await websocket.close(code=1008, reason="bad origin")
        return

    with SessionLocal() as db:
        try:
            user = user_from_token(db, websocket_token(websocket, token))
        except HTTPException:
            await websocket.close(code=1008, reason="auth required")
            return
        user_id = user.id
        channel = db.get(models.Channel, channel_id)
        if not channel or channel.is_deleted or channel.type != "voice":
            await websocket.close(code=1008, reason="bad channel")
            return
        if not db.get(models.ChannelMember, {"channel_id": channel_id, "user_id": user.id}):
            await websocket.close(code=1008, reason="no channel access")
            return
        if not has_permission(db, user.id, "voice.join") or not has_permission(db, user.id, "voice.speak"):
            await websocket.close(code=1008, reason="no voice permission")
            return

    await websocket.accept()
    await voice_relay.join(channel_id, user_id, websocket)
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if not data:
                continue
            if len(data) > 4096:
                continue
            if not data.startswith(ENCRYPTED_FRAME_PREFIX):
                continue
            await voice_relay.broadcast_audio(channel_id, user_id, data)
    except WebSocketDisconnect:
        pass
    finally:
        await voice_relay.leave(channel_id, user_id)
        with SessionLocal() as db:
            state = db.get(models.VoiceState, user_id)
            if state and state.channel_id == channel_id:
                db.delete(state)
                db.commit()


@app.websocket("/screen/ws/{channel_id}")
async def screen_websocket(websocket: WebSocket, channel_id: int, token: str = "") -> None:
    if not websocket_origin_allowed(websocket):
        await websocket.close(code=1008, reason="bad origin")
        return

    can_send = False
    with SessionLocal() as db:
        try:
            user = user_from_token(db, websocket_token(websocket, token))
        except HTTPException:
            await websocket.close(code=1008, reason="auth required")
            return
        user_id = user.id
        channel = db.get(models.Channel, channel_id)
        if not channel or channel.is_deleted or channel.type != "voice":
            await websocket.close(code=1008, reason="bad channel")
            return
        if not db.get(models.ChannelMember, {"channel_id": channel_id, "user_id": user.id}):
            await websocket.close(code=1008, reason="no channel access")
            return
        can_view = has_permission(db, user.id, "screen_share.view")
        can_send = has_permission(db, user.id, "screen_share.start")
        if not can_view and not can_send:
            await websocket.close(code=1008, reason="no screen permission")
            return

    await websocket.accept()
    await screen_relay.join(channel_id, user_id, websocket)
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            text = message.get("text")
            if text:
                if len(text) > MAX_WS_TEXT_CHARS:
                    continue
                try:
                    control = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if control.get("type") == "viewer_settings":
                    interval_ms = control.get("fps_interval_ms")
                    if isinstance(interval_ms, int) and interval_ms in SCREEN_ALLOWED_VIEWER_INTERVALS_MS:
                        await screen_relay.set_viewer_interval(channel_id, payload.user_id, interval_ms)
                continue
            data = message.get("bytes")
            if data is None:
                continue
            if not can_send:
                continue
            if len(data) > SCREEN_FRAME_LIMIT_BYTES:
                continue
            if data != b"__STOP__" and not data.startswith(ENCRYPTED_FRAME_PREFIX):
                continue
            await screen_relay.broadcast_frame(channel_id, user_id, data)
    except WebSocketDisconnect:
        pass
    finally:
        if can_send:
            await screen_relay.broadcast_frame(channel_id, user_id, b"__STOP__")
        await screen_relay.leave(channel_id, user_id)
