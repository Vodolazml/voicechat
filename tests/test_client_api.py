import pytest

from app.client.api import ApiClient, ApiError, normalize_base_url, validate_media_url
from app.client.client_config import load_client_config
from app.client.updater import update_file_name


def test_normalizes_remote_server_to_https() -> None:
    assert normalize_base_url("voice.example.com") == "https://voice.example.com"


def test_allows_local_http_for_development() -> None:
    assert normalize_base_url("127.0.0.1:8765") == "http://127.0.0.1:8765"


def test_rejects_remote_http() -> None:
    with pytest.raises(ApiError):
        normalize_base_url("http://voice.example.com")


def test_remote_http_is_never_allowed() -> None:
    with pytest.raises(ApiError):
        normalize_base_url("http://203.0.113.10:8765")


def test_media_url_must_be_wss() -> None:
    assert validate_media_url("wss://rtc.example.com") == "wss://rtc.example.com"
    assert validate_media_url("ws://127.0.0.1:7880") == "ws://127.0.0.1:7880"
    with pytest.raises(ApiError):
        validate_media_url("ws://194.85.249.53:7880")


def test_rotated_refresh_token_is_reported() -> None:
    seen = []
    client = ApiClient(on_refresh_token=seen.append)
    client._accept_tokens({"access_token": "a", "refresh_token": "r1"})
    client._accept_tokens({"access_token": "b", "refresh_token": "r1"})
    client._accept_tokens({"access_token": "c", "refresh_token": "r2"})
    assert seen == ["r1", "r2"]


def test_loads_packaged_client_config(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("VOICECHAT_SERVER_URL", raising=False)
    monkeypatch.delenv("VOICECHAT_LOCK_SERVER_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "client_config.json").write_text(
        '{"server_url":"https://voice.example.com","lock_server_url":true}',
        encoding="utf-8",
    )

    config = load_client_config()

    assert config.server_url == "https://voice.example.com"
    assert config.lock_server_url is True


def test_update_file_name_uses_url_name() -> None:
    assert update_file_name("https://voice.example.com/downloads/app.zip", "0.1.1") == "app.zip"
