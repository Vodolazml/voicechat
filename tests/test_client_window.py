import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.client import settings_store
from app.client.main import MainWindow


class FakeApi:
    def me(self) -> dict:
        return {"id": 1, "username": "admin", "display_name": "Администратор"}

    def set_device_key(self, public_key: str, fingerprint: str) -> None:
        self.public_key = public_key
        self.fingerprint = fingerprint

    def spaces(self) -> list[dict]:
        return [{"id": 1, "name": "Основное пространство"}]

    def channels(self, space_id: int) -> list[dict]:
        return [{"id": 1, "space_id": space_id, "name": "Лобби", "type": "voice"}]

    def voice_states(self, channel_id: int) -> list[dict]:
        return []

    def ping_ms(self) -> int:
        return 1


def test_main_window_initializes_client_settings_before_e2ee(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.json")
    app = QApplication.instance() or QApplication([])

    window = MainWindow(FakeApi())

    assert "e2ee_identity_private_key" in window.client_settings
    window.close()
    app.processEvents()
