from datetime import timedelta

import pytest

from app.server import files, main, models
from tests.test_presence import client, setup_admin_and_friend  # noqa: F401


@pytest.fixture(autouse=True)
def file_store(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "files_dir", str(tmp_path / "files"))
    monkeypatch.setattr(main.settings, "max_file_mb", 1)
    monkeypatch.setattr(main.settings, "min_free_disk_mb", 100)
    return tmp_path / "files"


def _text_channel(client, headers, space):
    return next(c["id"] for c in client.get(f"/spaces/{space}/channels", headers=headers).json()
                if c["type"] == "text")


def test_any_file_goes_through_the_chat_encrypted(client, file_store):
    admin, friend, space, _ = setup_admin_and_friend(client)
    channel = _text_channel(client, admin, space)
    payload = bytes(range(256)) * 400  # 100 KB of binary, not an image
    up = client.put(f"/channels/{channel}/files", params={"name": "отчёт v2.7z"}, headers=admin, content=payload)
    assert up.status_code == 200, up.text
    attachment = up.json()
    assert attachment["name"] == "отчёт v2.7z" and attachment["size"] == len(payload)
    stored = (file_store / f"{attachment['id']}.bin").read_bytes()
    assert payload[:64] not in stored, "the file is encrypted on disk"

    message = client.post(f"/channels/{channel}/messages", headers=admin,
                          json={"attachment_id": attachment["id"]}).json()
    assert message["body"] == "" and message["attachment"]["name"] == "отчёт v2.7z"
    listed = client.get(f"/channels/{channel}/messages", headers=friend).json()
    assert listed[-1]["attachment"]["id"] == attachment["id"]

    got = client.get(f"/files/{attachment['id']}", headers=friend)
    assert got.status_code == 200 and got.content == payload
    assert "filename*=UTF-8''" in got.headers["content-disposition"]

    # Deleting the message deletes the file.
    assert client.delete(f"/messages/{message['id']}", headers=admin).status_code == 200
    assert client.get(f"/files/{attachment['id']}", headers=friend).status_code == 404
    assert not (file_store / f"{attachment['id']}.bin").exists()


def test_too_big_file_and_foreign_attachment_are_refused(client):
    admin, friend, space, _ = setup_admin_and_friend(client)
    channel = _text_channel(client, admin, space)
    big = client.put(f"/channels/{channel}/files", params={"name": "big.bin"}, headers=admin,
                     content=b"x" * (1024 * 1024 + 1))
    assert big.status_code == 413
    mine = client.put(f"/channels/{channel}/files", params={"name": "a.txt"}, headers=admin, content=b"hi").json()
    stolen = client.post(f"/channels/{channel}/messages", headers=friend, json={"attachment_id": mine["id"]})
    assert stolen.status_code == 400
    assert client.post(f"/channels/{channel}/messages", headers=admin, json={"body": ""}).status_code == 400


def test_unsent_uploads_are_purged_after_a_day(client, file_store):
    admin, _, space, _ = setup_admin_and_friend(client)
    channel = _text_channel(client, admin, space)
    orphan = client.put(f"/channels/{channel}/files", params={"name": "a.txt"}, headers=admin, content=b"hi").json()
    with main.SessionLocal() as db:
        db.query(models.Attachment).filter_by(id=orphan["id"]).update(
            {"created_at": models.utcnow() - timedelta(days=2)})
        db.commit()
        assert files.purge_orphans(db) == 1
    assert not (file_store / f"{orphan['id']}.bin").exists()


def test_file_names_are_made_safe():
    assert files.clean_name('..\\..\\evil:"name".exe') == "_.._evil_name_.exe"
    assert files.clean_name("   ") == "файл"
