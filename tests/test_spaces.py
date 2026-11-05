"""Open sign-up, own spaces joined by invite code, the server owner sees everything."""
from tests.test_presence import bearer, client, setup_admin_and_friend  # noqa: F401


def signup(client, username):
    response = client.post("/auth/register", json={"username": username, "display_name": username.title(),
                                                   "password": "Strong" + username + "123!"})
    assert response.status_code == 200, response.text
    return bearer(response.json()["access_token"])


def test_anyone_signs_up_creates_a_space_and_others_join_by_code(client):
    admin, _, main_space, _ = setup_admin_and_friend(client)
    alice = signup(client, "alice")
    bob = signup(client, "bob")
    assert client.get("/spaces", headers=alice).json() == []

    created = client.post("/spaces", headers=alice, json={"name": "Команда Алисы"}).json()
    assert created["can_manage"] and created["is_mine"] and len(created["invite_code"]) == 9
    channels = client.get(f"/spaces/{created['id']}/channels", headers=alice).json()
    assert {c["type"] for c in channels} == {"voice", "text"}

    # Bob cannot see or use it without the code; a wrong code fails.
    assert client.get(f"/spaces/{created['id']}/channels", headers=bob).status_code == 403
    assert client.post("/spaces/join", headers=bob, json={"code": "ZZZZ-ZZZZ"}).status_code == 404
    joined = client.post("/spaces/join", headers=bob, json={"code": created["invite_code"].lower()}).json()
    assert joined["id"] == created["id"] and not joined["can_manage"] and joined["invite_code"] == created["invite_code"]
    assert len(client.get(f"/spaces/{created['id']}/channels", headers=bob).json()) == 2
    # Members only see their own spaces, never the main one.
    assert [s["id"] for s in client.get("/spaces", headers=bob).json()] == [created["id"]]

    # Only the creator manages it.
    assert client.post("/channels", headers=bob, json={"space_id": created["id"], "name": "Бобов",
                                                       "type": "voice"}).status_code == 403
    assert client.post("/channels", headers=alice, json={"space_id": created["id"], "name": "Второй",
                                                         "type": "voice"}).status_code == 200
    rotated = client.post(f"/spaces/{created['id']}/invite-code", headers=alice).json()["invite_code"]
    assert rotated != created["invite_code"]
    carol = signup(client, "carol")
    assert client.post("/spaces/join", headers=carol, json={"code": created["invite_code"]}).status_code == 404

    # The server owner sees every space and every channel, including ones made later.
    seen = {s["id"] for s in client.get("/spaces", headers=admin).json()}
    assert {main_space, created["id"]} <= seen
    assert len(client.get(f"/spaces/{created['id']}/channels", headers=admin).json()) == 3

    # A member can leave; the creator can delete.
    assert client.delete(f"/spaces/{created['id']}/members/me", headers=bob).status_code == 200
    assert client.get("/spaces", headers=bob).json() == []
    assert client.delete(f"/spaces/{created['id']}", headers=carol).status_code == 403
    assert client.delete(f"/spaces/{created['id']}", headers=alice).status_code == 200
    assert client.get("/spaces", headers=alice).json() == []


def test_sign_up_with_a_space_code_joins_that_space(client):
    signup(client, "dave")
    dave = bearer(client.post("/auth/login", json={"username": "dave", "password": "Strongdave123!"}).json()[
        "access_token"])
    space = client.post("/spaces", headers=dave, json={"name": "Дэйв"}).json()
    response = client.post("/auth/register", json={"code": space["invite_code"], "username": "erin",
                                                   "display_name": "Erin", "password": "StrongErin123!"})
    erin = bearer(response.json()["access_token"])
    assert [s["id"] for s in client.get("/spaces", headers=erin).json()] == [space["id"]]


def test_closed_registration(client, monkeypatch):
    from app.server import main

    monkeypatch.setattr(main.settings, "open_registration", False)
    response = client.post("/auth/register", json={"username": "frank", "display_name": "F",
                                                   "password": "StrongFrank123!"})
    assert response.status_code == 403


def test_manager_reorders_channels_and_members_cannot(client):
    alice = signup(client, "alice")
    bob = signup(client, "bob")
    space = client.post("/spaces", headers=alice, json={"name": "Порядок"}).json()
    client.post("/spaces/join", headers=bob, json={"code": space["invite_code"]})
    for name in ("Второй", "Третий"):
        client.post("/channels", headers=alice, json={"space_id": space["id"], "name": name, "type": "voice"})
    voice = [c for c in client.get(f"/spaces/{space['id']}/channels", headers=bob).json() if c["type"] == "voice"]
    wanted = [voice[2]["id"], voice[0]["id"], voice[1]["id"]]
    assert client.put(f"/spaces/{space['id']}/channels/order", headers=bob,
                      json={"channel_ids": wanted}).status_code == 403
    assert client.put(f"/spaces/{space['id']}/channels/order", headers=alice,
                      json={"channel_ids": wanted}).status_code == 200
    seen = [c["id"] for c in client.get(f"/spaces/{space['id']}/channels", headers=bob).json() if c["type"] == "voice"]
    assert seen == wanted
