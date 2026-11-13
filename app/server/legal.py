"""The privacy policy, the terms of use, the consent to personal data processing and the open-source
licenses, as public pages, plus the housekeeping the policy promises (old sign-ins and audit records are
purged, a deleted account is wiped).

The texts live in docs/legal/*.md with {placeholders} filled from the server settings, so the operator's
name and contact are configured in .env and never hard-coded in the repository.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from . import models
from .config import get_settings

logger = logging.getLogger("voicechat.legal")
router = APIRouter(include_in_schema=False)

# Bump when the policy, the terms or the consent change in substance: the app asks everyone to accept again.
LEGAL_VERSION = 2
LEGAL_DATE = "13.11.2026"
SESSION_RETENTION_DAYS = 90
AUDIT_RETENTION_DAYS = 365
PURGE_INTERVAL_SECONDS = 6 * 3600

ROOT = Path(__file__).resolve().parents[2]
DOCS = {"privacy": ROOT / "docs" / "legal" / "privacy.md", "terms": ROOT / "docs" / "legal" / "terms.md",
        "consent": ROOT / "docs" / "legal" / "consent.md", "licenses": ROOT / "THIRD_PARTY_NOTICES.md"}
TITLES = {"privacy": "Политика конфиденциальности", "terms": "Пользовательское соглашение",
          "consent": "Согласие на обработку персональных данных", "licenses": "Лицензии компонентов"}

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · VoiceChat</title>
<style>
  :root { color-scheme: dark; --text: #eef0f6; --muted: #9aa0b4; --accent: #b4a5ff; }
  body { margin: 0; padding: 32px 16px 64px; background: #0d0e13; color: var(--text);
         font: 15px/1.6 "Segoe UI Variable Text", "Segoe UI", Inter, system-ui, sans-serif; }
  main { max-width: 760px; margin: 0 auto; }
  h1 { font-size: 26px; line-height: 1.25; margin: 0 0 8px; }
  h2 { font-size: 18px; margin: 28px 0 8px; }
  p, li { color: #d6d9e4; }
  ul, ol { padding-left: 22px; }
  a { color: var(--accent); }
  nav { margin-bottom: 24px; font-size: 14px; color: var(--muted); }
  nav a { margin-right: 14px; }
  pre { white-space: pre-wrap; word-break: break-word; font-size: 13px; color: #c9ccd8; }
</style>
</head>
<body><main>
<nav><a href="/">VoiceChat</a><a href="/privacy">Конфиденциальность</a><a href="/terms">Соглашение</a><a href="/consent">Согласие</a><a href="/licenses">Лицензии</a></nav>
{body}
</main></body>
</html>
"""


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return re.sub(r"(?<![\"'>=])(https?://[^\s<,;)]+)", r'<a href="\1">\1</a>', text)


def render_markdown(source: str) -> str:
    """Just enough Markdown for our documents: headings, paragraphs, lists, bold and links."""
    out: list[str] = []
    paragraph: list[str] = []
    list_tag = ""

    def flush() -> None:
        nonlocal list_tag
        if paragraph:
            out.append("<p>" + _inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = ""

    for raw in source.splitlines():
        line = raw.strip()
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        item = re.match(r"^(?:[-*]|\d+\.)\s+(.*)$", line)
        if not line:
            flush()
        elif heading:
            flush()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif item:
            if paragraph:
                out.append("<p>" + _inline(" ".join(paragraph)) + "</p>")
                paragraph.clear()
            tag = "ol" if line[0].isdigit() else "ul"
            if list_tag != tag:
                if list_tag:
                    out.append(f"</{list_tag}>")
                out.append(f"<{tag}>")
                list_tag = tag
            out.append("<li>" + _inline(item.group(1)) + "</li>")
        else:
            if list_tag:
                out.append(f"</{list_tag}>")
                list_tag = ""
            paragraph.append(line)
    flush()
    return "\n".join(out)


def server_address(request: Request | None) -> str:
    if request is None:
        return "этот сервер"
    host = request.headers.get("host") or request.url.netloc
    scheme = "https" if get_settings().is_production else request.url.scheme
    return f"{scheme}://{host}"


def document_text(name: str, request: Request | None = None) -> str:
    settings = get_settings()
    text = DOCS[name].read_text(encoding="utf-8")
    values = {"version": str(LEGAL_VERSION), "date": LEGAL_DATE, "server": server_address(request),
              "operator": settings.operator_name, "contact": settings.operator_contact,
              "hosting": settings.hosting_provider}
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def _page(name: str, request: Request) -> Response:
    try:
        text = document_text(name, request)
    except OSError:
        return HTMLResponse("Документ не найден", status_code=404)
    body = render_markdown(text) if name != "licenses" else (
        f"<h1>{TITLES[name]}</h1><pre>" + html.escape(text) + "</pre>")
    page = PAGE.replace("{title}", TITLES[name]).replace("{body}", body)
    return HTMLResponse(page, headers={"Content-Security-Policy":
                                       "default-src 'none'; style-src 'unsafe-inline'; "
                                       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"})


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request) -> Response:
    return _page("privacy", request)


@router.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request) -> Response:
    return _page("terms", request)


@router.get("/consent", response_class=HTMLResponse)
def consent_page(request: Request) -> Response:
    return _page("consent", request)


@router.get("/licenses", response_class=HTMLResponse)
def licenses_page(request: Request) -> Response:
    return _page("licenses", request)


# --- housekeeping promised by the policy -----------------------------------------------------------------

def purge_old_records(db: Session) -> tuple[int, int]:
    """Sign-ins that ended more than 90 days ago and audit records older than a year are deleted."""
    now = models.utcnow()
    session_cutoff = now - timedelta(days=SESSION_RETENTION_DAYS)
    sessions = db.execute(delete(models.LoginSession).where(or_(
        models.LoginSession.expires_at < session_cutoff,
        models.LoginSession.revoked_at < session_cutoff,
    ))).rowcount or 0
    audits = db.execute(delete(models.AuditLog).where(
        models.AuditLog.created_at < now - timedelta(days=AUDIT_RETENTION_DAYS))).rowcount or 0
    db.commit()
    return sessions, audits


async def retention_janitor(session_factory) -> None:
    while True:
        try:
            from .files import purge_orphans

            with session_factory() as db:
                sessions, audits = purge_old_records(db)
                orphans = purge_orphans(db)
            if sessions or audits or orphans:
                logger.info("Retention: removed %s old sign-ins, %s audit records, %s unsent files",
                            sessions, audits, orphans)
        except Exception:
            logger.exception("Retention cleanup failed")
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)


def wipe_account(db: Session, user: models.User) -> None:
    """Deletes what identifies the person; rows other tables point at stay, anonymised."""
    uid = user.id
    user.status = "deleted"
    user.username = f"deleted-{uid}"
    user.display_name = "Удалённый пользователь"
    user.password_hash = "!"
    user.avatar = None
    user.avatar_url = None
    user.avatar_version = 0
    user.must_change_password = False
    db.execute(update(models.Message).where(models.Message.author_id == uid)
               .values(body="", is_deleted=True))
    db.execute(delete(models.LoginSession).where(models.LoginSession.user_id == uid))
    db.execute(delete(models.TemporaryCredential).where(models.TemporaryCredential.user_id == uid))
    db.execute(delete(models.VoiceState).where(models.VoiceState.user_id == uid))
    db.execute(delete(models.ChannelRead).where(models.ChannelRead.user_id == uid))
    db.execute(delete(models.ChannelMember).where(models.ChannelMember.user_id == uid))
    db.execute(delete(models.SpaceModerator).where(models.SpaceModerator.user_id == uid))
    db.execute(delete(models.SpaceMember).where(models.SpaceMember.user_id == uid))
    db.execute(update(models.AuditLog).where(models.AuditLog.actor_id == uid).values(ip_address="", details="{}"))


def owned_spaces(db: Session, user_id: int) -> list[str]:
    return list(db.scalars(select(models.Space.name).where(
        models.Space.created_by == user_id, models.Space.is_deleted.is_(False))))
