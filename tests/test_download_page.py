"""Front page with one download button, and a stable link to the newest installer."""
from tests.test_presence import client  # noqa: F401
from app.server import download_page


def test_front_page_without_installer(client):
    page = client.get("/")
    assert page.status_code == 200 and "Установщик скоро появится" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert client.get("/download", follow_redirects=False).status_code == 404


def test_front_page_links_the_latest_installer(client, monkeypatch):
    settings = download_page.get_settings()
    url = "https://chat.example.org/downloads/PrivateVoiceChat-0.3.1-setup.exe"
    monkeypatch.setattr(settings, "client_download_url", url)
    monkeypatch.setattr(settings, "client_latest_version", "0.3.1")
    page = client.get("/")
    assert "Скачать для Windows" in page.text and "Версия 0.3.1" in page.text
    redirect = client.get("/download", follow_redirects=False)
    assert redirect.status_code == 302 and redirect.headers["location"] == url
