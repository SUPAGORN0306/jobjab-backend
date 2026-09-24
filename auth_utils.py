"""
auth_utils.py — JWT helpers

- create_tokens_for_user : สร้าง access + refresh + csrf
- generate_csrf_token    : secrets.token_urlsafe
- decode_token_payload   : ดึง payload จาก token
- seconds_until_expiry   : TTL ของ token ที่เหลือ
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    decode_token,
)

from config import settings


# ============================================================
# TOKEN CREATION
# ============================================================

def create_tokens_for_user(user_id: int, role: str) -> dict:
    """
    สร้าง tokens ทั้งหมดที่ต้องใช้หลัง login

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
    """สร้าง CSRF token แบบสุ่ม (URL-safe)"""
    return secrets.token_urlsafe(32)


# ============================================================
# TOKEN DECODE / INSPECT
# ============================================================

def decode_token_payload(token: str) -> dict | None:
    """
    Decode token (verify signature ด้วย)
    Returns None ถ้า invalid
    """
    try:
        return decode_token(token)
    except Exception:
        return None


def get_token_ttl_seconds(token: str) -> int:
    """
    คำนวณเวลาที่เหลือของ token (วินาที)
    ใช้ตอน revoke — เก็บ jti ใน blocklist เท่ากับ TTL
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
    """ตรวจว่าเป็น refresh token ไหม"""
    return payload.get("type") == "refresh"


# ============================================================
# HELPERS
# ============================================================

def get_user_id_from_payload(payload: dict) -> int | None:
    """ดึง user_id จาก payload (sub)"""
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        return int(sub)
    except (ValueError, TypeError):
        return None


def get_role_from_payload(payload: dict) -> str | None:
    """ดึง role จาก payload"""
    return payload.get("role")


def get_jti_from_payload(payload: dict) -> str | None:
    """ดึง jti จาก payload"""
    return payload.get("jti")
