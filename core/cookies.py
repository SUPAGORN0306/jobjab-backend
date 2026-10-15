"""
cookies.py — Cookie helpers for httpOnly auth tokens

Strategy:
- access_token  : httpOnly, 15 minutes
- refresh_token : httpOnly, 7 days, path=/api/auth (restricted)
- csrf_token    : readable by JS, used with X-CSRF-Token header
"""
from flask import Response

from core.config import settings


# ---------- Cookie names ----------
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _base_cookie_kwargs() -> dict:
    """
    Base cookie options:
    - dev  (secure=False) : SameSite=Lax
    - prod (secure=True)  : SameSite=None (cross-site: Vercel → Render)
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
    """Access token cookie — httpOnly, 15-minute lifetime."""
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
    Refresh token cookie — httpOnly, 7-day lifetime.
    Path restricted to /api/auth → only sent for refresh/logout.
    """
    kwargs = _base_cookie_kwargs()
    kwargs["path"] = "/api/auth"

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
    CSRF token cookie — readable by JS.
    Not httpOnly because frontend needs to read and send as header.
    """
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=settings.CSRF_TOKEN_EXPIRES_HOURS * 3600,
        httponly=False,
        secure=settings.cookie_secure,
        domain=settings.COOKIE_DOMAIN or None,
        samesite="None" if settings.cookie_secure else "Lax",
        path="/",
    )
    return response


def clear_all_auth_cookies(response: Response) -> Response:
    """Clear all auth cookies — used on logout."""
    # path="/" — access + csrf
    for name in (ACCESS_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name,
            path="/",
            domain=settings.COOKIE_DOMAIN or None,
        )

    # refresh token has a different path
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
    """Helper: set all 3 cookies at once (on login)."""
    set_access_cookie(response, access_token)
    set_refresh_cookie(response, refresh_token)
    set_csrf_cookie(response, csrf_token)
    return response