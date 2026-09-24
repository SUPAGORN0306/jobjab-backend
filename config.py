"""
config.py — Type-safe configuration (pydantic-settings)

ข้อดี:
- Fail-fast: ถ้า env ขาด/ผิด format → แอปไม่ start (รู้ทันที)
- Type-safe: IDE autocomplete ได้
- Validate: ตรวจ DATABASE_URL, SUPABASE_URL อัตโนมัติ
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
        if not v.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL ต้องขึ้นต้นด้วย postgresql://")
        return v

    @field_validator("SUPABASE_URL")
    @classmethod
    def _validate_supabase(cls, v: str) -> str:
        if "/rest/v1" in v or "/storage/v1" in v:
            raise ValueError(
                "SUPABASE_URL ต้องเป็น base URL เท่านั้น (ไม่มี /rest/v1 หรือ /storage/v1)"
            )
        return v.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    """Cache settings — อ่าน .env ครั้งเดียว"""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()