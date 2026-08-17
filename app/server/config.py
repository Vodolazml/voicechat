from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Private VoiceChat"
    database_url: str = "sqlite:///./voicechat.db"
    secret_key: str = Field(min_length=32)
    access_token_minutes: int = 60
    bootstrap_password: str = Field(min_length=12)
    cors_origins: list[str] = []
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "testserver"]
    max_http_body_bytes: int = 65536
    client_latest_version: str = "0.1.0"
    client_download_url: str = ""
    client_download_sha256: str = ""
    client_update_required: bool = False
    client_release_notes_url: str = ""
    downloads_dir: str = "downloads"

    model_config = SettingsConfigDict(env_prefix="VOICECHAT_", env_file=".env")

    @field_validator("secret_key")
    @classmethod
    def reject_placeholder_secret(cls, value: str) -> str:
        lowered = value.lower()
        if "change-this" in lowered or "dev-only" in lowered or "example" in lowered or "replace" in lowered:
            raise ValueError("VOICECHAT_SECRET_KEY must be a unique random secret, not an example value")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
