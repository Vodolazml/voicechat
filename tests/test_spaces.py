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


def _png() -> bytes:
    import struct
    import zlib

    raw = b"\x00\xff\x00\x00"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_moderator_does_all_but_create_and_delete_channels(client):
    alice = signup(client, "alice")
    bob = signup(client, "bob")
    carol = signup(client, "carol")
    space = client.post("/spaces", headers=alice, json={"name": "Модерация"}).json()
    sid = space["id"]
    for who in (bob, carol):
        client.post("/spaces/join", headers=who, json={"code": space["invite_code"]})
    bob_id = client.get("/me", headers=bob).json()["id"]
    alice_id = client.get("/me", headers=alice).json()["id"]

    # Only the creator appoints moderators.
    assert client.put(f"/spaces/{sid}/moderators/{bob_id}", headers=carol, json={"moderator": True}).status_code == 403
    members = client.put(f"/spaces/{sid}/moderators/{bob_id}", headers=alice, json={"moderator": True}).json()
    assert {m["id"]: m["role"] for m in members}[bob_id] == "moderator"
    assert {m["id"]: m["role"] for m in members}[alice_id] == "admin"
    me_as_bob = next(s for s in client.get("/spaces", headers=bob).json() if s["id"] == sid)
    assert me_as_bob["role"] == "moderator" and me_as_bob["can_manage"]

    channel = next(c for c in client.get(f"/spaces/{sid}/channels", headers=bob).json() if c["type"] == "voice")
    # A moderator renames channels and the space, changes settings, rotates the code...
    assert client.patch(f"/channels/{channel['id']}", headers=bob, json={"name": "Болталка"}).status_code == 200
    updated = client.patch(f"/spaces/{sid}", headers=bob, json={"name": "Новое имя", "max_share_quality": "1080p30",
                                                               "camera_allowed": False})
    assert updated.status_code == 200, updated.text
    assert updated.json()["max_share_quality"] == "1080p30" and not updated.json()["camera_allowed"]
    assert client.post(f"/spaces/{sid}/invite-code", headers=bob).status_code == 200
    # ...but cannot create or delete channels, delete the space, set its picture or appoint moderators.
    assert client.post("/channels", headers=bob, json={"space_id": sid, "name": "Ещё", "type": "voice"}).status_code == 403
    assert client.delete(f"/channels/{channel['id']}", headers=bob).status_code == 403
    assert client.delete(f"/spaces/{sid}", headers=bob).status_code == 403
    assert client.put(f"/spaces/{sid}/avatar", headers={**bob, "Content-Type": "image/png"},
                      content=_png()).status_code == 403
    assert client.put(f"/spaces/{sid}/moderators/{bob_id}", headers=bob, json={"moderator": False}).status_code == 403
    # Plain members change nothing.
    assert client.patch(f"/spaces/{sid}", headers=carol, json={"name": "Взлом"}).status_code == 403
    assert client.patch(f"/spaces/{sid}", headers=alice, json={"max_share_quality": "8k"}).status_code == 422


def test_avatars_for_people_and_spaces(client):
    alice = signup(client, "alice")
    bob = signup(client, "bob")
    space = client.post("/spaces", headers=alice, json={"name": "Картинки"}).json()
    client.post("/spaces/join", headers=bob, json={"code": space["invite_code"]})
    picture = _png()

    assert client.put("/me/avatar", headers=alice, content=b"not a picture").status_code == 415
    assert client.put("/me/avatar", headers=alice, content=b"\x89PNG\r\n\x1a\n" + b"0" * 70_000).status_code == 413
    version = client.put("/me/avatar", headers=alice, content=picture).json()["avatar_version"]
    assert version >= 1
    alice_id = client.get("/me", headers=alice).json()["id"]
    assert client.get("/me", headers=alice).json()["avatar_version"] == version
    got = client.get(f"/avatars/user/{alice_id}", headers=bob)
    assert got.status_code == 200 and got.content == picture and got.headers["content-type"] == "image/png"
    members = client.get(f"/spaces/{space['id']}/members", headers=bob).json()
    assert {m["id"]: m["avatar_version"] for m in members}[alice_id] == version

    assert client.put(f"/spaces/{space['id']}/avatar", headers=alice, content=picture).status_code == 200
    seen = next(s for s in client.get("/spaces", headers=bob).json() if s["id"] == space["id"])
    assert seen["avatar_version"] >= 1
    assert client.get(f"/avatars/space/{space['id']}", headers=bob).content == picture
    outsider = signup(client, "carol")
    assert client.get(f"/avatars/space/{space['id']}", headers=outsider).status_code == 404
    # An empty body removes the picture.
    assert client.put("/me/avatar", headers=alice, content=b"").json()["avatar_version"] == 0
    assert client.get(f"/avatars/user/{alice_id}", headers=bob).status_code == 404
