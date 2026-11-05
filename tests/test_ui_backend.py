"""The QML backend against a real server: sign in, live presence, chat, joining and leaving a call.

The server runs in a thread on a free port; audio devices and LiveKit are replaced with fakes.
"""
import os
import socket
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.client import settings_store  # noqa: E402
from app.client.core import call as call_module  # noqa: E402
from app.client.ui import backend as backend_module  # noqa: E402
from app.server import main, media_sessions, presence  # noqa: E402
from app.server.database import get_db  # noqa: E402
from tests.test_client_core import FakeAudio, FakeMedia  # noqa: E402

STRONG = "ChangedAdmin123!Secure"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server(tmp_path, monkeypatch):
    import uvicorn

    engine = create_engine(f"sqlite:///{tmp_path / 'ui.db'}", connect_args={"check_same_thread": False})
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    for module in (main, presence):
        monkeypatch.setattr(module, "SessionLocal", sessions)
    monkeypatch.setattr(main, "engine", engine)

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "remove_media_participant", noop)
    monkeypatch.setattr(media_sessions, "remove_media_participant", noop)
    monkeypatch.setattr(media_sessions, "set_media_publish_allowed", noop)

    def database():
        with sessions() as db:
            yield db

    main.app.dependency_overrides[get_db] = database
    main._rate_limits.clear()
    port = _free_port()
    config = uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning", ws="websockets")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    import httpx

    token = httpx.post(url + "/auth/login", json={"username": "admin",
                                                  "password": main.settings.bootstrap_password}).json()["access_token"]
    httpx.post(url + "/me/password", headers={"Authorization": "Bearer " + token},
               json={"old_password": main.settings.bootstrap_password, "new_password": STRONG})
    yield url
    srv.should_exit = True
    thread.join(timeout=5)
    main.app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ui(server, qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "client" / "settings.json")
    monkeypatch.setenv("VOICECHAT_SERVER_URL", server)
    FakeMedia.instances.clear()

    def controller(api, keys, audio_settings, listener):
        return call_module.CallController(api, keys, audio_settings, listener,
                                          media_factory=FakeMedia, audio_factory=FakeAudio)
    monkeypatch.setattr(backend_module, "CallController", controller)
    backend = backend_module.Backend()
    yield backend
    backend.shutdown()


def pump(app, predicate, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


ISOLATED = os.environ.get("VOICECHAT_UI_ISOLATED") == "1"


def test_ui_flow_in_clean_process():
    """Qt objects leaked by the widget tests can crash a later Qt test during GC; run the flow alone."""
    import subprocess
    import sys

    if ISOLATED:
        pytest.skip("already isolated")
    env = {**os.environ, "VOICECHAT_UI_ISOLATED": "1"}
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             f"{__file__}::test_sign_in_chat_and_call_flow"],
                            env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]


@pytest.mark.skipif(not ISOLATED, reason="runs in its own process via test_ui_flow_in_clean_process")
def test_sign_in_chat_and_call_flow(ui, qt_app, server):
    assert pump(qt_app, lambda: ui.view == "login")
    ui.login(server, "admin", "wrong-password", False)
    assert pump(qt_app, lambda: ui.error != "")
    ui.login(server, "admin", STRONG, True)
    assert pump(qt_app, lambda: ui.view == "main" and ui.channels.count > 0 and ui.online)
    assert ui.me["display_name"]

    # Text channel chat: sending shows the message once, links are clickable, nothing is unread.
    text_id = next(r["id"] for r in ui.channels.rows if r["type"] == "text")
    ui.openChannel(text_id)
    ui.sendMessage("Привет https://example.org")
    assert pump(qt_app, lambda: ui.messages.count == 1)
    assert '<a href="https://example.org"' in ui.messages.rows[0]["html"]

    # Join a voice channel: we appear in the list (from the server's live event) and on the stage.
    voice_id = next(r["id"] for r in ui.channels.rows if r["type"] == "voice")
    ui.joinChannel(voice_id)
    assert pump(qt_app, lambda: ui.callState == "connected")
    assert pump(qt_app, lambda: any(m["isMe"] for m in ui.channels.find(voice_id)["members"]))
    assert ui.stage.count == 1 and ui.stage.rows[0]["isMe"]

    # Switching channels moves us without a disconnected flash and leaves no ghost behind.
    other = [r["id"] for r in ui.channels.rows if r["type"] == "voice" and r["id"] != voice_id]
    if other:
        states = []
        ui.callChanged.connect(lambda: states.append(ui.callState))
        ui.joinChannel(other[0])
        assert pump(qt_app, lambda: ui.callState == "connected" and ui.callChannelId == other[0])
        assert pump(qt_app, lambda: ui.channels.find(voice_id)["memberCount"] == 0)
        assert "idle" not in states

    ui.toggleMute()
    assert ui.muted
    ui.leaveCall()
    assert pump(qt_app, lambda: not ui.inCall)
    assert pump(qt_app, lambda: all(r["memberCount"] == 0 for r in ui.channels.rows))
    # "Remember me" keeps only a refresh token, never the password.
    stored = settings_store.load_client_settings()
    assert STRONG not in str(stored)


def test_message_rendering_escapes_html_and_finds_mentions():
    html_text, mentioned = backend_module.render_message("<script>x</script> @Admin see https://a.org/x?y=1).", "admin")
    assert "<script>" not in html_text and "&lt;script&gt;" in html_text
    assert mentioned
    assert 'href="https://a.org/x?y=1"' in html_text and html_text.endswith(").")
    assert backend_module.render_message("javascript:alert(1)", "me")[0] == "javascript:alert(1)"


def test_list_model_updates_rows_in_place():
    from app.client.ui.models import ListModel

    model = ListModel(["id", "name"])
    model.set_items([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
    changed = []
    model.dataChanged.connect(lambda a, b, roles: changed.append(a.row()))
    removed = []
    model.rowsRemoved.connect(lambda parent, first, last: removed.append(first))
    model.set_items([{"id": 2, "name": "B"}, {"id": 3, "name": "c"}])
    assert [r["id"] for r in model.rows] == [2, 3]
    assert removed == [0] and changed == [0]


def test_hotkey_combos_parse():
    from app.client.ui.platform import parse_combo

    assert parse_combo("Ctrl+Shift+M") == (0x11, 0x10, ord("M"))
    assert parse_combo("Mouse4") == (0x05,)
    assert parse_combo("F13") == (0x7C,)
    assert parse_combo("") is None and parse_combo("Ctrl+Nope") is None
