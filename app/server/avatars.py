"""Pictures: your own avatar (set in the profile) and a space's avatar (set by its creator).

The client shrinks the picture to 256×256 JPEG or PNG before sending, so it fits the normal request size
limit. The server only checks that the bytes really are a PNG or JPEG of sane size and never decodes them.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from . import models, schemas
from .audit import write_audit
from .database import get_db
from .deps import current_user, require_space_manager
from .events import hub
from .limits import rate_limit
from .presence import space_member_ids

router = APIRouter(tags=["avatars"])

MAX_AVATAR_BYTES = 60_000


def picture_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


async def _read_picture(request: Request) -> bytes | None:
    data = await request.body()
    if not data:
        return None  # empty body: remove the picture
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="Картинка слишком большая")
    if picture_type(data) is None:
        raise HTTPException(status_code=415, detail="Нужна картинка PNG или JPEG")
    return data


def _picture(data: bytes | None, version: int) -> Response:
    if not data:
        raise HTTPException(status_code=404, detail="Картинки нет")
    # The version is part of the URL, so the client may keep it as long as it likes.
    return Response(content=data, media_type=picture_type(data) or "application/octet-stream",
                    headers={"Cache-Control": "private, max-age=31536000, immutable", "ETag": f'"{version}"'})


def _friends_of(db: Session, user_id: int) -> set[int]:
    """Everyone who shares a space with this person, plus themselves."""
    spaces = select(models.SpaceMember.space_id).where(models.SpaceMember.user_id == user_id)
    return set(db.scalars(select(models.SpaceMember.user_id).where(models.SpaceMember.space_id.in_(spaces)))) | {user_id}


@router.put("/me/avatar")
async def set_my_avatar(request: Request, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(f"avatar:{user.id}", 20, 3600)
    data = await _read_picture(request)
    user.avatar = data
    user.avatar_version = (user.avatar_version or 0) + 1
    write_audit(db, actor_id=user.id, action="users.avatar", target_type="user", target_id=user.id, request=request)
    db.commit()
    hub.publish(_friends_of(db, user.id), {"type": "user.avatar", "user_id": user.id,
                                           "avatar_version": user.avatar_version if data else 0})
    return {"avatar_version": user.avatar_version if data else 0}


@router.patch("/me")
def update_profile(payload: schemas.ProfileIn, request: Request, user: models.User = Depends(current_user),
                   db: Session = Depends(get_db)):
    """Your own name, the one everybody sees."""
    rate_limit(f"profile:{user.id}", 10, 3600)
    name = " ".join(payload.display_name.split())
    if not name:
        raise HTTPException(status_code=422, detail="Имя не может быть пустым")
    user.display_name = name
    write_audit(db, actor_id=user.id, action="users.rename", target_type="user", target_id=user.id, request=request)
    db.commit()
    hub.publish(_friends_of(db, user.id), {"type": "user.renamed", "user_id": user.id, "display_name": name})
    return {"display_name": name}


@router.get("/avatars/user/{user_id}")
def user_avatar(user_id: int, _: models.User = Depends(current_user), db: Session = Depends(get_db)):
    user = db.scalar(select(models.User).options(undefer(models.User.avatar)).where(models.User.id == user_id))
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return _picture(user.avatar, user.avatar_version)


@router.put("/spaces/{space_id}/avatar")
async def set_space_avatar(space_id: int, request: Request, user: models.User = Depends(current_user),
                           db: Session = Depends(get_db)):
    space = db.get(models.Space, space_id)
    if not space or space.is_deleted:
        raise HTTPException(status_code=404, detail="Пространство не найдено")
    require_space_manager(db, user, space_id, "spaces.avatar")
    rate_limit(f"avatar:{user.id}", 20, 3600)
    data = await _read_picture(request)
    space.avatar = data
    space.avatar_version = (space.avatar_version or 0) + 1
    write_audit(db, actor_id=user.id, action="spaces.avatar", target_type="space", target_id=space_id,
                request=request)
    db.commit()
    version = space.avatar_version if data else 0
    hub.publish(space_member_ids(db, space_id), {"type": "space.updated", "space_id": space_id})
    return {"avatar_version": version}


@router.get("/avatars/space/{space_id}")
def space_avatar(space_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    if not db.get(models.SpaceMember, {"space_id": space_id, "user_id": user.id}):
        raise HTTPException(status_code=404, detail="Пространство не найдено")
    space = db.scalar(select(models.Space).options(undefer(models.Space.avatar)).where(models.Space.id == space_id))
    if not space or space.is_deleted:
        raise HTTPException(status_code=404, detail="Пространство не найдено")
    return _picture(space.avatar, space.avatar_version)
