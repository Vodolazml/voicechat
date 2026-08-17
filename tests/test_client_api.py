import pytest

from app.client.api import ApiError, normalize_base_url
from app.client.client_config import load_client_config
from app.client.updater import update_file_name


def test_normalizes_remote_server_to_https() -> None:
    assert normalize_base_url("voice.example.com") == "https://voice.example.com"


def test_allows_local_http_for_development() -> None:
    assert normalize_base_url("127.0.0.1:8765") == "http://127.0.0.1:8765"


def test_rejects_remote_http() -> None:
    with pytest.raises(ApiError):
        normalize_base_url("http://voice.example.com")


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
