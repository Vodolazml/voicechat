"""The server's Telegram bot.

- Anyone links their account from the app (Settings -> Account -> "Connect Telegram"): the app opens
  t.me/<bot>?start=<one-time code>, and the bot ties that chat to the account. Linked people can get
  "someone joined a voice channel" messages for their own channels.
- Admin information goes only to the server owner's chat (plus an optional configured admin chat):
  download requests with Allow/Deny buttons (see downloads.py) and the download counter.

Long polling (getUpdates) from the API process: no inbound webhook, nothing to open in the firewall.
The API runs as one worker, so there is exactly one poller.
"""
from __future__ import annotations

import asyncio
import html
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import models
from .config import get_settings
from .database import SessionLocal, get_db
from .deps import channel_participant_ids, current_user
from .limits import rate_limit

logger = logging.getLogger("voicechat.telegram")
# httpx logs every request URL at INFO, and a Bot API URL contains the token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

LINK_CODE_SECONDS = 15 * 60
JOIN_REPEAT_SECONDS = 10 * 60   # the same person re-joining the same channel is announced once per 10 min
API = "https://api.telegram.org"
MSK = timezone(timedelta(hours=3))

router = APIRouter(prefix="/telegram", tags=["telegram"])
CallbackHandler = Callable[[str, int, int, str], Awaitable[str | None]]


@dataclass
class Bot:
    username: str = ""
    link_codes: dict[str, tuple[int, float]] = field(default_factory=dict)
    callbacks: dict[str, CallbackHandler] = field(default_factory=dict)
    recent_joins: dict[tuple[int, int], float] = field(default_factory=dict)
    loop: asyncio.AbstractEventLoop | None = None
    _client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(get_settings().telegram_bot_token)

    # ------------------------------------------------------------------ Bot API

    async def call(self, method: str, **params: Any) -> Any:
        """One Bot API call; None on any failure (never raises, never logs the token)."""
        token = get_settings().telegram_bot_token
        if not token:
            return None
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=10.0))
        try:
            response = await self._client.post(f"{API}/bot{token}/{method}", json=params)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Telegram %s failed: %s", method, type(exc).__name__)
            return None
        if not data.get("ok"):
            logger.warning("Telegram %s refused: %s", method, str(data.get("description", ""))[:200])
            return None
        return data.get("result")

    async def send(self, chat_id: int, text: str, buttons: list[list[dict]] | None = None) -> int | None:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                  "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        result = await self.call("sendMessage", **params)
        return result.get("message_id") if isinstance(result, dict) else None

    async def edit(self, chat_id: int, message_id: int, text: str) -> None:
        await self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text,
                        parse_mode="HTML", disable_web_page_preview=True)

    def post(self, coro: Awaitable[Any]) -> None:
        """Fire and forget, from async code or from a worker thread (sync endpoints)."""
        if not self.enabled:
            close = getattr(coro, "close", None)
            if close:
                close()
            return
        try:
            asyncio.get_running_loop().create_task(coro)  # type: ignore[arg-type]
        except RuntimeError:
            if self.loop is not None and self.loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, self.loop)  # type: ignore[arg-type]
            else:
                coro.close()  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ recipients

    def admin_chats(self, db: Session) -> list[int]:
        chats = set(db.scalars(select(models.TelegramLink.chat_id)
                               .join(models.User, models.User.id == models.TelegramLink.user_id)
                               .where(models.User.is_owner.is_(True))))
        extra = get_settings().telegram_admin_chat_id
        if extra:
            chats.add(extra)
        return sorted(chats)

    def is_admin_chat(self, chat_id: int) -> bool:
        with SessionLocal() as db:
            return chat_id in self.admin_chats(db)

    # ------------------------------------------------------------------ linking

    def new_link_code(self, user_id: int) -> str:
        now = time.time()
        for code in [c for c, (_, exp) in self.link_codes.items() if exp < now]:
            del self.link_codes[code]
        for code in [c for c, (uid, _) in self.link_codes.items() if uid == user_id]:
            del self.link_codes[code]
        code = secrets.token_urlsafe(18)
        self.link_codes[code] = (user_id, now + LINK_CODE_SECONDS)
        return code

    def link(self, code: str, chat_id: int, tg_username: str) -> models.User | None:
        entry = self.link_codes.pop(code, None)
        if entry is None or entry[1] < time.time():
            return None
        with SessionLocal() as db:
            user = db.get(models.User, entry[0])
            if user is None or user.status != "active":
                return None
            # One chat speaks for one account; one account has one chat.
            db.execute(delete(models.TelegramLink).where(
                (models.TelegramLink.chat_id == chat_id) | (models.TelegramLink.user_id == user.id)))
            db.add(models.TelegramLink(user_id=user.id, chat_id=chat_id, tg_username=tg_username[:64]))
            db.commit()
            db.refresh(user)
            db.expunge(user)
            return user

    # ------------------------------------------------------------------ updates

    async def run(self) -> None:
        """Poll for updates until cancelled."""
        self.loop = asyncio.get_running_loop()
        me = await self.call("getMe")
        if isinstance(me, dict):
            self.username = str(me.get("username", ""))
            logger.info("Telegram bot @%s is on", self.username)
        await self.call("setMyCommands", commands=[
            {"command": "notify", "description": "Уведомления о входе в каналы: вкл/выкл"},
            {"command": "stop", "description": "Отвязать аккаунт"},
        ])
        offset = 0
        while True:
            updates = await self.call("getUpdates", offset=offset, timeout=50,
                                      allowed_updates=["message", "callback_query"])
            if updates is None:
                await asyncio.sleep(5)
                continue
            for update in updates:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                try:
                    await self.handle(update)
                except Exception:  # noqa: BLE001 - one bad update must not stop the bot
                    logger.exception("Telegram update failed")

    async def handle(self, update: dict) -> None:
        if "callback_query" in update:
            await self._on_callback(update["callback_query"])
            return
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            return
        chat_id = int(chat.get("id", 0))
        text = str(message.get("text", "")).strip()
        command, _, arg = text.partition(" ")
        command = ADMIN_BUTTONS.get(text, command.split("@", 1)[0].lower())
        if command == "/start" and arg.strip():
            await self._on_link(chat_id, arg.strip(), str((message.get("from") or {}).get("username", "")))
        elif command == "/notify":
            await self._toggle_joins(chat_id)
        elif command in ("/stats", "/online", "/versions"):
            if not self.is_admin_chat(chat_id):
                await self.send(chat_id, "Недоступно.")
            elif command == "/stats":
                from .downloads import stats_text
                await self.send(chat_id, stats_text())
            elif command == "/online":
                await self.send(chat_id, online_text())
            else:
                await self.send(chat_id, versions_text())
        elif command == "/stop":
            with SessionLocal() as db:
                removed = db.execute(delete(models.TelegramLink).where(models.TelegramLink.chat_id == chat_id))
                db.commit()
            await self.send(chat_id, "Аккаунт отвязан. Уведомлений больше не будет." if removed.rowcount
                            else "Этот чат не привязан к аккаунту.")
        else:
            if command == "/start":
                logger.info("Telegram /start from chat %s", chat_id)
            await self.send_status(chat_id)

    async def send_status(self, chat_id: int, prefix: str = "") -> None:
        params: dict[str, Any] = {"chat_id": chat_id, "text": prefix + self._status_text(chat_id),
                                  "parse_mode": "HTML", "disable_web_page_preview": True}
        if self.is_admin_chat(chat_id):
            params["reply_markup"] = {"keyboard": [[{"text": t} for t in ADMIN_BUTTONS]],
                                      "resize_keyboard": True, "is_persistent": True}
        await self.call("sendMessage", **params)

    def _status_text(self, chat_id: int) -> str:
        with SessionLocal() as db:
            link = db.scalar(select(models.TelegramLink).where(models.TelegramLink.chat_id == chat_id))
            user = db.get(models.User, link.user_id) if link else None
            admin = chat_id in self.admin_chats(db)
        if user is None:
            text = ("Это бот VoiceChat. Чтобы получать уведомления, привяжите аккаунт в программе: "
                    "<b>Настройки → Аккаунт и устройства → Подключить Telegram</b>.")
        else:
            joins = "включены" if link.notify_joins else "выключены"
            text = (f"Аккаунт: <b>{html.escape(user.display_name)}</b> (@{html.escape(user.username)}).\n"
                    f"Уведомления о входе в голосовые каналы {joins} (/notify).")
        if admin:
            text += ("\n\nВы администратор: сюда приходят запросы на скачивание. Кнопки внизу: кто в сети, "
                     "версии программы у всех, счётчик скачиваний.")
        return text

    async def _on_link(self, chat_id: int, code: str, tg_username: str) -> None:
        user = self.link(code, chat_id, tg_username)
        if user is None:
            await self.send(chat_id, "Ссылка устарела. Нажмите «Подключить Telegram» в программе ещё раз.")
            return
        logger.info("Telegram linked for user %s", user.id)
        await self.send_status(chat_id, "Готово! ")
        from .events import hub
        hub.publish([user.id], {"type": "telegram.linked"})

    async def _toggle_joins(self, chat_id: int) -> None:
        with SessionLocal() as db:
            link = db.scalar(select(models.TelegramLink).where(models.TelegramLink.chat_id == chat_id))
            if link is None:
                await self.send(chat_id, self._status_text(chat_id))
                return
            link.notify_joins = not link.notify_joins
            on = link.notify_joins
            db.commit()
        await self.send(chat_id, "Уведомления о входе в каналы включены." if on
                        else "Уведомления о входе в каналы выключены.")

    async def _on_callback(self, query: dict) -> None:
        data = str(query.get("data", ""))
        message = query.get("message") or {}
        chat_id = int((message.get("chat") or {}).get("id", 0))
        message_id = int(message.get("message_id", 0))
        prefix, _, rest = data.partition(":")
        handler = self.callbacks.get(prefix)
        answer = await handler(rest, chat_id, message_id, str(message.get("text", ""))) if handler else None
        await self.call("answerCallbackQuery", callback_query_id=query.get("id"), text=answer or "")

    # ------------------------------------------------------------------ notifications

    def notify_join(self, db: Session, user: models.User, channel: models.Channel) -> None:
        """Someone joined a voice channel: tell linked people of that channel who are not in it."""
        if not self.enabled:
            return
        now = time.time()
        key = (user.id, channel.id)
        if now - self.recent_joins.get(key, 0) < JOIN_REPEAT_SECONDS:
            return
        self.recent_joins[key] = now
        if len(self.recent_joins) > 5000:
            self.recent_joins = {k: t for k, t in self.recent_joins.items() if now - t < JOIN_REPEAT_SECONDS}
        inside = set(db.scalars(select(models.VoiceState.user_id).where(
            models.VoiceState.channel_id == channel.id)))
        recipients = channel_participant_ids(db, channel.id) - inside - {user.id}
        if not recipients:
            return
        chats = list(db.scalars(select(models.TelegramLink.chat_id).where(
            models.TelegramLink.user_id.in_(recipients), models.TelegramLink.notify_joins.is_(True))))
        if not chats:
            return
        space = db.get(models.Space, channel.space_id)
        names = list(db.scalars(select(models.User.display_name).where(
            models.User.id.in_(inside - {user.id})).order_by(models.User.display_name)))
        text = (f"🎙 <b>{html.escape(user.display_name)}</b> в канале «{html.escape(channel.name)}»"
                + (f" · {html.escape(space.name)}" if space else ""))
        if names:
            text += "\nУже там: " + html.escape(", ".join(names[:10])) + (" и другие" if len(names) > 10 else "")
        for chat_id in chats:
            self.post(self.send(chat_id, text))

    def notify_admins(self, text: str) -> None:
        with SessionLocal() as db:
            chats = self.admin_chats(db)
        for chat_id in chats:
            self.post(self.send(chat_id, text))


bot = Bot()
ADMIN_BUTTONS = {"👥 Кто в сети": "/online", "🔄 Версии": "/versions", "📊 Статистика": "/stats"}


def _ago(iso: str) -> str:
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return "?"
    return then.astimezone(MSK).strftime("%d.%m %H:%M")


def online_text() -> str:
    """Owner only: who has the app open now, and who sits in which voice channel."""
    from .events import hub
    from .downloads import load_versions
    versions = load_versions()
    with SessionLocal() as db:
        online = hub.online_user_ids()
        states = {s.user_id: s for s in db.scalars(select(models.VoiceState))}
        ids = online | set(states)
        if not ids:
            return "👥 Сейчас в сети никого нет."
        users = db.scalars(select(models.User).where(models.User.id.in_(ids)).order_by(models.User.display_name))
        channels = {c.id: c.name for c in db.scalars(select(models.Channel).where(
            models.Channel.id.in_({s.channel_id for s in states.values()})))} if states else {}
        lines = [f"👥 <b>В сети: {len(ids)}</b>"]
        for user in users:
            state = states.get(user.id)
            where = ""
            if state:
                marks = ("🔇" if state.muted or state.server_muted else "") + ("🙉" if state.deafened else "")
                where = f" — 🎙 «{html.escape(channels.get(state.channel_id, '?'))}» {marks}".rstrip()
            version = (versions.get(str(user.id)) or {}).get("version")
            lines.append(f"• {html.escape(user.display_name)} (@{html.escape(user.username)})"
                         + (f" · {html.escape(version)}" if version else "") + where)
    return "\n".join(lines)


def versions_text() -> str:
    """Owner only: the app version each person last reported, and since when."""
    from .downloads import load_versions
    versions = load_versions()
    latest = get_settings().client_latest_version
    with SessionLocal() as db:
        users = list(db.scalars(select(models.User).where(models.User.status == "active")
                                .order_by(models.User.display_name)))
    lines = [f"🔄 <b>Версии программы</b> (последняя {html.escape(latest)})"]
    for user in users:
        row = versions.get(str(user.id)) or {}
        if not row:
            lines.append(f"• {html.escape(user.display_name)}: версия пока неизвестна (появится после входа в программу)")
            continue
        mark = "✅" if row.get("version") == latest else "⏳"
        text = f"{mark} {html.escape(user.display_name)}: {html.escape(row.get('version', '?'))}"
        if row.get("previous"):
            text += f" (обновление с {html.escape(row['previous'])}: {_ago(row.get('since', ''))})"
        text += f" · последний вход {_ago(row.get('seen', ''))}"
        lines.append(text)
    return "\n".join(lines)


# ---------------------------------------------------------------------- app endpoints

class TelegramStatusOut(BaseModel):
    available: bool
    linked: bool = False
    tg_username: str = ""
    notify_joins: bool = True
    bot_username: str = ""


class TelegramSettingsIn(BaseModel):
    notify_joins: bool


def _status(db: Session, user: models.User) -> TelegramStatusOut:
    link = db.get(models.TelegramLink, user.id)
    return TelegramStatusOut(available=bot.enabled and bool(bot.username), linked=link is not None,
                             tg_username=link.tg_username if link else "",
                             notify_joins=link.notify_joins if link else True, bot_username=bot.username)


@router.get("", response_model=TelegramStatusOut)
def telegram_status(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return _status(db, user)


@router.post("/link")
def telegram_link(user: models.User = Depends(current_user)) -> dict[str, str]:
    rate_limit(f"telegram-link:{user.id}", 10, 600)
    if not bot.enabled or not bot.username:
        raise HTTPException(status_code=503, detail="Telegram-бот на сервере не настроен")
    return {"url": f"https://t.me/{bot.username}?start={bot.new_link_code(user.id)}"}


@router.put("", response_model=TelegramStatusOut)
def telegram_settings(payload: TelegramSettingsIn, user: models.User = Depends(current_user),
                      db: Session = Depends(get_db)):
    link = db.get(models.TelegramLink, user.id)
    if link is None:
        raise HTTPException(status_code=404, detail="Telegram не подключён")
    link.notify_joins = payload.notify_joins
    db.commit()
    return _status(db, user)


@router.delete("", response_model=TelegramStatusOut)
def telegram_unlink(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    link = db.get(models.TelegramLink, user.id)
    if link is not None:
        chat_id = link.chat_id
        db.delete(link)
        db.commit()
        bot.post(bot.send(chat_id, "Аккаунт отвязан в программе. Уведомлений больше не будет."))
    return _status(db, user)
