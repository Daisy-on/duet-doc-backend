from functools import lru_cache

from pydantic import Field, SecretStr
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

    auth_jwt_secret: SecretStr = SecretStr("development-only-change-me-32-bytes")
    auth_jwt_issuer: str = "duet-doc-backend"
    auth_jwt_audience: str = "duet-doc-app"
    auth_access_token_minutes: int = 15
    auth_refresh_token_days: int = 30
    auth_refresh_cookie_name: str = "duet_refresh_token"

    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_fast_model: str = "deepseek-v4-flash"
    deepseek_quality_model: str = "deepseek-v4-pro"

    dashscope_api_key: SecretStr | None = None
    siliconflow_api_key: SecretStr | None = None
    rag_embedding_base_url: str = "https://api.siliconflow.cn/v1/embeddings"
    rag_embedding_model: str = "BAAI/bge-large-zh-v1.5"
    rag_embedding_dimension: int = Field(default=1024, ge=1, le=4096)
    rag_worker_poll_seconds: float = Field(default=2, ge=0.2, le=60)
    rag_worker_max_attempts: int = Field(default=3, ge=1, le=10)

    ai_connect_timeout_seconds: float = 10
    ai_read_timeout_seconds: float = 120
    ai_max_messages: int = 50
    ai_max_context_chars: int = 50_000

    oss_region: str = "cn-chengdu"
    oss_endpoint: str = "https://oss-cn-chengdu.aliyuncs.com"
    oss_bucket: str | None = None
    oss_ecs_role_name: str | None = None
    oss_media_bucket: str | None = None
    media_max_size_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    media_url_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    model_download_url_ttl_seconds: int = Field(default=900, ge=60, le=3_600)
    model_manifest_user_hourly_limit: int = Field(default=3, ge=1, le=100)
    model_manifest_user_daily_limit: int = Field(default=8, ge=1, le=1_000)
    model_manifest_cache_safety_seconds: int = Field(default=30, ge=0, le=300)
    model_manifest_ip_rate_per_minute: int = Field(default=5, ge=1, le=1_000)
    model_manifest_ip_burst: int = Field(default=3, ge=0, le=1_000)
    auth_register_ip_limit_per_hour: int = Field(default=5, ge=1, le=1_000)
    auth_login_ip_limit_per_minute: int = Field(default=10, ge=1, le=1_000)
    trust_proxy_headers: bool = False

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def auth_refresh_cookie_secure(self) -> bool:
        return self.app_env != "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
