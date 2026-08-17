from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Private VoiceChat"
    database_url: str = "sqlite:///./voicechat.db"
    secret_key: str = "dev-only-change-me"
    access_token_minutes: int = 60
    bootstrap_password: str = "Admin12345!"
    cors_origins: list[str] = []

    model_config = SettingsConfigDict(env_prefix="VOICECHAT_", env_file=".env")


@lru_cache
def get_settings() -> Settings:
    return Settings()
