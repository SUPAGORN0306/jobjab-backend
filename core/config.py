"""
config.py — Type-safe configuration (pydantic-settings)

Benefits:
- Fail-fast: if env is missing/malformed → app won't start
- Type-safe: IDE autocomplete works
- Validated: DATABASE_URL, SUPABASE_URL checked automatically
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ---------- Core ----------
    ENV: Literal["development", "staging", "production"] = "development"
    FLASK_APP: str = "app.py"
    FLASK_DEBUG: int = 0
    PORT: int = 5000                     # ← ⭐ เพิ่มบรรทัดนี้

    # ---------- Database ----------
    DATABASE_URL: str

    # ---------- Supabase ----------
    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str

    # ---------- JWT ----------
    JWT_SECRET_KEY: str = Field(min_length=32)
    JWT_ACCESS_TOKEN_EXPIRES_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRES_DAYS: int = 7

    # ---------- CSRF ----------
    CSRF_TOKEN_EXPIRES_HOURS: int = 24

    # ---------- Redis ----------
    REDIS_URL: str = "memory://"

    # ---------- CORS ----------
    CORS_ORIGINS: str = "http://localhost:5173"

    # ---------- Cookies ----------
    COOKIE_DOMAIN: str | None = None
    COOKIE_SECURE: int = 0

    # ---------- Rate Limit ----------
    RATELIMIT_STORAGE_URI: str = ""
    RATELIMIT_ENABLED: int = 1

    # ---------- Logging ----------
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["console", "json"] = "console"

    # ---------- Sentry ----------
    SENTRY_DSN: str = ""

    # ---------- Computed properties ----------
    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

    @property
    def is_development(self) -> bool:
        return self.ENV == "development"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def ratelimit_uri(self) -> str:
        return self.RATELIMIT_STORAGE_URI or self.REDIS_URL

    @property
    def cookie_secure(self) -> bool:
        return bool(self.COOKIE_SECURE)

    # ---------- Validators ----------
    @field_validator("DATABASE_URL")
    @classmethod
    def _validate_db(cls, v: str) -> str:
        if not v.startswith(("postgresql://", "postgres://", "sqlite://")):
            raise ValueError(
                "DATABASE_URL must start with postgresql:// or sqlite:// (test)"
            )
        return v

    @field_validator("SUPABASE_URL")
    @classmethod
    def _validate_supabase(cls, v: str) -> str:
        if "/rest/v1" in v or "/storage/v1" in v:
            raise ValueError(
                "SUPABASE_URL must be a base URL only (no /rest/v1 or /storage/v1)"
            )
        return v.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    """Cache settings — read .env once."""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()