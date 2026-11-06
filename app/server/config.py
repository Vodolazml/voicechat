from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Values that were ever published in this repository's docs, examples or tests.
# A server must never start with one of them in production.
KNOWN_PUBLIC_SECRETS = frozenset(
    {
        "Admin12345!Local",
        "Admin12345!",
        "test-private-voicechat-secret-key-32",
        "abc123def456789012345678901234567890abcdef1234567890abcdef12",
    }
)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class Settings(BaseSettings):
    app_name: str = "Private VoiceChat"
    # "production" enforces TLS, unique secrets and closed API docs; "development" relaxes this for local runs/tests.
    environment: str = Field(default="production", pattern=r"^(production|development)$")
    database_url: str = "sqlite:///./voicechat.db"
    secret_key: str = Field(min_length=32)
    access_token_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_days: int = Field(default=30, ge=1, le=90)
    refresh_reuse_grace_seconds: int = Field(default=60, ge=0, le=600)
    # Empty means: generate a random owner password on first start and print it once to the server log.
    bootstrap_password: str = ""
    # Anyone can create an account; joining a space still needs that space's invite code.
    open_registration: bool = True
    max_spaces_per_user: int = Field(default=10, ge=0, le=100)
    invite_hours: int = Field(default=24, ge=1, le=24 * 14)
    enable_api_docs: bool = False
    cors_origins: list[str] = []
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "testserver"]
    max_http_body_bytes: int = 65536
    client_latest_version: str = "0.2.0"
    client_download_url: str = ""
    client_download_sha256: str = ""
    client_download_signature: str = ""
    client_update_required: bool = False
    client_release_notes_url: str = ""
    downloads_dir: str = "downloads"
    livekit_url: str = ""
    livekit_internal_url: str = "http://livekit:7880"
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    voice_state_timeout_seconds: int = Field(default=45, ge=10, le=600)

    model_config = SettingsConfigDict(env_prefix="VOICECHAT_", env_file=".env")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @field_validator("secret_key")
    @classmethod
    def reject_placeholder_secret(cls, value: str) -> str:
        lowered = value.lower()
        if any(marker in lowered for marker in ("change-this", "dev-only", "example", "replace", "changeme")):
            raise ValueError("VOICECHAT_SECRET_KEY must be a unique random secret, not an example value")
        if len(set(value)) < 12:
            raise ValueError("VOICECHAT_SECRET_KEY is too predictable; generate it with `openssl rand -base64 48`")
        return value

    @model_validator(mode="after")
    def enforce_production_rules(self) -> "Settings":
        if not self.is_production:
            return self
        if self.secret_key in KNOWN_PUBLIC_SECRETS:
            raise ValueError("VOICECHAT_SECRET_KEY is published in the repository; generate a new one")
        if self.bootstrap_password and self.bootstrap_password in KNOWN_PUBLIC_SECRETS:
            raise ValueError("VOICECHAT_BOOTSTRAP_PASSWORD is published in the repository; leave it empty to generate one")
        if self.livekit_url:
            parsed = urlparse(self.livekit_url)
            if parsed.scheme != "wss" and (parsed.hostname or "") not in LOCAL_HOSTS:
                raise ValueError("VOICECHAT_LIVEKIT_URL must use wss:// in production")
        if self.livekit_api_secret and len(self.livekit_api_secret) < 32:
            raise ValueError("VOICECHAT_LIVEKIT_API_SECRET must be at least 32 characters")
        if self.client_download_url and not self.client_download_url.startswith("https://"):
            raise ValueError("VOICECHAT_CLIENT_DOWNLOAD_URL must use https:// in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
