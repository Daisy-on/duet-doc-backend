from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    frontend_origins: str = "http://localhost:5173"
    database_url: SecretStr | None = None
    dev_auth_enabled: bool = False
    dev_user_id: str = "00000000-0000-0000-0000-000000000001"

    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_fast_model: str = "deepseek-v4-flash"
    deepseek_quality_model: str = "deepseek-v4-pro"

    ai_connect_timeout_seconds: float = 10
    ai_read_timeout_seconds: float = 120
    ai_max_messages: int = 50
    ai_max_context_chars: int = 50_000

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
