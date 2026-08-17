from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Private VoiceChat"
    database_url: str = "sqlite:///./voicechat.db"
    secret_key: str = "dev-only-change-me"
    access_token_minutes: int = 60
    bootstrap_password: str = "Admin12345!Local"
    cors_origins: list[str] = []
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "testserver"]
    max_http_body_bytes: int = 65536

    model_config = SettingsConfigDict(env_prefix="VOICECHAT_", env_file=".env")


@lru_cache
def get_settings() -> Settings:
    return Settings()
