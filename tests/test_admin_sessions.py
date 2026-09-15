import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.server import main
from app.server.database import get_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={'check_same_thread': False})
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    def database():
        with sessions() as db:
            yield db
    main.app.dependency_overrides[get_db] = database
    main._rate_limits.clear()
    try:
        with TestClient(main.app) as client:
            yield client
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()


def admin(client):
    data = client.post('/auth/login', json={'username': 'admin', 'password': main.settings.bootstrap_password}).json()
    headers = {'Authorization': 'Bearer ' + data['access_token']}
    assert client.get('/spaces', headers=headers).status_code == 403
    assert client.post('/me/password', headers=headers, json={
        'old_password': main.settings.bootstrap_password, 'new_password': 'ChangedAdmin123!Secure'}).status_code == 200
    data = client.post('/auth/login', json={'username': 'admin', 'password': 'ChangedAdmin123!Secure'}).json()
    return {'Authorization': 'Bearer ' + data['access_token']}


def test_generated_password_refresh_and_disable(client):
    headers = admin(client)
    response = client.post('/users', headers=headers, json={'username': 'friend', 'display_name': 'Friend'})
    assert response.status_code == 200
    user = response.json()
    assert len(user['temporary_password']) >= 24
    data = client.post('/auth/login', json={'username': 'friend', 'password': user['temporary_password']}).json()
    assert not data['refresh_token']
    temporary_headers = {'Authorization': 'Bearer ' + data['access_token']}
    assert client.get('/spaces', headers=temporary_headers).status_code == 403
    assert client.post('/me/password', headers=temporary_headers, json={
        'old_password': user['temporary_password'], 'new_password': 'FriendPermanent123!'}).status_code == 200
    data = client.post('/auth/login', json={'username': 'friend', 'password': 'FriendPermanent123!'}).json()
    assert client.post('/auth/refresh', json={'refresh_token': data['refresh_token']}).status_code == 200
    assert client.post(f"/users/{user['id']}/disable", headers=headers).status_code == 200
    assert client.post('/auth/refresh', json={'refresh_token': data['refresh_token']}).status_code == 401
    assert client.get('/me', headers={'Authorization': 'Bearer ' + data['access_token']}).status_code == 401


def test_admin_cannot_disable_self(client):
    assert client.post('/users/1/disable', headers=admin(client)).status_code == 400
