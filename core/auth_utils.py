"""
auth_utils.py — JWT helpers

- create_tokens_for_user : create access + refresh + csrf
- generate_csrf_token    : secrets.token_urlsafe
- decode_token_payload   : decode token payload
- get_token_ttl_seconds  : remaining TTL of token
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    decode_token,
)

from core.config import settings


# ============================================================
# TOKEN CREATION
# ============================================================

def create_tokens_for_user(user_id: int, role: str) -> dict:
    """
    Create all tokens needed after login.

    Returns:
        {
            "access_token": "...",
            "refresh_token": "...",
            "csrf_token": "...",
            "expires_in": 900,  # seconds
        }
    """
    # ---------- Access Token ----------
    access_jti = str(uuid.uuid4())
    access_expires = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRES_MINUTES)

    access_token = create_access_token(
        identity=str(user_id),
        additional_claims={
            "role": role,
            "jti": access_jti,
        },
        expires_delta=access_expires,
        fresh=True,
    )

    # ---------- Refresh Token ----------
    refresh_jti = str(uuid.uuid4())
    refresh_expires = timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRES_DAYS)

    refresh_token = create_refresh_token(
        identity=str(user_id),
        additional_claims={
            "role": role,
            "jti": refresh_jti,
        },
        expires_delta=refresh_expires,
    )

    # ---------- CSRF Token ----------
    csrf_token = generate_csrf_token()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "csrf_token": csrf_token,
        "expires_in": settings.JWT_ACCESS_TOKEN_EXPIRES_MINUTES * 60,
    }


# ============================================================
# CSRF
# ============================================================

def generate_csrf_token() -> str:
    """Generate a random URL-safe CSRF token."""
    return secrets.token_urlsafe(32)


# ============================================================
# TOKEN DECODE / INSPECT
# ============================================================

def decode_token_payload(token: str) -> dict | None:
    """
    Decode token (verify signature).
    Returns None if invalid.
    """
    try:
        return decode_token(token)
    except Exception:
        return None


def get_token_ttl_seconds(token: str) -> int:
    """
    Compute remaining token TTL in seconds.
    Used when revoking — store jti in blocklist with same TTL.
    """
    payload = decode_token_payload(token)
    if not payload:
        return 0

    exp = payload.get("exp")
    if not exp:
        return 0

    now = datetime.now(timezone.utc).timestamp()
    return max(0, int(exp - now))


def is_token_type_refresh(payload: dict) -> bool:
    """Check if token is a refresh token."""
    return payload.get("type") == "refresh"


# ============================================================
# HELPERS
# ============================================================

def get_user_id_from_payload(payload: dict) -> int | None:
    """Extract user_id from payload (sub)."""
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        return int(sub)
    except (ValueError, TypeError):
        return None


def get_role_from_payload(payload: dict) -> str | None:
    """Extract role from payload."""
    return payload.get("role")


def get_jti_from_payload(payload: dict) -> str | None:
    """Extract jti from payload."""
    return payload.get("jti")