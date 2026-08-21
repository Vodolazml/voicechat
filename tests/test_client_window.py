import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QDialog

from app.client import settings_store
from app.client.client_config import ClientConfig
from app.client.main import LoginDialog, MainWindow
from app.version import APP_VERSION


class FakeApi:
    def __init__(self) -> None:
        self.base_url = "http://127.0.0.1:8765"

    def set_base_url(self, value: str, *, allow_insecure_http: bool = False) -> None:
        self.base_url = value.rstrip("/")

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

    def voice_ws_url(self, channel_id: int) -> str:
        return f"ws://test/voice/ws/{channel_id}"

    def ws_headers(self) -> dict:
        return {}

    def check_update(self):
        raise AssertionError("update check is not expected")

    def login(self, username: str, password: str) -> dict:
        raise AssertionError("login is not expected")


def test_main_window_initializes_client_settings_before_e2ee(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.json")
    app = QApplication.instance() or QApplication([])

    window = MainWindow(FakeApi())

    assert "e2ee_identity_private_key" in window.client_settings
    assert window.version_label.text() == f"v{APP_VERSION}"
    window.close()
    app.processEvents()


def test_start_audio_passes_channel_and_user_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.json")
    app = QApplication.instance() or QApplication([])
    captured = {}

    class FakeE2EE:
        def ensure_outgoing_key(self) -> tuple[str, bytes]:
            return "key-1", b"1" * 32

    class FakeVoiceAudioClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def start(self) -> None:
            captured["started"] = True

        def stop(self) -> None:
            captured["stopped"] = True

    monkeypatch.setattr("app.client.main.VoiceAudioClient", FakeVoiceAudioClient)
    window = MainWindow(FakeApi())
    monkeypatch.setattr(window, "ensure_e2ee_for_channel", lambda channel_id, force=False: FakeE2EE())

    window.start_audio(7)

    assert captured["channel_id"] == 7
    assert captured["user_id"] == 1
    assert captured["started"] is True
    window.close()
    app.processEvents()


def test_noise_settings_do_not_restart_active_audio(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.json")
    app = QApplication.instance() or QApplication([])

    class FakeAudio:
        def stop(self) -> None:
            raise AssertionError("audio restart is not expected")

    class FakeDialog:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def exec(self) -> int:
            return QDialog.Accepted

        def selected_devices(self) -> tuple[None, None]:
            return None, None

        def noise_suppression_enabled(self) -> bool:
            return False

        def selected_threshold(self) -> int:
            return 620

    monkeypatch.setattr("app.client.main.AudioSettingsDialog", FakeDialog)
    window = MainWindow(FakeApi())
    window.connected_channel_id = 7
    window.voice_audio = FakeAudio()
    monkeypatch.setattr(window, "start_audio", lambda channel_id: (_ for _ in ()).throw(AssertionError("audio restart is not expected")))

    window.open_audio_settings()

    assert window.noise_suppression is False
    assert window.noise_threshold == 620
    assert window.client_settings["noise_suppression"] is False
    assert window.client_settings["noise_threshold"] == 620
    window.voice_audio = None
    window.connected_channel_id = None
    window.close()
    app.processEvents()


def test_device_settings_restart_streams_without_rejoining_voice(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.json")
    app = QApplication.instance() or QApplication([])
    calls = {}

    class FakeAudio:
        def restart_devices(self, input_device_id, output_device_id) -> None:
            calls["restart_devices"] = (input_device_id, output_device_id)

        def stop(self) -> None:
            raise AssertionError("full audio restart is not expected")

    class FakeDialog:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def exec(self) -> int:
            return QDialog.Accepted

        def selected_devices(self) -> tuple[int, int]:
            return 11, 12

        def noise_suppression_enabled(self) -> bool:
            return True

        def selected_threshold(self) -> int:
            return 500

    monkeypatch.setattr("app.client.main.AudioSettingsDialog", FakeDialog)
    window = MainWindow(FakeApi())
    window.connected_channel_id = 7
    window.voice_audio = FakeAudio()
    monkeypatch.setattr(window, "start_audio", lambda channel_id: (_ for _ in ()).throw(AssertionError("full audio restart is not expected")))

    window.open_audio_settings()

    assert calls["restart_devices"] == (11, 12)
    assert window.input_device_id == 11
    assert window.output_device_id == 12
    window.voice_audio = None
    window.connected_channel_id = None
    window.close()
    app.processEvents()


def test_login_dialog_locked_server_is_not_editable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(
        "app.client.main.load_client_config",
        lambda: ClientConfig(
            server_url="http://72.35.246.230:8765",
            lock_server_url=True,
            allow_insecure_http=True,
        ),
    )
    app = QApplication.instance() or QApplication([])

    dialog = LoginDialog(FakeApi())

    assert isinstance(dialog.server, QLabel)
    assert not isinstance(dialog.server, QLineEdit)
    assert dialog.server.text() == "http://72.35.246.230:8765"
    dialog.close()
    app.processEvents()


def test_login_dialog_loads_remembered_password(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.json")
    settings_store.save_client_settings(
        {
            "remember_me": True,
            "remembered_username": "admin",
            "remembered_password_dpapi": "secret",
        }
    )
    monkeypatch.setattr("app.client.main.unprotect_text", lambda value: "Admin12345!")
    monkeypatch.setattr(
        "app.client.main.load_client_config",
        lambda: ClientConfig(
            server_url="http://72.35.246.230:8765",
            lock_server_url=True,
            allow_insecure_http=True,
        ),
    )
    app = QApplication.instance() or QApplication([])

    dialog = LoginDialog(FakeApi())

    assert dialog.username.text() == "admin"
    assert dialog.password.text() == "Admin12345!"
    dialog.close()
    app.processEvents()
