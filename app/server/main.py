from contextlib import asynccontextmanager
from datetime import timedelta
import asyncio
import logging
import secrets
from datetime import timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from .config import get_settings
from . import models, schemas
from .audit import write_audit
from .database import SessionLocal, engine, get_db, run_migrations
from .e2ee_state import e2ee_state
from ..e2ee_crypto import public_key_fingerprint
from .deps import channel_participant_ids, current_user, ensure_channel_member, require_permission, token_session_id
from .limits import _rate_limits, client_ip, rate_limit  # noqa: F401  (_rate_limits re-exported for tests)
from .permissions import seed_permissions, has_permission, user_permissions
from .security import (
    burn_password_check,
    create_token,
    hash_password,
    hash_secret_token,
    password_fingerprint,
    validate_password_strength,
    verify_password,
)
from .media_sessions import remove_media_participant
from .events import hub
from . import chat, download_page, presence, spaces
from .presence import grant_public_channels, leave_voice, voice_state_out  # noqa: F401
from .body_limit import BodyLimitMiddleware
from ..version import APP_VERSION


settings = get_settings()
logger = logging.getLogger("voicechat.server")

_docs_enabled = settings.enable_api_docs or not settings.is_production


@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed_database()
    janitor = asyncio.create_task(presence.presence_janitor())
    try:
        yield
    finally:
        janitor.cancel()


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
app.add_middleware(BodyLimitMiddleware, limit=settings.max_http_body_bytes)
app.mount("/downloads", StaticFiles(directory=settings.downloads_dir, check_dir=False), name="downloads")
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


app.include_router(presence.router)
app.include_router(chat.router)
app.include_router(spaces.router)
app.include_router(download_page.router)


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


def revoke_user_sessions(db: Session, user_id: int, *, keep_session_id: int | None = None) -> None:
    now = models.utcnow()
    rows = db.scalars(
        select(models.LoginSession).where(
            models.LoginSession.user_id == user_id, models.LoginSession.revoked_at.is_(None)
        )
    )
    for row in rows:
        if row.id != keep_session_id:
            row.revoked_at = now
            row.token_hash = None


def ensure_media_e2ee_access(db: Session, user_id: int, channel_id: int) -> models.Channel:
    channel = ensure_channel_member(db, user_id, channel_id)
    if channel.type != "voice":
        raise HTTPException(status_code=400, detail="E2EE доступен только для голосового канала")
    if not (
        has_permission(db, user_id, "voice.join")
        or has_permission(db, user_id, "screen_share.view")
        or has_permission(db, user_id, "screen_share.start")
    ):
        raise HTTPException(status_code=403, detail="Нет права на E2EE медиа")
    return channel


def bootstrap_owner_password() -> tuple[str, bool]:
    if settings.bootstrap_password:
        return settings.bootstrap_password, False
    return secrets.token_urlsafe(15) + "aA7!", True


def seed_database() -> None:
    run_migrations(engine)
    with SessionLocal() as db:
        seed_permissions(db)
        # Nobody is in a call right after a restart; LiveKit drops the rooms too.
        db.query(models.VoiceState).delete()
        admin = db.scalar(select(models.User).where(models.User.username == "admin"))
        admin_role = db.scalar(select(models.Role).where(models.Role.name == "Owner/System Admin"))
        if not admin:
            password, generated = bootstrap_owner_password()
            admin = models.User(
                username="admin",
                display_name="Администратор",
                password_hash=hash_password(password),
                must_change_password=True,
                is_owner=True,
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
            if generated:
                # Shown once; the owner must replace it at first sign-in.
                print(
                    "\n" + "=" * 64 + "\n"
                    f"  VOICECHAT FIRST START: login 'admin', one-time password: {password}\n"
                    "  You will be asked to change it at first sign-in.\n" + "=" * 64 + "\n",
                    flush=True,
                )
        db.commit()


@app.get("/health", tags=["service"])
def health() -> dict[str, str]:
    return {"status": "ok", "encoding": "utf-8"}


def version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for raw in value.split("."):
        try:
            parts.append(int(raw))
        except ValueError:
            break
    return tuple(parts or [0])


@app.get("/client/update", response_model=schemas.ClientUpdateOut, tags=["service"])
def client_update(version: str = APP_VERSION) -> schemas.ClientUpdateOut:
    latest = settings.client_latest_version
    update_available = (
        bool(settings.client_download_url)
        and bool(settings.client_download_sha256)
        and version_tuple(latest) > version_tuple(version)
    )
    return schemas.ClientUpdateOut(
        latest_version=latest,
        update_available=update_available,
        required=update_available and settings.client_update_required,
        download_url=settings.client_download_url if update_available else "",
        sha256=settings.client_download_sha256 if update_available else "",
        signature=settings.client_download_signature if update_available else "",
        release_notes_url=settings.client_release_notes_url if update_available else "",
    )


def _as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@app.post("/auth/login", response_model=schemas.TokenOut, tags=["auth"])
def login(payload: schemas.LoginIn, request: Request, db: Session = Depends(get_db)) -> schemas.TokenOut:
    username = payload.username.strip()
    ip = client_ip(request)
    rate_limit(f"login-ip:{ip}", 30, 600)
    rate_limit(f"login:{ip}:{hash_secret_token(username.lower())[:24]}", 5, 300)
    user = db.scalar(select(models.User).where(models.User.username == username))
    if user is None:
        burn_password_check(payload.password)
    if not user or user.status != "active" or not verify_password(payload.password, user.password_hash):
        write_audit(
            db,
            actor_id=user.id if user else None,
            action="auth.login_failed",
            target_type="user",
            target_id=username[:64],
            request=request,
            result="failed",
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    temporary = db.get(models.TemporaryCredential, user.id)
    if user.must_change_password and temporary and _as_utc(temporary.expires_at) <= models.utcnow():
        raise HTTPException(status_code=401, detail="Временный пароль истёк. Обратитесь к администратору")
    write_audit(db, actor_id=user.id, action="auth.login", target_type="user", target_id=user.id, request=request)
    return issue_session(db, user, request, payload.device_name)


def issue_session(db: Session, user: models.User, request: Request, device_name: str = "") -> schemas.TokenOut:
    """Create a device session. Accounts that still have a temporary password get no refresh token."""
    refresh = "" if user.must_change_password else secrets.token_urlsafe(48)
    now = models.utcnow()
    session = models.LoginSession(
        user_id=user.id,
        token_hash=hash_secret_token(refresh) if refresh else None,
        password_fingerprint=password_fingerprint(user.password_hash),
        device_name=device_name.strip()[:120],
        ip_address=client_ip(request),
        created_at=now,
        last_used_at=now,
        expires_at=now + (timedelta(days=settings.refresh_token_days) if refresh else timedelta(hours=1)),
    )
    db.add(session)
    db.commit()
    return schemas.TokenOut(
        access_token=create_token(user.id, user.password_hash, session.id),
        must_change_password=user.must_change_password,
        refresh_token=refresh,
    )


@app.post("/auth/refresh", response_model=schemas.TokenOut, tags=["auth"])
def refresh_session(payload: schemas.RefreshIn, request: Request, db: Session = Depends(get_db)):
    rate_limit(f"refresh:{client_ip(request)}", 120, 60)
    token_hash = hash_secret_token(payload.refresh_token)
    now = models.utcnow()
    session = db.scalar(select(models.LoginSession).where(models.LoginSession.token_hash == token_hash))
    if session is None:
        previous = db.scalar(
            select(models.LoginSession).where(
                models.LoginSession.previous_token_hash == token_hash,
                models.LoginSession.revoked_at.is_(None),
            )
        )
        if previous is None:
            raise HTTPException(status_code=401, detail="Требуется повторный вход")
        rotated_at = _as_utc(previous.rotated_at) if previous.rotated_at else None
        if rotated_at is None or (now - rotated_at).total_seconds() > settings.refresh_reuse_grace_seconds:
            # An old refresh token came back: someone copied it. Sign this user out everywhere.
            revoke_user_sessions(db, previous.user_id)
            write_audit(db, actor_id=previous.user_id, action="auth.refresh_reuse", target_type="session",
                        target_id=previous.id, request=request, result="blocked")
            db.commit()
            raise HTTPException(status_code=401, detail="Сессия отозвана из соображений безопасности, войдите снова")
        # The client lost the answer to its previous refresh (crash, timeout). Issue a fresh token again.
        session = previous
        keep_previous = True
    else:
        keep_previous = False
    if session.revoked_at is not None or _as_utc(session.expires_at) <= now:
        raise HTTPException(status_code=401, detail="Требуется повторный вход")
    user = db.get(models.User, session.user_id)
    if (
        not user
        or user.status != "active"
        or user.must_change_password
        or session.password_fingerprint != password_fingerprint(user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Сессия отозвана")
    new_token = secrets.token_urlsafe(48)
    if not keep_previous:
        session.previous_token_hash = session.token_hash
        session.rotated_at = now
    session.token_hash = hash_secret_token(new_token)
    session.last_used_at = now
    session.ip_address = client_ip(request)
    session.expires_at = now + timedelta(days=settings.refresh_token_days)
    db.commit()
    return schemas.TokenOut(
        access_token=create_token(user.id, user.password_hash, session.id),
        must_change_password=False,
        refresh_token=new_token,
    )


def _bearer_session_id(request: Request) -> int:
    return token_session_id(request.headers.get("authorization", "").removeprefix("Bearer ").strip())


@app.post("/auth/logout", tags=["auth"])
def logout(request: Request, user: models.User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, str]:
    session = db.get(models.LoginSession, _bearer_session_id(request))
    if session and session.user_id == user.id:
        session.revoked_at = models.utcnow()
        session.token_hash = None
    write_audit(db, actor_id=user.id, action="auth.logout", target_type="user", target_id=user.id, request=request)
    db.commit()
    return {"status": "ok"}


@app.get("/me/sessions", response_model=list[schemas.SessionOut], tags=["auth"])
def my_sessions(request: Request, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    current_id = _bearer_session_id(request)
    now = models.utcnow()
    rows = db.scalars(
        select(models.LoginSession)
        .where(models.LoginSession.user_id == user.id, models.LoginSession.revoked_at.is_(None))
        .order_by(models.LoginSession.last_used_at.desc())
    )
    return [
        schemas.SessionOut(
            id=row.id,
            device_name=row.device_name or "Неизвестное устройство",
            ip_address=row.ip_address,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            current=row.id == current_id,
        )
        for row in rows
        if _as_utc(row.expires_at) > now
    ]


@app.delete("/me/sessions/{session_id}", tags=["auth"])
def revoke_my_session(session_id: int, request: Request, user: models.User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict[str, str]:
    session = db.get(models.LoginSession, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    session.revoked_at = models.utcnow()
    session.token_hash = None
    write_audit(db, actor_id=user.id, action="auth.session_revoked", target_type="session", target_id=session_id,
                request=request)
    db.commit()
    return {"status": "ok"}


@app.post("/me/sessions/revoke-others", tags=["auth"])
def revoke_other_sessions(request: Request, user: models.User = Depends(current_user),
                          db: Session = Depends(get_db)) -> dict[str, str]:
    revoke_user_sessions(db, user.id, keep_session_id=_bearer_session_id(request))
    write_audit(db, actor_id=user.id, action="auth.sessions_revoked", target_type="user", target_id=user.id,
                request=request)
    db.commit()
    # Streams of the remaining device reconnect immediately; revoked ones fail their re-auth.
    hub.disconnect_user(user.id)
    return {"status": "ok"}


@app.get("/me", response_model=schemas.MeOut, tags=["auth"])
def me(user: models.User = Depends(current_user), db: Session = Depends(get_db)) -> schemas.MeOut:
    return schemas.MeOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        must_change_password=user.must_change_password,
        permissions=sorted(user_permissions(db, user.id)),
        is_owner=user.is_owner,
    )


@app.post("/me/password", tags=["auth"])
def change_password(
    payload: schemas.PasswordChangeIn,
    request: Request,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    rate_limit(f"password:{user.id}", 5, 300)
    if payload.new_password == payload.old_password:
        raise HTTPException(status_code=400, detail="Новый пароль должен отличаться от старого")
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Старый пароль указан неверно")
    validate_password_strength(payload.new_password)
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    temporary = db.get(models.TemporaryCredential, user.id)
    if temporary:
        db.delete(temporary)
    # Every device signs in again with the new password, including stolen ones.
    revoke_user_sessions(db, user.id)
    write_audit(db, actor_id=user.id, action="auth.password_changed", target_type="user", target_id=user.id, request=request)
    db.commit()
    hub.disconnect_user(user.id)
    return {"status": "ok"}


@app.get("/users", response_model=list[schemas.UserOut], tags=["admin"])
def list_users(
    _: models.User = Depends(require_permission("users.create")),
    db: Session = Depends(get_db),
) -> list[models.User]:
    return list(db.scalars(select(models.User).where(models.User.status != "deleted").order_by(models.User.username)))


@app.post("/users", response_model=schemas.UserCreatedOut, tags=["admin"])
def create_user(
    payload: schemas.UserCreateIn,
    request: Request,
    actor: models.User = Depends(require_permission("users.create")),
    db: Session = Depends(get_db),
) -> models.User:
    # Rate limiting для создания пользователей: максимум 10 в минуту с одного IP
    rate_limit(f"create_user:{client_ip(request)}", 10, 60)
    temporary_password = payload.temporary_password or (secrets.token_urlsafe(18) + "aA7!")
    validate_password_strength(temporary_password)
    if payload.is_admin and not has_permission(db, actor.id, 'roles.manage'):
        raise HTTPException(status_code=403, detail="Нет права назначать администратора")
    role_name = "Owner/System Admin" if payload.is_admin else "User"
    role = db.scalar(select(models.Role).where(models.Role.name == role_name))
    user = models.User(
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(temporary_password),
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
    db.add(models.TemporaryCredential(user_id=user.id, expires_at=models.utcnow() + timedelta(days=1)))
    write_audit(db, actor_id=actor.id, action="users.create", target_type="user", target_id=user.id, request=request)
    db.commit()
    return schemas.UserCreatedOut(**schemas.UserOut.model_validate(user).model_dump(), temporary_password=temporary_password)


@app.post('/users/{user_id}/reset-password', tags=['admin'])
async def reset_user_password(user_id: int, request: Request,
    actor: models.User = Depends(require_permission('users.reset_password')), db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if not user or user.id == actor.id:
        raise HTTPException(status_code=400, detail='Выберите другого пользователя')
    if user.is_owner:
        raise HTTPException(status_code=403, detail='Пароль владельца сервера может сменить только он сам')
    password = secrets.token_urlsafe(18) + 'aA7!'
    user.password_hash = hash_password(password)
    user.must_change_password = True
    temporary = db.get(models.TemporaryCredential, user_id)
    if temporary:
        temporary.expires_at = models.utcnow() + timedelta(days=1)
    else:
        db.add(models.TemporaryCredential(user_id=user_id, expires_at=models.utcnow() + timedelta(days=1)))
    state = db.get(models.VoiceState, user_id)
    channel_id = leave_voice(db, state)[0] if state else None
    revoke_user_sessions(db, user_id)
    write_audit(db, actor_id=actor.id, action='users.reset_password', target_type='user', target_id=user_id, request=request)
    db.commit()
    hub.disconnect_user(user_id)
    if channel_id:
        await remove_media_participant(channel_id, user_id)
    return {'temporary_password': password}


@app.post('/users/{user_id}/disable', tags=['admin'])
async def disable_user(user_id: int, request: Request,
    actor: models.User = Depends(require_permission('users.disable')), db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if not user or user.id == actor.id:
        raise HTTPException(status_code=400, detail='Нельзя заблокировать свою учётную запись')
    if user.is_owner:
        raise HTTPException(status_code=403, detail='Владельца сервера нельзя заблокировать')
    user.status = 'disabled'
    revoke_user_sessions(db, user_id)
    state = db.get(models.VoiceState, user_id)
    channel_id = leave_voice(db, state)[0] if state else None
    write_audit(db, actor_id=actor.id, action='users.disable', target_type='user', target_id=user_id, request=request)
    db.commit()
    hub.disconnect_user(user_id)
    if channel_id:
        await remove_media_participant(channel_id, user_id)
    return {'status': 'ok'}


def invite_out(invite: models.Invite) -> schemas.InviteOut:
    return schemas.InviteOut(
        id=invite.id,
        note=invite.note,
        is_admin=invite.is_admin,
        space_id=invite.space_id,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        used=invite.used_at is not None,
    )


@app.post("/invites", response_model=schemas.InviteCreatedOut, tags=["admin"])
def create_invite(
    payload: schemas.InviteCreateIn,
    request: Request,
    actor: models.User = Depends(require_permission("users.create")),
    db: Session = Depends(get_db),
) -> schemas.InviteCreatedOut:
    rate_limit(f"invite:{actor.id}", 20, 3600)
    if payload.is_admin and not has_permission(db, actor.id, "roles.manage"):
        raise HTTPException(status_code=403, detail="Нет права приглашать администраторов")
    space_id = payload.space_id
    if space_id is None:
        space_id = db.scalar(
            select(models.SpaceMember.space_id).where(models.SpaceMember.user_id == actor.id).order_by(
                models.SpaceMember.space_id)
        )
    elif not db.get(models.SpaceMember, {"space_id": space_id, "user_id": actor.id}):
        raise HTTPException(status_code=403, detail="Нет доступа к пространству")
    code = secrets.token_urlsafe(18)
    invite = models.Invite(
        code_hash=hash_secret_token(code),
        space_id=space_id,
        is_admin=payload.is_admin,
        note=payload.note.strip(),
        created_by=actor.id,
        created_at=models.utcnow(),
        expires_at=models.utcnow() + timedelta(hours=settings.invite_hours),
    )
    db.add(invite)
    db.flush()
    write_audit(db, actor_id=actor.id, action="invites.create", target_type="invite", target_id=invite.id,
                request=request)
    db.commit()
    return schemas.InviteCreatedOut(**invite_out(invite).model_dump(), code=code)


@app.get("/invites", response_model=list[schemas.InviteOut], tags=["admin"])
def list_invites(
    _: models.User = Depends(require_permission("users.create")),
    db: Session = Depends(get_db),
) -> list[schemas.InviteOut]:
    now = models.utcnow()
    rows = db.scalars(select(models.Invite).where(models.Invite.revoked.is_(False)).order_by(models.Invite.id.desc()))
    return [invite_out(row) for row in rows if row.used_at is not None or _as_utc(row.expires_at) > now][:100]


@app.delete("/invites/{invite_id}", tags=["admin"])
def revoke_invite(
    invite_id: int,
    request: Request,
    actor: models.User = Depends(require_permission("users.create")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    invite = db.get(models.Invite, invite_id)
    if not invite:
        raise HTTPException(status_code=404, detail="Приглашение не найдено")
    invite.revoked = True
    write_audit(db, actor_id=actor.id, action="invites.revoke", target_type="invite", target_id=invite_id,
                request=request)
    db.commit()
    return {"status": "ok"}


@app.post("/auth/register", response_model=schemas.TokenOut, tags=["auth"])
def register(payload: schemas.RegisterIn, request: Request, db: Session = Depends(get_db)):
    """Open sign-up, or sign-up with a one-time invite or a space's invite code (joins that space)."""
    rate_limit(f"register:{client_ip(request)}", 10, 3600)
    code = payload.code.strip()
    invite = None
    space = None
    if code:
        invite = db.scalar(select(models.Invite).where(models.Invite.code_hash == hash_secret_token(code)))
        if invite is None:
            space = spaces.space_by_code(db, code)
        if invite is not None and (
            invite.revoked or invite.used_at is not None or _as_utc(invite.expires_at) <= models.utcnow()
        ):
            invite = None
            raise HTTPException(status_code=400, detail="Приглашение недействительно или уже использовано")
        if invite is None and space is None:
            raise HTTPException(status_code=400, detail="Приглашение недействительно или уже использовано")
    elif not settings.open_registration:
        raise HTTPException(status_code=403, detail="Регистрация на этом сервере только по приглашению")
    validate_password_strength(payload.password)
    user = models.User(
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        must_change_password=False,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Такой логин уже занят") from exc
    role = db.scalar(select(models.Role).where(
        models.Role.name == ("Owner/System Admin" if invite and invite.is_admin else "User")))
    if role:
        db.add(models.UserRole(user_id=user.id, role_id=role.id))
    if invite and invite.space_id:
        space = db.get(models.Space, invite.space_id)
    if space and not space.is_deleted:
        spaces.join_space(db, space, user.id)
    if invite:
        invite.used_at = models.utcnow()
        invite.used_by = user.id
    write_audit(db, actor_id=user.id, action="auth.register", target_type="invite" if invite else "space",
                target_id=invite.id if invite else (space.id if space else 0), request=request)
    db.commit()
    if space:
        hub.publish(presence.space_member_ids(db, space.id), {"type": "space.members", "space_id": space.id,
                                                              "user_id": user.id})
    return issue_session(db, user, request, payload.device_name)


@app.put("/me/device-key", tags=["security"])
def set_device_key(
    payload: schemas.DeviceKeyIn,
    user: models.User = Depends(current_user),
) -> dict[str, str]:
    try:
        expected_fingerprint = public_key_fingerprint(payload.public_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Некорректный public key") from exc
    if expected_fingerprint != payload.fingerprint:
        raise HTTPException(status_code=400, detail="Отпечаток public key не совпадает")
    e2ee_state.set_public_key(user.id, payload.public_key, payload.fingerprint)
    return {"status": "ok"}


@app.get("/channels/{channel_id}/e2ee", response_model=schemas.E2EEStateOut, tags=["security"])
def channel_e2ee_state(
    channel_id: int,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    ensure_media_e2ee_access(db, user.id, channel_id)
    return e2ee_state.channel_state(channel_id, channel_participant_ids(db, channel_id), user.id)


@app.put("/channels/{channel_id}/e2ee/sender-key", tags=["security"])
def publish_sender_key(
    channel_id: int,
    payload: schemas.SenderKeyIn,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    ensure_media_e2ee_access(db, user.id, channel_id)
    recipients = channel_participant_ids(db, channel_id)
    e2ee_state.set_sender_key(channel_id, user.id, payload.key_id, payload.envelopes, recipients)
    # Friends fetch the new key right away instead of polling for it.
    hub.publish(recipients - {user.id}, {"type": "e2ee.updated", "channel_id": channel_id, "user_id": user.id})
    return {"status": "ok"}


@app.get("/audit", response_model=list[schemas.AuditOut], tags=["admin"])
def audit_log(
    _: models.User = Depends(require_permission("audit.view")),
    db: Session = Depends(get_db),
) -> list[models.AuditLog]:
    return list(db.scalars(select(models.AuditLog).order_by(models.AuditLog.id.desc()).limit(100)))
