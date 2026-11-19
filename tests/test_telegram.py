"""Telegram bot: linking from the app, join notifications, and owner-approved installer downloads."""
import asyncio
from urllib.parse import parse_qs, urlparse

import pytest

from app.server import downloads, main, telegram
from tests.test_presence import client, setup_admin_and_friend  # noqa: F401

URL = "https://chat.example.org/downloads/PrivateVoiceChat-0.7.0-setup.exe"
ADMIN_CHAT, FRIEND_CHAT, STRANGER_CHAT = 1001, 2002, 3003


@pytest.fixture
def tg(client, monkeypatch, tmp_path):  # noqa: F811
    bot = telegram.bot
    settings = main.settings
    monkeypatch.setattr(settings, "telegram_bot_token", "123:test")
    monkeypatch.setattr(settings, "client_download_url", URL)
    monkeypatch.setattr(settings, "client_download_sha256", "ab" * 32)
    monkeypatch.setattr(settings, "client_latest_version", "0.7.0")
    monkeypatch.setattr(settings, "download_stats_file", str(tmp_path / "stats.json"))
    monkeypatch.setattr(bot, "username", "TestBot")
    monkeypatch.setattr(bot, "recent_joins", {})
    sessions = main.SessionLocal
    monkeypatch.setattr(telegram, "SessionLocal", sessions)
    monkeypatch.setattr(downloads, "SessionLocal", sessions)
    downloads._requests.clear()
    downloads._update_ips.clear()
    calls, pending = [], []
    counter = iter(range(100, 10_000))

    async def fake_call(method, **params):
        calls.append((method, params))
        return {"message_id": next(counter)} if method == "sendMessage" else True

    monkeypatch.setattr(bot, "call", fake_call)
    monkeypatch.setattr(bot, "post", pending.append)

    def flush():
        while pending:
            asyncio.run(pending.pop(0))

    def update(chat_id, text):
        asyncio.run(bot.handle({"update_id": 1, "message": {"chat": {"id": chat_id, "type": "private"},
                                                             "from": {"username": f"u{chat_id}"}, "text": text}}))

    def press(chat_id, data, message_id=100):
        asyncio.run(bot.handle({"update_id": 2, "callback_query": {
            "id": "q", "data": data, "message": {"chat": {"id": chat_id}, "message_id": message_id, "text": "req"}}}))

    def sent_to(chat_id):
        return [p["text"] for m, p in calls if m == "sendMessage" and p["chat_id"] == chat_id]

    yield type("TG", (), {"calls": calls, "flush": staticmethod(flush), "update": staticmethod(update),
                          "press": staticmethod(press), "sent_to": staticmethod(sent_to)})
    for coro in pending:
        coro.close()


def link(client, headers, tg, chat_id):  # noqa: F811
    url = client.post("/telegram/link", headers=headers).json()["url"]
    assert url.startswith("https://t.me/TestBot?start=")
    tg.update(chat_id, "/start " + url.split("start=", 1)[1])
    return url


def test_link_from_app_and_join_notifications(client, tg):  # noqa: F811
    admin, friend, _space, voice = setup_admin_and_friend(client)
    assert client.get("/telegram", headers=friend).json()["linked"] is False
    url = link(client, friend, tg, FRIEND_CHAT)
    status = client.get("/telegram", headers=friend).json()
    assert status["linked"] and status["notify_joins"] and status["tg_username"] == f"u{FRIEND_CHAT}"
    assert "Готово" in tg.sent_to(FRIEND_CHAT)[-1]
    # A used code does not link a second chat.
    tg.update(STRANGER_CHAT, "/start " + url.split("start=", 1)[1])
    assert "устарела" in tg.sent_to(STRANGER_CHAT)[-1]

    client.post(f"/channels/{voice[0]}/connect", headers=admin, json={})
    tg.flush()
    assert "в канале" in tg.sent_to(FRIEND_CHAT)[-1]
    # Rejoining the same channel (app restart) is not announced again.
    before = len(tg.sent_to(FRIEND_CHAT))
    client.post(f"/channels/{voice[0]}/connect", headers=admin, json={})
    tg.flush()
    assert len(tg.sent_to(FRIEND_CHAT)) == before

    # Turned off in the app: nothing more.
    assert client.put("/telegram", headers=friend, json={"notify_joins": False}).json()["notify_joins"] is False
    client.post(f"/channels/{voice[1]}/connect", headers=admin, json={})
    tg.flush()
    assert len(tg.sent_to(FRIEND_CHAT)) == before
    assert client.delete("/telegram", headers=friend).json()["linked"] is False


def test_downloads_are_open_until_the_owner_links(client, tg):  # noqa: F811
    response = client.get("/download", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"].startswith(URL + "?e=")


def test_owner_approves_each_download(client, tg):  # noqa: F811
    admin, friend, _space, _voice = setup_admin_and_friend(client)
    link(client, admin, tg, ADMIN_CHAT)
    link(client, friend, tg, FRIEND_CHAT)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"

    # Without a signed link Caddy gets a 403 for the installer.
    assert client.get("/downloads-auth", headers={"X-Forwarded-Uri": "/downloads/PrivateVoiceChat-0.7.0-setup.exe"}).status_code == 403

    wait = client.get("/download", headers={"User-Agent": ua}, follow_redirects=False)
    assert wait.status_code == 303 and wait.headers["location"].startswith("/download/wait/")
    tg.flush()
    request_text = tg.sent_to(ADMIN_CHAT)[-1]
    assert "Windows 10/11 · Chrome 131" in request_text and "IP:" in request_text
    assert not any("Запрос на скачивание" in t for t in tg.sent_to(FRIEND_CHAT))  # admin info: owner only
    page = client.get(wait.headers["location"])
    assert "Ожидает подтверждения" in page.text and 'http-equiv="refresh"' in page.text
    assert "TestBot" not in page.text and "admin" not in page.text.lower()

    req_id = wait.headers["location"].rsplit("/", 1)[1]
    tg.press(FRIEND_CHAT, f"dl:ok:{req_id}")   # not the owner: ignored
    assert downloads._requests[req_id].status == "pending"
    tg.press(ADMIN_CHAT, f"dl:ok:{req_id}")
    assert downloads._requests[req_id].status == "approved"

    go = client.get(wait.headers["location"], follow_redirects=False)
    assert go.status_code == 302
    signed = urlparse(go.headers["location"])
    assert client.get("/downloads-auth", headers={"X-Forwarded-Uri": f"{signed.path}?{signed.query}"}).status_code == 204
    forged = signed.query.replace(parse_qs(signed.query)["t"][0], "0" * 40)
    assert client.get("/downloads-auth", headers={"X-Forwarded-Uri": f"{signed.path}?{forged}"}).status_code == 403
    assert "скачиваний всего: 1" in [p["text"] for m, p in tg.calls if m == "editMessageText"][-1]

    # Denied.
    downloads._requests.clear()
    wait = client.get("/download", headers={"User-Agent": ua}, follow_redirects=False)
    req_id = wait.headers["location"].rsplit("/", 1)[1]
    tg.flush()
    tg.press(ADMIN_CHAT, f"dl:no:{req_id}")
    assert "Отказано" in client.get(wait.headers["location"]).text

    tg.update(ADMIN_CHAT, "/stats")
    assert "Скачиваний установщика: 1" in tg.sent_to(ADMIN_CHAT)[-1]
    tg.update(FRIEND_CHAT, "/stats")
    assert "Скачиваний" not in tg.sent_to(FRIEND_CHAT)[-1]


def test_updates_only_for_signed_in_people_without_approval(client, tg):  # noqa: F811
    admin, friend, _space, _voice = setup_admin_and_friend(client)
    link(client, admin, tg, ADMIN_CHAT)
    anonymous = client.get("/client/update", params={"version": "0.6.2"}).json()
    assert anonymous["update_available"] is False and anonymous["download_url"] == ""
    update = client.get("/client/update", params={"version": "0.6.2"}, headers=friend).json()
    # The plain URL: the release signature the app checks covers the exact URL.
    assert update["update_available"] and update["download_url"] == URL
    assert client.get("/downloads-auth", headers={"X-Forwarded-Uri": urlparse(URL).path}).status_code == 204
    client.get("/client/update", params={"version": "0.6.2"}, headers=friend)
    tg.flush()
    notes = [t for t in tg.sent_to(ADMIN_CHAT) if "Обновление 0.6.2 → 0.7.0" in t]
    assert len(notes) == 1 and "Друг" in notes[0]


def test_device_names():
    assert downloads.describe_device(
        "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"
    ) == "Android · SM-S918B · Chrome 130"
    assert downloads.describe_device(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
    ) == "iPhone · Safari 17"
    assert downloads.describe_device("") == "неизвестное устройство"
