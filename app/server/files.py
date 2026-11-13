"""Files sent in chat: any type, up to max_file_mb.

The client uploads the raw bytes with PUT /channels/{id}/files?name=..., then sends a message that points
at the returned attachment. On disk every file is AES-256-GCM encrypted with a key derived from the server
secret and bound to the file's id; its name is encrypted in the database like message text. Files that were
uploaded but never attached to a message are removed after a day; a deleted message removes its file.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, schemas
from .chat import seal, unseal
from .config import get_settings
from .database import get_db
from .deps import current_user, ensure_channel_member
from .limits import rate_limit
from .permissions import has_permission

logger = logging.getLogger("voicechat.files")
router = APIRouter(tags=["chat"])
UPLOAD_PATH = re.compile(r"^/channels/\d+/files$")
ORPHAN_HOURS = 24
_UNSAFE_NAME = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')


@lru_cache
def _files_key() -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"voicechat-files-at-rest",
                info=b"files-v1").derive(get_settings().secret_key.encode())


def max_file_bytes() -> int:
    return get_settings().max_file_mb * 1024 * 1024


def files_dir() -> Path:
    path = Path(get_settings().files_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(attachment_id: int) -> Path:
    return files_dir() / f"{attachment_id}.bin"


def clean_name(name: str) -> str:
    name = _UNSAFE_NAME.sub("_", name).strip(" .") or "файл"
    if len(name) > 180:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:170] + dot + ext[:9]) if dot and len(ext) < 10 else name[:180]
    return name


def attachment_out(attachment: models.Attachment) -> schemas.AttachmentOut:
    return schemas.AttachmentOut(id=attachment.id, name=unseal(attachment.channel_id, attachment.name),
                                 size=attachment.size, content_type=attachment.content_type)


def remove_file(db: Session, attachment_id: int | None) -> None:
    if not attachment_id:
        return
    attachment = db.get(models.Attachment, attachment_id)
    try:
        _path(attachment_id).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete file %s", attachment_id, exc_info=True)
    if attachment is not None:
        attachment.size = 0


@router.put("/channels/{channel_id}/files", response_model=schemas.AttachmentOut)
async def upload_file(channel_id: int, request: Request, name: str = Query(min_length=1, max_length=255),
                      user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    ensure_channel_member(db, user.id, channel_id)
    if not has_permission(db, user.id, "text.send"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    rate_limit(f"upload:{user.id}", 20, 600)
    settings = get_settings()
    declared = int(request.headers.get("content-length") or 0)
    if declared > max_file_bytes():
        raise HTTPException(status_code=413, detail=f"Файл больше {settings.max_file_mb} МБ")
    free_mb = shutil.disk_usage(files_dir()).free // (1024 * 1024)
    if free_mb - declared // (1024 * 1024) < settings.min_free_disk_mb:
        raise HTTPException(status_code=507, detail="На сервере кончается место, файл не принят")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_file_bytes():
            raise HTTPException(status_code=413, detail=f"Файл больше {settings.max_file_mb} МБ")
        chunks.append(chunk)
    if size == 0:
        raise HTTPException(status_code=400, detail="Пустой файл")
    content_type = (request.headers.get("content-type") or "application/octet-stream")[:120]
    attachment = models.Attachment(channel_id=channel_id, uploader_id=user.id,
                                   name=seal(channel_id, clean_name(name)), size=size,
                                   content_type=content_type, created_at=models.utcnow())
    db.add(attachment)
    db.flush()
    nonce = os.urandom(12)
    sealed = AESGCM(_files_key()).encrypt(nonce, b"".join(chunks), f"file:{attachment.id}".encode())
    target = _path(attachment.id)
    temporary = target.with_suffix(".part")
    temporary.write_bytes(nonce + sealed)
    temporary.replace(target)
    db.commit()
    return attachment_out(attachment)


@router.get("/files/{attachment_id}")
def download_file(attachment_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    attachment = db.get(models.Attachment, attachment_id)
    if attachment is None or attachment.size == 0:
        raise HTTPException(status_code=404, detail="Файл не найден или удалён")
    ensure_channel_member(db, user.id, attachment.channel_id)
    if not has_permission(db, user.id, "text.read"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    message = db.scalar(select(models.Message).where(models.Message.attachment_id == attachment_id))
    if (message is None and attachment.uploader_id != user.id) or (message is not None and message.is_deleted):
        raise HTTPException(status_code=404, detail="Файл не найден или удалён")
    try:
        raw = _path(attachment_id).read_bytes()
        data = AESGCM(_files_key()).decrypt(raw[:12], raw[12:], f"file:{attachment_id}".encode())
    except (OSError, ValueError, Exception):
        logger.warning("File %s could not be read", attachment_id, exc_info=True)
        raise HTTPException(status_code=404, detail="Файл не найден или повреждён")
    name = unseal(attachment.channel_id, attachment.name)
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")
    return Response(data, media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


def purge_orphans(db: Session) -> int:
    """Uploads never attached to a message within a day are deleted."""
    cutoff = models.utcnow() - timedelta(hours=ORPHAN_HOURS)
    attached = select(models.Message.attachment_id).where(models.Message.attachment_id.isnot(None))
    orphans = list(db.scalars(select(models.Attachment).where(
        models.Attachment.created_at < cutoff, models.Attachment.size > 0, models.Attachment.id.not_in(attached))))
    for attachment in orphans:
        remove_file(db, attachment.id)
    db.commit()
    return len(orphans)


def remove_user_files(db: Session, user_id: int) -> None:
    for attachment_id in db.scalars(select(models.Attachment.id).where(
            models.Attachment.uploader_id == user_id, models.Attachment.size > 0)):
        remove_file(db, attachment_id)
