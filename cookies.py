"""
cookies.py — Cookie helpers สำหรับ httpOnly auth tokens

Strategy:
- access_token  : httpOnly, 15 นาที
- refresh_token : httpOnly, 7 วัน, path=/api/auth (จำกัด)
- csrf_token    : readable (JS อ่านได้), ใช้คู่กับ header X-CSRF-Token
"""
from flask import Response

from config import settings


# ---------- Cookie names ----------
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _base_cookie_kwargs() -> dict:
    """
    Cookie options พื้นฐาน
    - dev  (secure=False) : SameSite=Lax
    - prod (secure=True)  : SameSite=None (cross-site Vercel → Render)
    """
    return {
        "secure": settings.cookie_secure,
        "domain": settings.COOKIE_DOMAIN or None,
        "samesite": "None" if settings.cookie_secure else "Lax",
        "path": "/",
    }


# ============================================================
# PUBLIC API
# ============================================================

def set_access_cookie(response: Response, token: str) -> Response:
    """Access token cookie — httpOnly, อายุ 15 นาที"""
    response.set_cookie(
        ACCESS_COOKIE,
        token,
        max_age=settings.JWT_ACCESS_TOKEN_EXPIRES_MINUTES * 60,
        httponly=True,
        **_base_cookie_kwargs(),
    )
    return response


def set_refresh_cookie(response: Response, token: str) -> Response:
    """
    Refresh token cookie — httpOnly, อายุ 7 วัน
    Path จำกัดที่ /api/auth → cookie ส่งแค่ตอน refresh/logout
    """
    kwargs = _base_cookie_kwargs()
    kwargs["path"] = "/api/auth"  # ← จำกัด path

    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRES_DAYS * 24 * 3600,
        httponly=True,
        **kwargs,
    )
    return response


def set_csrf_cookie(response: Response, token: str) -> Response:
    """
    CSRF token cookie — readable (JS อ่านได้)
    ไม่ httpOnly เพราะ frontend ต้องอ่านไปใส่ header
    """
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=settings.CSRF_TOKEN_EXPIRES_HOURS * 3600,
        httponly=False,  # ← JS ต้องอ่านได้
        secure=settings.cookie_secure,
        domain=settings.COOKIE_DOMAIN or None,
        samesite="None" if settings.cookie_secure else "Lax",
        path="/",
    )
    return response


def clear_all_auth_cookies(response: Response) -> Response:
    """ลบ cookies ทั้งหมด — ใช้ตอน logout"""
    # Cookie path="/" — access + csrf
    for name in (ACCESS_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name,
            path="/",
            domain=settings.COOKIE_DOMAIN or None,
        )

    # refresh token มี path ต่าง
    response.delete_cookie(
        REFRESH_COOKIE,
        path="/api/auth",
        domain=settings.COOKIE_DOMAIN or None,
    )

    return response


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
) -> Response:
    """Helper: set ทั้ง 3 cookies ทีเดียว (ตอน login)"""
    set_access_cookie(response, access_token)
    set_refresh_cookie(response, refresh_token)
    set_csrf_cookie(response, csrf_token)
    return response