import pytest

from app.client.api import ApiError, normalize_base_url


def test_normalizes_remote_server_to_https() -> None:
    assert normalize_base_url("voice.example.com") == "https://voice.example.com"


def test_allows_local_http_for_development() -> None:
    assert normalize_base_url("127.0.0.1:8765") == "http://127.0.0.1:8765"


def test_rejects_remote_http() -> None:
    with pytest.raises(ApiError):
        normalize_base_url("http://voice.example.com")
