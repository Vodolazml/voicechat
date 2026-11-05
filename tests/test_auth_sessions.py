"""Stage 1: sessions with rotating refresh tokens, invites, owner protection, channel switch kicks."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.server import main, models
from app.server.config import Settings
from app.server.database import get_db

STRONG = "ChangedAdmin123!Secure"


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    kicked: list[tuple[int, int]] = []

    async def fake_remove(channel_id, user_id):
        kicked.append((channel_id, user_id))

    monkeypatch.setattr(main, "remove_media_participant", fake_remove)

    def database():
        with sessions() as db:
            yield db

    main.app.dependency_overrides[get_db] = database
    main._rate_limits.clear()
    try:
        with TestClient(main.app) as test_client:
            test_client.kicked = kicked
            test_client.sessions = sessions
            yield test_client
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()


def bearer(token):
    return {"Authorization": "Bearer " + token}


def admin_login(client, device="PC"):
    data = client.post("/auth/login", json={"username": "admin", "password": main.settings.bootstrap_password}).json()
    client.post("/me/password", headers=bearer(data["access_token"]),
                json={"old_password": main.settings.bootstrap_password, "new_password": STRONG})
    return client.post("/auth/login", json={"username": "admin", "password": STRONG, "device_name": device}).json()


def test_refresh_rotates_and_reuse_revokes_everything(client, monkeypatch):
    first = admin_login(client)
    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()
    assert second["refresh_token"] != first["refresh_token"]
    # A retry inside the grace window (lost response) still works.
    retry = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert retry.status_code == 200
    current = retry.json()
    # After the grace window an old token is treated as stolen.
    monkeypatch.setattr(main.settings, "refresh_reuse_grace_seconds", 0)
    with client.sessions() as db:
        for row in db.query(models.LoginSession):
            if row.rotated_at:
                row.rotated_at = row.rotated_at - timedelta(seconds=5)
        db.commit()
    assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": current["refresh_token"]}).status_code == 401
    assert client.get("/me", headers=bearer(current["access_token"])).status_code == 401


def test_logout_and_session_list(client):
    pc = admin_login(client, "PC")
    laptop = client.post("/auth/login", json={"username": "admin", "password": STRONG, "device_name": "Laptop"}).json()
    listed = client.get("/me/sessions", headers=bearer(pc["access_token"])).json()
    assert {row["device_name"] for row in listed} >= {"PC", "Laptop"}
    assert [row["current"] for row in listed if row["device_name"] == "PC"] == [True]
    assert client.post("/me/sessions/revoke-others", headers=bearer(pc["access_token"])).status_code == 200
    assert client.get("/me", headers=bearer(laptop["access_token"])).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": laptop["refresh_token"]}).status_code == 401
    assert client.post("/auth/logout", headers=bearer(pc["access_token"])).status_code == 200
    assert client.get("/me", headers=bearer(pc["access_token"])).status_code == 401


def test_renew_endpoint_is_gone(client):
    token = admin_login(client)["access_token"]
    assert client.post("/auth/renew", headers=bearer(token)).status_code in {404, 405}


def test_password_change_signs_out_all_devices(client):
    data = admin_login(client)
    headers = bearer(data["access_token"])
    assert client.post("/me/password", headers=headers,
                       json={"old_password": STRONG, "new_password": "AnotherAdmin456!Pass"}).status_code == 200
    assert client.get("/me", headers=headers).status_code == 401


def test_invite_registration_flow(client):
    headers = bearer(admin_login(client)["access_token"])
    invite = client.post("/invites", headers=headers, json={"note": "Вася"}).json()
    assert len(invite["code"]) >= 20
    body = {"code": invite["code"], "username": "vasya", "display_name": "Вася",
            "password": "VasyaStrong123!", "device_name": "Vasya PC"}
    created = client.post("/auth/register", json=body)
    assert created.status_code == 200
    token = created.json()
    assert token["refresh_token"] and not token["must_change_password"]
    friend = bearer(token["access_token"])
    spaces = client.get("/spaces", headers=friend).json()
    assert spaces, "invited user joins the inviter's space"
    assert client.get(f"/spaces/{spaces[0]['id']}/channels", headers=friend).json()
    # One-time code.
    body["username"] = "petya"
    assert client.post("/auth/register", json=body).status_code == 400
    assert client.get("/invites", headers=headers).json()[0]["used"] is True


def test_revoked_invite_cannot_be_used(client):
    headers = bearer(admin_login(client)["access_token"])
    invite = client.post("/invites", headers=headers, json={}).json()
    assert client.delete(f"/invites/{invite['id']}", headers=headers).status_code == 200
    response = client.post("/auth/register", json={"code": invite["code"], "username": "late", "display_name": "L",
                                                   "password": "LateStrong123!"})
    assert response.status_code == 400


def test_owner_cannot_be_disabled_or_reset_by_other_admin(client):
    owner = bearer(admin_login(client)["access_token"])
    invite = client.post("/invites", headers=owner, json={"is_admin": True}).json()
    second = client.post("/auth/register", json={"code": invite["code"], "username": "admin2", "display_name": "A2",
                                                 "password": "SecondAdmin123!"}).json()
    other = bearer(second["access_token"])
    assert client.post("/users/1/disable", headers=other).status_code == 403
    assert client.post("/users/1/reset-password", headers=other).status_code == 403


def test_switching_channels_kicks_from_old_media_room(client):
    headers = bearer(admin_login(client)["access_token"])
    space = client.get("/spaces", headers=headers).json()[0]
    voice = [c for c in client.get(f"/spaces/{space['id']}/channels", headers=headers).json() if c["type"] == "voice"]
    first, second = voice[0]["id"], voice[1]["id"]
    assert client.post(f"/channels/{first}/connect", headers=headers, json={}).status_code == 200
    assert client.post(f"/channels/{second}/connect", headers=headers, json={}).status_code == 200
    assert client.kicked == [(first, 1)]


def test_stale_voice_state_is_kicked(client):
    headers = bearer(admin_login(client)["access_token"])
    space = client.get("/spaces", headers=headers).json()[0]
    channel = [c for c in client.get(f"/spaces/{space['id']}/channels", headers=headers).json()
               if c["type"] == "voice"][0]["id"]
    client.post(f"/channels/{channel}/connect", headers=headers, json={})
    with client.sessions() as db:
        state = db.get(models.VoiceState, 1)
        state.updated_at = models.utcnow() - timedelta(minutes=5)
        db.commit()
    assert client.get(f"/channels/{channel}/voice", headers=headers).json() == []
    assert client.kicked == [(channel, 1)]


def test_voice_status_is_limited(client):
    headers = bearer(admin_login(client)["access_token"])
    space = client.get("/spaces", headers=headers).json()[0]
    channel = [c for c in client.get(f"/spaces/{space['id']}/channels", headers=headers).json()
               if c["type"] == "voice"][0]["id"]
    client.post(f"/channels/{channel}/connect", headers=headers, json={})
    response = client.put(f"/channels/{channel}/voice-state", headers=headers,
                          json={"muted": False, "deafened": False, "status": "x" * 50_000})
    assert response.status_code == 422


def test_unknown_user_login_takes_as_long_as_wrong_password(client):
    import time

    admin_login(client)
    started = time.perf_counter()
    client.post("/auth/login", json={"username": "nobody", "password": "whatever"})
    missing = time.perf_counter() - started
    started = time.perf_counter()
    client.post("/auth/login", json={"username": "admin", "password": "whatever"})
    wrong = time.perf_counter() - started
    assert missing > wrong * 0.5


def test_production_settings_reject_published_secrets():
    with pytest.raises(ValidationError):
        Settings(environment="production", bootstrap_password="",
                 secret_key="abc123def456789012345678901234567890abcdef1234567890abcdef12")
    with pytest.raises(ValidationError):
        Settings(environment="production", secret_key="Q" * 20 + "w8Lk2pZ9xV4mN7rT1yB3",
                 bootstrap_password="Admin12345!Local")
    with pytest.raises(ValidationError):
        Settings(environment="production", secret_key="Q" * 20 + "w8Lk2pZ9xV4mN7rT1yB3",
                 livekit_url="ws://194.85.249.53:7880", bootstrap_password="")
    ok = Settings(environment="production", secret_key="Q" * 20 + "w8Lk2pZ9xV4mN7rT1yB3",
                  livekit_url="wss://rtc.example.org", bootstrap_password="")
    assert ok.access_token_minutes == 15


def test_api_docs_closed_in_production(monkeypatch):
    import importlib

    import app.server.config as config

    monkeypatch.setenv("VOICECHAT_ENVIRONMENT", "production")
    monkeypatch.setenv("VOICECHAT_SECRET_KEY", "Q" * 20 + "w8Lk2pZ9xV4mN7rT1yB3")
    monkeypatch.setenv("VOICECHAT_BOOTSTRAP_PASSWORD", "")
    config.get_settings.cache_clear()
    try:
        fresh = importlib.reload(main)
        assert fresh.app.docs_url is None and fresh.app.openapi_url is None
    finally:
        monkeypatch.undo()
        config.get_settings.cache_clear()
        importlib.reload(main)


def test_first_start_generates_one_time_owner_password(tmp_path, monkeypatch, capsys):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(main.settings, "bootstrap_password", "")
    main.seed_database()
    printed = capsys.readouterr().out
    password = printed.split("one-time password: ")[1].split()[0]
    with main.SessionLocal() as db:
        owner = db.get(models.User, 1)
        assert owner.is_owner and owner.must_change_password
        assert main.verify_password(password, owner.password_hash)
    main.seed_database()  # second start: no new password, migrations idempotent
    assert "one-time password" not in capsys.readouterr().out
    engine.dispose()
