"""
extensions.py — Centralized extension instances

ทำไมต้องมีไฟล์นี้?
- ป้องกัน circular import (app.py ↔ models.py ↔ routes.py)
- init ใน app.py ที่เดียว
- test ง่าย (mock extensions ได้)
"""
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

# ---------- Database ----------
db = SQLAlchemy()
migrate = Migrate()

# ---------- Auth ----------
jwt = JWTManager()
csrf = CSRFProtect()

# ---------- Rate Limit ----------
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],  # กำหนด limit ที่ endpoint เอง
    headers_enabled=True,  # ส่ง X-RateLimit-* headers
)


# ---------- JWT Error Handlers ----------
@jwt.unauthorized_loader
def _missing_token(reason: str):
    """ไม่ส่ง token มา"""
    from flask import jsonify
    return jsonify({
        "error": {
            "code": "MISSING_TOKEN",
            "message": "กรุณาเข้าสู่ระบบ",
        }
    }), 401


@jwt.invalid_token_loader
def _invalid_token(reason: str):
    """token เสียหาย"""
    from flask import jsonify
    return jsonify({
        "error": {
            "code": "INVALID_TOKEN",
            "message": "Token ไม่ถูกต้อง",
        }
    }), 401


@jwt.expired_token_loader
def _expired_token(jwt_header, jwt_payload):
    """token หมดอายุ"""
    from flask import jsonify
    return jsonify({
        "error": {
            "code": "TOKEN_EXPIRED",
            "message": "Token หมดอายุ กรุณา refresh",
        }
    }), 401


@jwt.revoked_token_loader
def _revoked_token(jwt_header, jwt_payload):
    """token ถูก revoke (logout)"""
    from flask import jsonify
    return jsonify({
        "error": {
            "code": "TOKEN_REVOKED",
            "message": "Token ถูกยกเลิก กรุณาเข้าสู่ระบบใหม่",
        }
    }), 401