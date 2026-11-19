"""Installer downloads: owner approval in Telegram, signed links, and a counter.

When the Telegram bot is set up and the owner (or the configured admin chat) is linked, every download from
the web page waits for the owner: the bot shows the IP and the device with Allow / Deny buttons, and the
visitor's page turns into the download once allowed. The file itself is served by Caddy only with a
short-lived signed link (Caddy asks /downloads-auth first). Updates of an installed app get a signed link
straight from /client/update (the update check runs before sign-in), and the owner is told about them.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .config import get_settings
from .database import SessionLocal
from .limits import client_ip, rate_limit
from .telegram import bot

logger = logging.getLogger("voicechat.downloads")
router = APIRouter(include_in_schema=False)

PENDING_SECONDS = 30 * 60
APPROVED_LINK_HOURS = 1.0       # an approved web download: one hour, from the requester's address only
MAX_REQUESTS = 300
MSK = timezone(timedelta(hours=3))
CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'none'")


@dataclass
class DownloadRequest:
    id: str
    ip: str
    device: str
    created: float
    status: str = "pending"        # pending | approved | denied
    delivered: bool = False
    messages: tuple[tuple[int, int], ...] = ()


_requests: dict[str, DownloadRequest] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------- signed links

def _token(name: str, expires: int, ip: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, f"download|{name}|{expires}|{ip}".encode(), hashlib.sha256).hexdigest()[:40]


def file_name(url: str) -> str:
    return urlparse(url).path.rsplit("/", 1)[-1]


def signed_url(url: str, ip: str, hours: float | None = None) -> str:
    """A link that works only for a while and only from the address it was made for."""
    if not url:
        return url
    expires = int(time.time() + 3600 * (hours or get_settings().download_link_hours))
    return f"{url}?e={expires}&t={_token(file_name(url), expires, ip)}"


def link_valid(path: str, query: str, ip: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    params = parse_qs(query)
    try:
        expires = int(params.get("e", ["0"])[0])
    except ValueError:
        return False
    given = params.get("t", [""])[0]
    return expires > time.time() and hmac.compare_digest(given, _token(name, expires, ip))


def gate_active() -> bool:
    """Approval is needed only once someone can approve: the bot is on and an admin chat is known."""
    if not bot.enabled:
        return False
    with SessionLocal() as db:
        return bool(bot.admin_chats(db))


def protected(path: str) -> bool:
    return path.lower().endswith(".exe")


def allowed(path: str, query: str, ip: str) -> bool:
    return not protected(path) or not gate_active() or link_valid(path, query, ip)


@router.get("/downloads-auth")
def downloads_auth(request: Request) -> Response:
    """Caddy's forward_auth for /downloads/*: 204 lets the file through, 403 stops it."""
    uri = urlparse(request.headers.get("x-forwarded-uri", ""))
    if allowed(uri.path, uri.query, client_ip(request)):
        return Response(status_code=204)
    return HTMLResponse("Ссылка на скачивание устарела. Откройте главную страницу и нажмите «Скачать».",
                        status_code=403)


# ---------------------------------------------------------------------- counter

def _stats_path() -> Path:
    settings = get_settings()
    if settings.download_stats_file:
        return Path(settings.download_stats_file)
    return Path(settings.files_dir).resolve().parent / "download-stats.json"


def load_stats() -> dict:
    try:
        return json.loads(_stats_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def count(event: str, version: str = "", detail: str = "") -> dict:
    """event: page (downloads without approval), requested, approved, denied, update."""
    with _lock:
        stats = load_stats()
        stats[event] = int(stats.get(event, 0)) + 1
        if version:
            per = stats.setdefault(f"{event}_by_version", {})
            per[version] = int(per.get(version, 0)) + 1
        recent = stats.setdefault("recent", [])
        recent.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event,
                       "version": version, "detail": detail[:120]})
        del recent[:-50]
        path = _stats_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".stats-")
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(stats, out, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("Download counter not saved: %s", exc)
        return stats


def stats_text() -> str:
    stats = load_stats()
    downloads = int(stats.get("approved", 0)) + int(stats.get("page", 0))
    lines = [f"📊 <b>Скачиваний установщика: {downloads}</b>",
             f"Запросов: {stats.get('requested', 0)} · разрешено: {stats.get('approved', 0)} · "
             f"отклонено: {stats.get('denied', 0)}"]
    if stats.get("page"):
        lines.append(f"До включения подтверждений: {stats['page']}")
    lines.append(f"Обновлений программы: {stats.get('update', 0)}")
    per = stats.get("update_by_version") or {}
    if per:
        lines.append("По версиям: " + ", ".join(f"{v} — {n}" for v, n in sorted(per.items())[-5:]))
    return "\n".join(lines)


# ---------------------------------------------------------------------- device names

def describe_device(user_agent: str) -> str:
    ua = user_agent or ""
    system = "неизвестное устройство"
    rules = [
        (r"Windows NT 10\.0", "Windows 10/11"), (r"Windows NT 6\.3", "Windows 8.1"), (r"Windows NT 6\.1", "Windows 7"),
        (r"Windows", "Windows"), (r"iPhone", "iPhone"), (r"iPad", "iPad"), (r"Mac OS X", "macOS"),
        (r"CrOS", "ChromeOS"), (r"Linux", "Linux"),
    ]
    android = re.search(r"Android [\d.]+;(?: [^;)]*;)*? ?([^;)]+?)(?: Build[^;)]*)?\)", ua)
    if "Android" in ua:
        model = android.group(1).strip() if android else ""
        system = "Android" + (f" · {model}" if model and model not in ("K", "wv") else "")
    else:
        for pattern, name in rules:
            if re.search(pattern, ua):
                system = name
                break
    browsers = [(r"YaBrowser/(\d+)", "Яндекс Браузер"), (r"OPR/(\d+)", "Opera"), (r"Edg/(\d+)", "Edge"),
                (r"Firefox/(\d+)", "Firefox"), (r"SamsungBrowser/(\d+)", "Samsung Internet"),
                (r"Chrome/(\d+)", "Chrome"), (r"Version/(\d+).*Safari", "Safari")]
    browser = ""
    for pattern, name in browsers:
        match = re.search(pattern, ua)
        if match:
            browser = f"{name} {match.group(1)}"
            break
    if not browser and ua:
        browser = html.unescape(ua[:60])
    return f"{system} · {browser}" if browser else system


# ---------------------------------------------------------------------- web flow

def _purge(now: float) -> None:
    for key in [k for k, r in _requests.items() if now - r.created > PENDING_SECONDS]:
        del _requests[key]


def _request_text(req: DownloadRequest, version: str, language: str) -> str:
    when = datetime.fromtimestamp(req.created, MSK).strftime("%d.%m %H:%M")
    return (f"⬇️ <b>Запрос на скачивание VoiceChat {html.escape(version)}</b>\n"
            f"IP: <code>{html.escape(req.ip)}</code>\n"
            f"Устройство: {html.escape(req.device)}\n"
            + (f"Язык: {html.escape(language)}\n" if language else "")
            + f"Время: {when} (МСК)")


def start_request(request: Request) -> DownloadRequest:
    ip = client_ip(request)
    now = time.time()
    with _lock:
        _purge(now)
        for existing in _requests.values():
            if existing.ip == ip and existing.status != "denied":
                return existing   # a second click waits on the same request
    rate_limit(f"download-request:{ip}", 5, 3600)
    req = DownloadRequest(id=secrets.token_urlsafe(16), ip=ip,
                          device=describe_device(request.headers.get("user-agent", "")), created=now)
    with _lock:
        if len(_requests) >= MAX_REQUESTS:
            raise HTTPException(status_code=429, detail="Слишком много запросов. Попробуйте позже.")
        _requests[req.id] = req
    count("requested", detail=f"{ip} {req.device}")
    language = request.headers.get("accept-language", "").split(",")[0][:20]
    text = _request_text(req, get_settings().client_latest_version, language)
    buttons = [[{"text": "✅ Разрешить", "callback_data": f"dl:ok:{req.id}"},
                {"text": "❌ Отклонить", "callback_data": f"dl:no:{req.id}"}]]

    async def notify() -> None:
        with SessionLocal() as db:
            chats = bot.admin_chats(db)
        sent = []
        for chat_id in chats:
            message_id = await bot.send(chat_id, text, buttons)
            if message_id:
                sent.append((chat_id, message_id))
        req.messages = tuple(sent)

    bot.post(notify())
    return req


async def on_decision(rest: str, chat_id: int, message_id: int, text: str) -> str | None:
    """The owner pressed Allow / Deny under a download request."""
    if not bot.is_admin_chat(chat_id):
        return "Это может только владелец сервера"
    action, _, req_id = rest.partition(":")
    req = _requests.get(req_id)
    if req is None:
        await bot.edit(chat_id, message_id, html.escape(text) + "\n\n⌛ Запрос устарел")
        return "Запрос устарел"
    if req.status != "pending":
        return "Уже решено"
    req.status = "approved" if action == "ok" else "denied"
    stats = count(req.status, get_settings().client_latest_version, f"{req.ip} {req.device}")
    if req.status == "approved":
        total = int(stats.get("approved", 0)) + int(stats.get("page", 0))
        mark = f"✅ Разрешено · скачиваний всего: {total}"
    else:
        mark = "❌ Отклонено"
    for chat, message in req.messages or ((chat_id, message_id),):
        await bot.edit(chat, message, html.escape(text) + "\n\n" + mark)
    return "Разрешено" if req.status == "approved" else "Отклонено"


bot.callbacks["dl"] = on_decision


WAIT_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}<title>VoiceChat</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px 16px; background: #0d0e13;
         color: #eef0f6; font-family: "Segoe UI", Inter, system-ui, sans-serif; }}
  .card {{ max-width: 440px; padding: 32px 28px; border-radius: 22px; text-align: center;
          background: rgba(20, 21, 30, .9); border: 1px solid rgba(255, 255, 255, .07); }}
  h1 {{ margin: 0 0 10px; font-size: 22px; }}
  p {{ margin: 0; color: #9aa0b4; line-height: 1.5; }}
  a.button {{ display: inline-block; margin-top: 22px; padding: 13px 24px; border-radius: 14px; color: #fff;
             text-decoration: none; font-weight: 600; background: linear-gradient(90deg, #7c5cff, #a14dff); }}
  a.back {{ display: inline-block; margin-top: 18px; color: #9aa0b4; }}
</style></head>
<body><main class="card"><h1>{title}</h1><p>{text}</p>{extra}</main></body></html>"""


def _page(title: str, text: str, extra: str = "", refresh: str = "", status: int = 200) -> HTMLResponse:
    page = WAIT_PAGE.format(title=title, text=text, extra=extra, refresh=refresh)
    return HTMLResponse(page, status_code=status, headers={"Content-Security-Policy": CSP})


@router.get("/download")
def download_latest(request: Request) -> Response:
    """The page's button and the shareable link. Waits for the owner's approval when that is on."""
    url = get_settings().client_download_url
    if not url:
        return HTMLResponse("Установщик ещё не опубликован", status_code=404)
    if not gate_active():
        count("page", get_settings().client_latest_version, client_ip(request))
        return RedirectResponse(signed_url(url, client_ip(request)) if bot.enabled else url, status_code=302)
    req = start_request(request)
    return RedirectResponse(f"/download/wait/{req.id}", status_code=303)


@router.get("/download/wait/{req_id}")
def download_wait(req_id: str, request: Request) -> Response:
    """Neutral status only: nothing about who decides or how."""
    req = _requests.get(req_id)
    back = '<a class="back" href="/">На главную</a>'
    if req is None or req.ip != client_ip(request):
        return _page("Ссылка устарела", "Откройте главную страницу и нажмите «Скачать» ещё раз.", back,
                     status=404)
    if req.status == "denied":
        return _page("Отказано", "Скачивание не разрешено.", back)
    if req.status == "approved":
        link = signed_url(get_settings().client_download_url, req.ip, APPROVED_LINK_HOURS)
        if not req.delivered:
            req.delivered = True
            return RedirectResponse(link, status_code=302)
        return _page("Разрешено", "Скачивание началось. Если нет, нажмите кнопку.",
                     f'<a class="button" href="{html.escape(link)}">Скачать установщик</a>')
    return _page("Ожидает подтверждения", "Не закрывайте страницу: загрузка начнётся сама.",
                 refresh='<meta http-equiv="refresh" content="3">')


def update_link(version_from: str, request: Request, user_name: str | None) -> str:
    """The installer link for an installed app's update. With approvals on, only for a signed-in person
    (no approval needed: updates must keep working); the owner is told who updated."""
    settings = get_settings()
    if not bot.enabled:
        return settings.client_download_url
    ip = client_ip(request)
    if gate_active() and user_name is None:
        return ""
    try:
        rate_limit(f"update-note:{ip}:{settings.client_latest_version}", 1, 6 * 3600)
        first = True
    except HTTPException:  # already counted and told for this address and version
        first = False
    if first:
        who = f"{user_name} · " if user_name else ""
        count("update", settings.client_latest_version, f"{ip} {who}{version_from}")
        bot.notify_admins(f"🔄 Обновление {html.escape(version_from)} → "
                          f"{html.escape(settings.client_latest_version)}\n"
                          f"{html.escape(who)}IP: <code>{html.escape(ip)}</code>")
    return signed_url(settings.client_download_url, ip)
