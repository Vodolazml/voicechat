"""Stage 2: live presence. Leaving means leaving, and everyone sees it at once."""
import base64
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.server import main, media_sessions, presence
from app.server.database import get_db

STRONG = "ChangedAdmin123!Secure"
LK_KEY, LK_SECRET = "vctestkey", "s" * 8 + "TestLiveKitSecretThatIsLongEnough42"


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    for module in (main, presence):
        monkeypatch.setattr(module, "SessionLocal", sessions)
    monkeypatch.setattr(main, "engine", engine)
    calls: list[tuple] = []

    async def fake_remove(channel_id, user_id):
        calls.append(("kick", channel_id, user_id))

    async def fake_publish_allowed(channel_id, user_id, allowed):
        calls.append(("publish", channel_id, user_id, allowed))

    monkeypatch.setattr(main, "remove_media_participant", fake_remove)
    monkeypatch.setattr(media_sessions, "remove_media_participant", fake_remove)
    monkeypatch.setattr(media_sessions, "set_media_publish_allowed", fake_publish_allowed)
    monkeypatch.setattr(presence.settings, "livekit_api_key", LK_KEY)
    monkeypatch.setattr(presence.settings, "livekit_api_secret", LK_SECRET)
    monkeypatch.setattr(presence.settings, "livekit_url", "wss://rtc.example.org")

    def database():
        with sessions() as db:
            yield db

    main.app.dependency_overrides[get_db] = database
    main._rate_limits.clear()
    try:
        with TestClient(main.app) as test_client:
            test_client.calls = calls
            yield test_client
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()


def bearer(token):
    return {"Authorization": "Bearer " + token}


def setup_admin_and_friend(client):
    data = client.post("/auth/login", json={"username": "admin", "password": main.settings.bootstrap_password}).json()
    client.post("/me/password", headers=bearer(data["access_token"]),
                json={"old_password": main.settings.bootstrap_password, "new_password": STRONG})
    admin = client.post("/auth/login", json={"username": "admin", "password": STRONG}).json()["access_token"]
    code = client.post("/invites", headers=bearer(admin), json={}).json()["code"]
    friend = client.post("/auth/register", json={"code": code, "username": "friend", "display_name": "Друг",
                                                 "password": "FriendStrong123!"}).json()["access_token"]
    space = client.get("/spaces", headers=bearer(admin)).json()[0]["id"]
    voice = [c["id"] for c in client.get(f"/spaces/{space}/channels", headers=bearer(admin)).json()
             if c["type"] == "voice"]
    return bearer(admin), bearer(friend), space, voice


def signed_webhook(event: dict) -> tuple[str, dict]:
    from livekit import api

    body = json.dumps(event)
    digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    token = api.AccessToken(LK_KEY, LK_SECRET).with_sha256(digest).to_jwt()
    return body, {"Authorization": token, "Content-Type": "application/webhook+json"}


def test_events_stream_shows_join_and_leave_immediately(client):
    admin, friend, space, voice = setup_admin_and_friend(client)
    friend_token = friend["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect("/events") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": friend_token}))
        assert ws.receive_json()["type"] == "hello"
        client.post(f"/channels/{voice[0]}/connect", headers=admin, json={})
        joined = ws.receive_json()
        assert joined["type"] == "voice.state" and joined["state"]["user_id"] == 1
        client.post(f"/channels/{voice[0]}/disconnect", headers=admin)
        assert ws.receive_json() == {"type": "voice.left", "channel_id": voice[0], "user_id": 1}
        ws.send_text("x" * 2000)  # the server closes the stream itself (see the chat test below)
        assert ws.receive()["type"] == "websocket.close"


def test_events_requires_auth_message(client):
    with client.websocket_connect("/events") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "nonsense"}))
        with pytest.raises(Exception):
            ws.receive_json()


def test_closing_the_app_removes_user_via_livekit_webhook(client):
    admin, friend, space, voice = setup_admin_and_friend(client)
    client.post(f"/channels/{voice[0]}/connect", headers=friend, json={})
    body, headers = signed_webhook({"event": "participant_left", "room": {"name": f"channel-{voice[0]}"},
                                    "participant": {"identity": "u2"}})
    assert client.post("/livekit/webhook", content=body, headers=headers).status_code == 200
    assert client.get(f"/spaces/{space}/voice", headers=admin).json() == []


def test_webhook_for_old_room_does_not_end_new_call(client):
    admin, friend, space, voice = setup_admin_and_friend(client)
    client.post(f"/channels/{voice[0]}/connect", headers=friend, json={})
    client.post(f"/channels/{voice[1]}/connect", headers=friend, json={})
    body, headers = signed_webhook({"event": "participant_left", "room": {"name": f"channel-{voice[0]}"},
                                    "participant": {"identity": "u2"}})
    client.post("/livekit/webhook", content=body, headers=headers)
    states = client.get(f"/spaces/{space}/voice", headers=admin).json()
    assert [(s["user_id"], s["channel_id"]) for s in states] == [(2, voice[1])]


def test_forged_webhook_is_rejected(client):
    admin, friend, space, voice = setup_admin_and_friend(client)
    client.post(f"/channels/{voice[0]}/connect", headers=friend, json={})
    body = json.dumps({"event": "participant_left", "room": {"name": f"channel-{voice[0]}"},
                       "participant": {"identity": "u2"}})
    assert client.post("/livekit/webhook", content=body, headers={"Authorization": "x"}).status_code == 401
    assert len(client.get(f"/spaces/{space}/voice", headers=admin).json()) == 1


def test_moderation_move_kick_and_server_mute(client):
    admin, friend, space, voice = setup_admin_and_friend(client)
    client.post(f"/channels/{voice[0]}/connect", headers=friend, json={})
    moved = client.post("/voice/2/move", headers=admin, json={"channel_id": voice[1]})
    assert moved.status_code == 200 and moved.json()["channel_id"] == voice[1]
    assert ("kick", voice[0], 2) in client.calls
    muted = client.post("/voice/2/server-mute", headers=admin, json={"muted": True}).json()
    assert muted["server_muted"] is True
    assert ("publish", voice[1], 2, False) in client.calls
    token = client.post(f"/channels/{voice[1]}/media-token", headers=friend).json()
    assert token["identity"] == "u2"
    assert client.post("/voice/2/kick", headers=admin).status_code == 200
    assert client.get(f"/spaces/{space}/voice", headers=admin).json() == []
    # Friends cannot moderate.
    client.post(f"/channels/{voice[0]}/connect", headers=admin, json={})
    assert client.post("/voice/1/kick", headers=friend).status_code == 403


def test_channel_management_and_limits(client):
    admin, friend, space, voice = setup_admin_and_friend(client)
    created = client.post("/channels", headers=admin,
                          json={"space_id": space, "name": "Катка", "type": "voice", "user_limit": 1}).json()
    # Public channels appear for everyone in the space, not only for the creator.
    friend_channels = {c["id"] for c in client.get(f"/spaces/{space}/channels", headers=friend).json()}
    assert created["id"] in friend_channels
    client.post(f"/channels/{created['id']}/connect", headers=admin, json={})
    assert client.post(f"/channels/{created['id']}/connect", headers=friend, json={}).status_code == 409
    renamed = client.patch(f"/channels/{created['id']}", headers=admin, json={"name": "Катка 5x5", "position": 0})
    assert renamed.json()["name"] == "Катка 5x5"
    assert client.patch(f"/channels/{created['id']}", headers=friend, json={"name": "hack"}).status_code == 403
    assert client.delete(f"/channels/{created['id']}", headers=admin).status_code == 200
    assert ("kick", created["id"], 1) in client.calls
    assert created["id"] not in {c["id"] for c in client.get(f"/spaces/{space}/channels", headers=friend).json()}


def test_private_channel_is_hidden(client):
    admin, friend, space, voice = setup_admin_and_friend(client)
    private = client.post("/channels", headers=admin,
                          json={"space_id": space, "name": "Админка", "type": "voice", "is_private": True}).json()
    assert private["id"] not in {c["id"] for c in client.get(f"/spaces/{space}/channels", headers=friend).json()}
    assert client.post(f"/channels/{private['id']}/connect", headers=friend, json={}).status_code == 403


def test_chat_in_voice_channel_is_live_and_encrypted_at_rest(client):
    admin, friend, space, voice = setup_admin_and_friend(client)
    friend_token = friend["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect("/events") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": friend_token}))
        ws.receive_json()
        sent = client.post(f"/channels/{voice[0]}/messages", headers=admin,
                           json={"body": "Заходи @friend https://example.org/match"})
        assert sent.status_code == 200
        event = ws.receive_json()
        assert event["type"] == "message.created"
        assert event["message"]["body"].startswith("Заходи") and event["message"]["mentions"] == [2]
        # Let the server end the stream itself (an oversized frame), so the test client never has to
        # cancel a handler that is still running (that race made this test fail now and then).
        ws.send_text("x" * 2000)
        assert ws.receive()["type"] == "websocket.close"
    with presence.SessionLocal() as db:
        from app.server import models
        stored = db.get(models.Message, sent.json()["id"]).body
    assert "Заходи" not in stored and stored.startswith("v1:")
    assert client.get(f"/spaces/{space}/unread", headers=friend).json() == [{"channel_id": voice[0], "count": 1}]
    client.post(f"/channels/{voice[0]}/read", headers=friend, json={"message_id": sent.json()["id"]})
    assert client.get(f"/spaces/{space}/unread", headers=friend).json() == []
    history = client.get(f"/channels/{voice[0]}/messages", headers=friend).json()
    assert [m["body"] for m in history] == ["Заходи @friend https://example.org/match"]
    assert client.patch(f"/messages/{history[0]['id']}", headers=friend, json={"body": "x"}).status_code == 403
    assert client.delete(f"/messages/{history[0]['id']}", headers=admin).status_code == 200
    assert client.get(f"/channels/{voice[0]}/messages", headers=friend).json()[0]["deleted"] is True


def test_wake_only_when_something_is_off_and_one_minute_for_everyone(client):
    from app.server import presence as presence_module

    presence_module._last_wake.clear()
    admin, friend, space, voice = setup_admin_and_friend(client)
    friend_id = client.get("/me", headers=friend).json()["id"]
    client.post(f"/channels/{voice[0]}/connect", headers=friend, json={"muted": False, "deafened": False})
    # Everything on: nothing to wake.
    assert client.post(f"/voice/{friend_id}/wake", headers=admin).status_code == 409
    client.put(f"/channels/{voice[0]}/voice-state", headers=friend, json={"muted": True, "deafened": False})
    first = client.post(f"/voice/{friend_id}/wake", headers=admin)
    assert first.status_code == 200, first.text
    # The cooldown belongs to the person being woken, so it applies to every caller.
    again = client.post(f"/voice/{friend_id}/wake", headers=admin)
    assert again.status_code == 429 and "через" in again.json()["detail"]
    assert client.post(f"/voice/{friend_id}/wake", headers=friend).status_code == 400  # not yourself
    presence_module._last_wake[friend_id] -= 61
    assert client.post(f"/voice/{friend_id}/wake", headers=admin).status_code == 200
