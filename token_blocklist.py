"""
token_blocklist.py — Revoked token storage

- Production: Redis (persistent, shared across workers)
- Development: in-memory (resets on restart)
"""
import time
from datetime import datetime, timezone

from config import settings


# ============================================================
# BACKENDS
# ============================================================

class _MemoryBackend:
    """In-memory backend — dev only"""

    def __init__(self) -> None:
        # เก็บ (value, expires_at)
        self._store: dict[str, tuple[str, float]] = {}

    def setex(self, key: str, ttl: int, value: str = "1") -> None:
        self._store[key] = (value, time.time() + ttl)

    def get(self, key: str) -> str | None:
        item = self._store.get(key)
        if item is None:
            return None
        value, exp = item
        if exp < time.time():
            self._store.pop(key, None)
            return None
        return value

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if self._store.pop(k, None) is not None:
                n += 1
        return n

    def ping(self) -> bool:
        return True


def _create_backend():
    if settings.REDIS_URL == "memory://":
        return _MemoryBackend()

    import redis
    return redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


_backend = _create_backend()


# ============================================================
# KEY PREFIXES
# ============================================================

_JTI_PREFIX = "jobjab:blocklist:jti:"
_USER_REVOKE_PREFIX = "jobjab:blocklist:user:"


# ============================================================
# PUBLIC API — TOKEN REVOCATION
# ============================================================

def revoke_token(jti: str, ttl_seconds: int) -> None:
    """เพิ่ม jti เข้า blocklist (TTL = เวลาที่เหลือของ token)"""
    if not jti or ttl_seconds <= 0:
        return
    _backend.setex(f"{_JTI_PREFIX}{jti}", ttl_seconds, "1")


def is_token_revoked(jti: str) -> bool:
    """ตรวจว่า jti ถูก revoke ไหม"""
    if not jti:
        return False
    return _backend.exists(f"{_JTI_PREFIX}{jti}")


# ============================================================
# PUBLIC API — USER-LEVEL REVOCATION
# ============================================================

def revoke_all_user_tokens(user_id: int, ttl_seconds: int | None = None) -> None:
    """Revoke ทุก token ของ user ที่ออกก่อนตอนนี้ (ใช้ตอน change password)"""
    if ttl_seconds is None:
        ttl_seconds = settings.JWT_REFRESH_TOKEN_EXPIRES_DAYS * 24 * 3600

    now = int(datetime.now(timezone.utc).timestamp())
    _backend.setex(f"{_USER_REVOKE_PREFIX}{user_id}", ttl_seconds, str(now))


def is_user_token_revoked(user_id: int, token_iat: int) -> bool:
    """ตรวจว่า token ที่ออกตอน iat ถูก revoke ไหม"""
    val = _backend.get(f"{_USER_REVOKE_PREFIX}{user_id}")
    if not val:
        return False
    try:
        return token_iat < int(val)
    except (ValueError, TypeError):
        return False


# ============================================================
# HEALTH
# ============================================================

def is_healthy() -> bool:
    """ตรวจ backend ทำงาน"""
    try:
        return _backend.ping()
    except Exception:
        return False
