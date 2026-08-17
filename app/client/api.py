from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import httpx


class ApiError(RuntimeError):
    pass


def normalize_base_url(value: str, default: str = "http://127.0.0.1:8765") -> str:
    raw = value.strip().rstrip("/") or default
    if "://" not in raw:
        raw = f"{'http' if raw.startswith(('127.', 'localhost')) else 'https'}://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ApiError("Адрес сервера должен быть вида https://example.com")
    hostname = parsed.hostname or ""
    is_local = hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme == "http" and not is_local:
        raise ApiError("Для удалённого сервера используйте HTTPS.")
    return raw


@dataclass
class ApiClient:
    base_url: str = "http://127.0.0.1:8765"
    token: str | None = None
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
            raise ApiError("Сервер недоступен. Проверьте, что backend запущен.") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "Ошибка запроса")
            except ValueError:
                detail = response.text or "Ошибка запроса"
            raise ApiError(str(detail))
        if not response.content:
            return None
        return response.json()

    def login(self, username: str, password: str) -> dict[str, Any]:
        data = self.request("POST", "/auth/login", json={"username": username, "password": password})
        self.token = data["access_token"]
        return data

    def ping_ms(self) -> int:
        samples = []
        for _ in range(2):
            started = perf_counter()
            self.request("GET", "/health")
            samples.append(int((perf_counter() - started) * 1000))
        return min(samples)

    def voice_ws_url(self, channel_id: int) -> str:
        scheme = "wss" if self.base_url.startswith("https://") else "ws"
        host = urlparse(self.base_url).netloc
        return f"{scheme}://{host}/voice/ws/{channel_id}"

    def screen_ws_url(self, channel_id: int) -> str:
        scheme = "wss" if self.base_url.startswith("https://") else "ws"
        host = urlparse(self.base_url).netloc
        return f"{scheme}://{host}/screen/ws/{channel_id}"

    def ws_headers(self) -> dict[str, str]:
        return self._headers()

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
                "temporary_password": password,
                "is_admin": is_admin,
            },
        )

    def spaces(self) -> list[dict[str, Any]]:
        return self.request("GET", "/spaces")

    def create_space(self, name: str) -> dict[str, Any]:
        return self.request("POST", "/spaces", json={"name": name})

    def channels(self, space_id: int) -> list[dict[str, Any]]:
        return self.request("GET", f"/spaces/{space_id}/channels")

    def create_channel(self, space_id: int, name: str, channel_type: str) -> dict[str, Any]:
        return self.request("POST", "/channels", json={"space_id": space_id, "name": name, "type": channel_type})

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
