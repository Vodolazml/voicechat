from __future__ import annotations

import platform
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable
from urllib.parse import urlparse
from threading import Lock

import httpx

from app.version import APP_VERSION
from .updater import UpdateInfo


class ApiError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def normalize_base_url(value: str, default: str = "http://127.0.0.1:8765") -> str:
    """Remote servers are reachable only over HTTPS; plain HTTP is allowed for a server on this computer."""
    raw = value.strip().rstrip("/") or default
    if "://" not in raw:
        raw = f"{'http' if raw.startswith(('127.', 'localhost')) else 'https'}://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ApiError("Адрес сервера должен быть вида https://chat.example.com")
    if parsed.scheme == "http" and (parsed.hostname or "") not in LOCAL_HOSTS:
        raise ApiError("Сервер должен работать по HTTPS. Укажите адрес вида https://chat.example.com")
    return raw


def validate_media_url(value: str) -> str:
    """LiveKit signalling carries the E2EE room join; it must be encrypted too."""
    parsed = urlparse(value)
    if parsed.scheme == "wss" or (parsed.scheme == "ws" and (parsed.hostname or "") in LOCAL_HOSTS):
        return value
    raise ApiError("Медиасервер должен работать по wss://. Обратитесь к администратору сервера")


def default_device_name() -> str:
    """Shown in the user's list of signed-in devices."""
    host = (platform.node() or "Компьютер")[:60]
    system = platform.system() or ""
    return f"{host} · {system}" if system else host


@dataclass
class ApiClient:
    base_url: str = "http://127.0.0.1:8765"
    token: str | None = None
    refresh_token: str = ""
    # Called with the new refresh token after every rotation, so "remember me" never keeps a spent token.
    on_refresh_token: Callable[[str], None] | None = None
    _refresh_lock: Lock = field(default_factory=Lock, init=False)
    _client: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=8), init=False)

    def set_base_url(self, value: str) -> None:
        self.base_url = normalize_base_url(value, self.base_url)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(
                method,
                f"{self.base_url.rstrip('/')}{path}",
                headers=self._headers(),
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise ApiError("Нет связи с сервером. Проверьте интернет") from exc
        if response.status_code == 401 and self.refresh_token and not path.startswith('/auth/'):
            failed_token = self.token
            with self._refresh_lock:
                # Another thread may have refreshed while we waited; reuse its result.
                if self.token == failed_token:
                    self.refresh_session(self.refresh_token)
            try:
                response = self._client.request(method, f"{self.base_url.rstrip('/')}{path}",
                                                headers=self._headers(), **kwargs)
            except httpx.HTTPError as exc:
                raise ApiError("Сервер недоступен") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "Ошибка запроса")
            except ValueError:
                detail = response.text or "Ошибка запроса"
            raise ApiError(str(detail), response.status_code)
        if not response.content:
            return None
        return response.json()

    def login(self, username: str, password: str) -> dict[str, Any]:
        data = self.request("POST", "/auth/login", json={
            "username": username, "password": password, "device_name": default_device_name()})
        self._accept_tokens(data)
        return data

    def register(self, code: str, username: str, display_name: str, password: str) -> dict[str, Any]:
        data = self.request("POST", "/auth/register", json={
            "code": code, "username": username, "display_name": display_name, "password": password,
            "device_name": default_device_name()})
        self._accept_tokens(data)
        return data

    def refresh_session(self, token):
        data = self.request('POST', '/auth/refresh', json={'refresh_token': token})
        self._accept_tokens(data)
        return data

    def _accept_tokens(self, data: dict[str, Any]) -> None:
        self.token = data["access_token"]
        new_refresh = data.get("refresh_token", "")
        changed = new_refresh != self.refresh_token
        self.refresh_token = new_refresh
        if changed and new_refresh and self.on_refresh_token:
            self.on_refresh_token(new_refresh)

    def logout(self) -> None:
        try:
            self.request("POST", "/auth/logout")
        finally:
            self.token = None
            self.refresh_token = ""

    def sessions(self) -> list[dict[str, Any]]:
        return self.request("GET", "/me/sessions")

    def revoke_session(self, session_id: int) -> None:
        self.request("DELETE", f"/me/sessions/{session_id}")

    def revoke_other_sessions(self) -> None:
        self.request("POST", "/me/sessions/revoke-others")

    def create_invite(self, note: str = "", is_admin: bool = False) -> dict[str, Any]:
        return self.request("POST", "/invites", json={"note": note, "is_admin": is_admin})

    def media_token(self, channel_id: int) -> dict[str, Any]:
        """Single LiveKit connection for voice and screen (server 0.3+)."""
        data = self.request("POST", f"/channels/{channel_id}/media-token")
        validate_media_url(str(data.get("url", "")))
        return data

    def space_voice(self, space_id: int) -> list[dict[str, Any]]:
        return self.request("GET", f"/spaces/{space_id}/voice")

    def update_channel(self, channel_id: int, **changes: Any) -> dict[str, Any]:
        return self.request("PATCH", f"/channels/{channel_id}", json=changes)

    def delete_channel(self, channel_id: int) -> None:
        self.request("DELETE", f"/channels/{channel_id}")

    def move_user(self, user_id: int, channel_id: int) -> None:
        self.request("POST", f"/voice/{user_id}/move", json={"channel_id": channel_id})

    def kick_user(self, user_id: int) -> None:
        self.request("POST", f"/voice/{user_id}/kick")

    def server_mute(self, user_id: int, muted: bool) -> None:
        self.request("POST", f"/voice/{user_id}/server-mute", json={"muted": muted})

    def media_credentials(self, channel_id: int, *, audio: bool) -> dict[str, Any]:
        data = self.request("POST", f"/channels/{channel_id}/video-token" + ("?audio=true" if audio else ""))
        validate_media_url(str(data.get("url", "")))
        return data

    def ping_ms(self) -> int:
        samples = []
        for _ in range(2):
            started = perf_counter()
            self.request("GET", "/health")
            samples.append(int((perf_counter() - started) * 1000))
        return min(samples)

    def check_update(self) -> UpdateInfo:
        data = self.request("GET", f"/client/update?version={APP_VERSION}")
        return UpdateInfo(
            latest_version=str(data.get("latest_version", APP_VERSION)),
            update_available=bool(data.get("update_available", False)),
            required=bool(data.get("required", False)),
            download_url=str(data.get("download_url", "")),
            sha256=str(data.get("sha256", "")),
            signature=str(data.get("signature", "")),
            release_notes_url=str(data.get("release_notes_url", "")),
        )

    def voice_ws_url(self, channel_id: int) -> str:
        scheme = "wss" if self.base_url.startswith("https://") else "ws"
        host = urlparse(self.base_url).netloc
        return f"{scheme}://{host}/voice/ws/{channel_id}"

    def screen_ws_url(self, channel_id: int) -> str:
        scheme = "wss" if self.base_url.startswith("https://") else "ws"
        host = urlparse(self.base_url).netloc
        return f"{scheme}://{host}/screen/ws/{channel_id}"

    def ws_headers(self) -> dict[str, str]:
        return {**self._headers(), "X-VoiceChat-Protocol": "2"}

    def set_device_key(self, public_key: str, fingerprint: str) -> None:
        self.request("PUT", "/me/device-key", json={"public_key": public_key, "fingerprint": fingerprint})

    def e2ee_state(self, channel_id: int) -> dict[str, Any]:
        return self.request("GET", f"/channels/{channel_id}/e2ee")

    def publish_sender_key(self, channel_id: int, key_id: str, envelopes: dict[int, str]) -> None:
        self.request("PUT", f"/channels/{channel_id}/e2ee/sender-key", json={"key_id": key_id, "envelopes": envelopes})

    def me(self) -> dict[str, Any]:
        return self.request("GET", "/me")

    def change_password(self, old_password: str, new_password: str) -> None:
        self.request("POST", "/me/password", json={"old_password": old_password, "new_password": new_password})

    def users(self) -> list[dict[str, Any]]:
        return self.request("GET", "/users")

    def create_user(self, username: str, display_name: str, password: str, is_admin: bool) -> dict[str, Any]:
        return self.request(
            "POST",
            "/users",
            json={
                "username": username,
                "display_name": display_name,
                "temporary_password": password or None,
                "is_admin": is_admin,
            },
        )

    def spaces(self) -> list[dict[str, Any]]:
        return self.request("GET", "/spaces")

    def create_space(self, name: str) -> dict[str, Any]:
        return self.request("POST", "/spaces", json={"name": name})

    def channels(self, space_id: int) -> list[dict[str, Any]]:
        return self.request("GET", f"/spaces/{space_id}/channels")

    def create_channel(self, space_id: int, name: str, channel_type: str, is_private: bool = False,
                       user_limit: int = 0) -> dict[str, Any]:
        return self.request("POST", "/channels", json={"space_id": space_id, "name": name, "type": channel_type,
                                                       "is_private": is_private, "user_limit": user_limit})

    # --- chat -------------------------------------------------------------------------------------

    def messages(self, channel_id: int, before: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if before:
            params["before"] = before
        return self.request("GET", f"/channels/{channel_id}/messages", params=params)

    def send_message(self, channel_id: int, body: str) -> dict[str, Any]:
        return self.request("POST", f"/channels/{channel_id}/messages", json={"body": body})

    def edit_message(self, message_id: int, body: str) -> dict[str, Any]:
        return self.request("PATCH", f"/messages/{message_id}", json={"body": body})

    def delete_message(self, message_id: int) -> None:
        self.request("DELETE", f"/messages/{message_id}")

    def mark_read(self, channel_id: int, message_id: int) -> None:
        self.request("POST", f"/channels/{channel_id}/read", json={"message_id": message_id})

    def unread(self, space_id: int) -> list[dict[str, Any]]:
        return self.request("GET", f"/spaces/{space_id}/unread")

    def add_channel_member(self, channel_id: int, user_id: int) -> None:
        self.request("POST", f"/channels/{channel_id}/members", json={"user_id": user_id})

    def connect(self, channel_id: int, muted: bool, deafened: bool) -> dict[str, Any]:
        return self.request("POST", f"/channels/{channel_id}/connect", json={"muted": muted, "deafened": deafened})

    def update_voice(self, channel_id: int, muted: bool, deafened: bool, speaking: bool = False) -> dict[str, Any]:
        return self.request(
            "PUT",
            f"/channels/{channel_id}/voice-state",
            json={"muted": muted, "deafened": deafened, "speaking": speaking, "status": "connected"},
        )

    def disconnect(self, channel_id: int) -> None:
        self.request("POST", f"/channels/{channel_id}/disconnect")

    def voice_states(self, channel_id: int) -> list[dict[str, Any]]:
        return self.request("GET", f"/channels/{channel_id}/voice")
