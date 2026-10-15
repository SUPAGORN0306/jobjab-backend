"""
extensions.py — Centralized extension instances

Prevents circular imports, single init point, easy to mock in tests.
"""
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache
from flask_wtf.csrf import CSRFProtect

# ---------- Database ----------
db = SQLAlchemy()
cache = Cache()
migrate = Migrate()

# ---------- Auth ----------
jwt = JWTManager()
csrf = CSRFProtect()

# ---------- Rate Limit ----------
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    headers_enabled=True,
)


# ---------- JWT Error Handlers ----------
@jwt.unauthorized_loader
def _missing_token(reason: str):
    from flask import jsonify
    return jsonify({
        "error": {"code": "MISSING_TOKEN", "message": "Please log in"}
    }), 401


@jwt.invalid_token_loader
def _invalid_token(reason: str):
    from flask import jsonify
    return jsonify({
        "error": {"code": "INVALID_TOKEN", "message": "Invalid token"}
    }), 401


@jwt.expired_token_loader
def _expired_token(jwt_header, jwt_payload):
    from flask import jsonify
    return jsonify({
        "error": {"code": "TOKEN_EXPIRED", "message": "Token expired. Please refresh."}
    }), 401


@jwt.revoked_token_loader
def _revoked_token(jwt_header, jwt_payload):
    from flask import jsonify
    return jsonify({
        "error": {"code": "TOKEN_REVOKED", "message": "Token revoked. Please log in again."}
    }), 401


def invalidate_jobs_cache():
    """Clear all jobs cache (called after create/update/delete/apply)."""
    try:
        cache.clear()
    except Exception:
        pass