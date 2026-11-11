"""Update downloads: four ranges at once, resumed piece by piece, one stream when the server can't do ranges."""
import hashlib
import re

import httpx
import pytest

from app.client import updater

URL = "https://chat.example.org/downloads/PrivateVoiceChat-9.9.9-setup.exe"


def fake_server(monkeypatch, body: bytes, ranges: bool = True, seen: list | None = None, fail_once: set | None = None):
    seen = seen if seen is not None else []
    fail_once = fail_once if fail_once is not None else set()

    def handler(request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("range")
        seen.append((request.method, rng))
        headers = {"accept-ranges": "bytes"} if ranges else {}
        if request.method == "HEAD":
            return httpx.Response(200, headers={**headers, "content-length": str(len(body))})
        if ranges and rng:
            start, end = re.match(r"bytes=(\d+)-(\d*)", rng).groups()
            start, end = int(start), int(end) if end else len(body) - 1
            if start in fail_once:
                fail_once.discard(start)
                raise httpx.ReadError("connection reset")
            return httpx.Response(206, content=body[start:end + 1], headers=headers)
        return httpx.Response(200, content=body, headers=headers)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(updater.httpx, "Client", lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr(updater.httpx, "stream",
                        lambda method, url, **kw: real_client(transport=transport, follow_redirects=True)
                        .stream(method, url, headers=kw.get("headers")))
    return seen


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "UPDATE_DIR", tmp_path)
    monkeypatch.setattr(updater, "verify_release", lambda *args: None)
    monkeypatch.setattr(updater, "PARALLEL_MIN_BYTES", 1000)
    body = bytes(range(256)) * 400  # 102 400 bytes
    info = updater.UpdateInfo("9.9.9", True, False, URL, hashlib.sha256(body).hexdigest(), "sig")
    return tmp_path, body, info


def test_downloads_in_four_ranges_and_joins_them(setup, monkeypatch):
    tmp_path, body, info = setup
    seen = fake_server(monkeypatch, body)
    progress = []
    path = updater.download_update(info, lambda done, total: progress.append((done, total)))
    assert path.read_bytes() == body
    gets = sorted(r for m, r in seen if m == "GET")
    assert len(gets) == 4 and all(r.startswith("bytes=") for r in gets)
    assert progress[-1] == (len(body), len(body))
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]   # no pieces left behind


def test_broken_piece_resumes_where_it_stopped(setup, monkeypatch):
    tmp_path, body, info = setup
    quarter = len(body) // 4
    partial = tmp_path / "PrivateVoiceChat-9.9.9-setup.partial"
    piece = updater._part_path(partial, 1)
    piece.write_bytes(body[quarter:quarter + 1000])        # piece 1 had 1000 bytes before the app closed
    seen = fake_server(monkeypatch, body, fail_once={quarter * 2})  # piece 2 breaks once
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    path = updater.download_update(info)
    assert path.read_bytes() == body
    ranges = [r for m, r in seen if m == "GET"]
    assert f"bytes={quarter + 1000}-{quarter * 2 - 1}" in ranges   # continued, not restarted
    assert ranges.count(f"bytes={quarter * 2}-{quarter * 3 - 1}") == 2


def test_server_without_ranges_gets_one_stream(setup, monkeypatch):
    _, body, info = setup
    seen = fake_server(monkeypatch, body, ranges=False)
    path = updater.download_update(info)
    assert path.read_bytes() == body
    assert [m for m, _ in seen] == ["HEAD", "GET"]


def test_corrupt_download_is_deleted(setup, monkeypatch):
    tmp_path, body, info = setup
    fake_server(monkeypatch, body[:-1] + b"\x00")
    with pytest.raises(RuntimeError, match="Хэш"):
        updater.download_update(info)
    assert list(tmp_path.iterdir()) == []
