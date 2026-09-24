"""
auth.py — Authentication endpoints (raw SQL version)

Routes:
    POST /api/auth/register
    POST /api/auth/login
    POST /api/auth/refresh
    POST /api/auth/logout
    GET  /api/auth/me
"""
import re
import uuid
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from auth_utils import create_tokens_for_user
from cookies import clear_all_auth_cookies, set_auth_cookies
from extensions import db, limiter
from logging_config import get_logger
from security import (
    hash_password,
    is_valid_email,
    is_valid_password,
    rehash_if_needed,
    require_auth,
    sanitize_text,
    verify_password,
)
from token_blocklist import (
    is_token_revoked,
    is_user_token_revoked,
    revoke_token,
)

logger = get_logger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

VALID_ROLES = ("candidate", "employer")


# ============================================================
# HELPERS
# ============================================================

def _error(code: str, message: str, status: int = 400):
    return jsonify({"error": {"code": code, "message": message}}), status


def _user_dict(row) -> dict:
    """แปลง row จาก DB → dict"""
    if not row:
        return {}
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


def _get_user_roles(user_id: int) -> list[str]:
    """ดึง roles ทั้งหมดของ user"""
    rows = db.session.execute(
        text("SELECT role FROM user_roles WHERE user_id = :uid ORDER BY role"),
        {"uid": user_id},
    ).fetchall()
    return [r[0] for r in rows]


def _generate_username_from_email(email: str) -> str:
    """สร้าง username ไม่ซ้ำจาก email"""
    base = email.split("@")[0].lower()
    base = re.sub(r"[^a-z0-9_]", "_", base)
    username = base
    counter = 1
    while True:
        exists = db.session.execute(
            text("SELECT id FROM users WHERE username = :u"),
            {"u": username},
        ).first()
        if not exists:
            return username
        counter += 1
        username = f"{base}_{counter}"


# ============================================================
# REGISTER
# ============================================================

@auth_bp.route("/register", methods=["POST"])
@limiter.limit("3 per hour")
def register():
    """สมัครสมาชิกใหม่"""
    data = request.get_json(silent=True) or {}

    email = sanitize_text(data.get("email", ""), max_length=255).lower()
    password = data.get("password", "")
    full_name = sanitize_text(data.get("full_name", ""), max_length=100)
    role = (data.get("role") or "candidate").lower()
    company_name = sanitize_text(data.get("company_name", ""), max_length=200)
    industry = sanitize_text(data.get("industry", ""), max_length=100)

    # ---------- Validate ----------
    if not email or not password:
        return _error("MISSING_FIELDS", "Email และ password จำเป็น")

    if not is_valid_email(email):
        return _error("INVALID_EMAIL", "Email ไม่ถูกต้อง")

    ok, msg = is_valid_password(password)
    if not ok:
        return _error("WEAK_PASSWORD", msg)

    if role not in VALID_ROLES:
        return _error("INVALID_ROLE", f"Role ต้องเป็น {VALID_ROLES}")

    if role == "employer" and not company_name:
        return _error("MISSING_COMPANY", "Employer ต้องระบุชื่อบริษัท")

    # ---------- ตรวจซ้ำ ----------
    existing = db.session.execute(
        text("SELECT id FROM users WHERE email = :e"),
        {"e": email},
    ).first()

    if existing:
        return _error("EMAIL_EXISTS", "Email นี้ถูกใช้แล้ว", 409)

    # ---------- สร้าง user ----------
    username = _generate_username_from_email(email)
    password_hash = hash_password(password)

    try:
        result = db.session.execute(
            text("""
                INSERT INTO users
                    (username, email, password_hash, full_name, role,
                     created_at, updated_at)
                VALUES
                    (:username, :email, :password_hash, :full_name, :role,
                     NOW(), NOW())
                RETURNING id, username, email, full_name, role, created_at
            """),
            {
                "username": username,
                "email": email,
                "password_hash": password_hash,
                "full_name": full_name or None,
                "role": role,
            },
        )
        user_row = result.mappings().first()
        user_id = user_row["id"]

        # เพิ่ม role ใน user_roles
        db.session.execute(
            text("""
                INSERT INTO user_roles (user_id, role, created_at)
                VALUES (:uid, :r, NOW())
            """),
            {"uid": user_id, "r": role},
        )

        # ถ้า employer → สร้าง employer_profiles
        if role == "employer":
            db.session.execute(
                text("""
                    INSERT INTO employer_profiles
                        (user_id, company_name, industry, created_at, updated_at)
                    VALUES
                        (:uid, :cn, :ind, NOW(), NOW())
                """),
                {
                    "uid": user_id,
                    "cn": company_name,
                    "ind": industry or None,
                },
            )

        db.session.commit()

    except IntegrityError as e:
        db.session.rollback()
        logger.error("register_integrity", error=str(e))
        return _error("REGISTER_FAILED", "สมัครไม่สำเร็จ (ข้อมูลซ้ำ)", 409)
    except Exception as e:
        db.session.rollback()
        logger.error("register_failed", error=str(e), exc_info=True)
        return _error("REGISTER_FAILED", "สมัครไม่สำเร็จ", 500)

    logger.info("user_registered", user_id=user_id, email=email, role=role)

    return jsonify({
        "status": "success",
        "message": "สมัครสมาชิกสำเร็จ",
        "user": _user_dict(user_row),
    }), 201


# ============================================================
# LOGIN
# ============================================================

@auth_bp.route("/login", methods=["POST"])
@limiter.limit("5 per minute")
def login():
    """เข้าสู่ระบบ + set cookies"""
    data = request.get_json(silent=True) or {}
    email = sanitize_text(data.get("email", ""), max_length=255).lower()
    password = data.get("password", "")
    requested_role = (data.get("role") or "").lower()

    if not email or not password:
        return _error("MISSING_CREDENTIALS", "กรุณากรอก email และ password")

    # ---------- หา user ----------
    row = db.session.execute(
        text("""
            SELECT id, username, email, password_hash, full_name,
                   role, profile_image, resume_url, resume_filename
            FROM users WHERE email = :e
        """),
        {"e": email},
    ).mappings().first()

    if not row or not verify_password(password, row["password_hash"]):
        logger.warning("login_failed", email=email)
        return _error("INVALID_CREDENTIALS", "Email หรือ password ไม่ถูกต้อง", 401)

    user_id = row["id"]

    # ---------- Rehash ถ้าจำเป็น ----------
    new_hash = rehash_if_needed(password, row["password_hash"])
    if new_hash:
        try:
            db.session.execute(
                text("UPDATE users SET password_hash = :h WHERE id = :uid"),
                {"h": new_hash, "uid": user_id},
            )
            db.session.commit()
            logger.info("password_rehashed", user_id=user_id)
        except Exception as e:
            db.session.rollback()
            logger.warning("rehash_failed", user_id=user_id, error=str(e))

    # ---------- Roles ----------
    roles = _get_user_roles(user_id) or [row["role"] or "candidate"]

    if requested_role and requested_role in roles:
        selected_role = requested_role
    else:
        selected_role = roles[0]

    # ---------- สร้าง tokens ----------
    tokens = create_tokens_for_user(user_id, selected_role)

    # ---------- Response ----------
    response_user = {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "full_name": row["full_name"],
        "role": selected_role,
        "roles": roles,
        "profile_image": row.get("profile_image"),
        "resume_url": row.get("resume_url"),
        "resume_filename": row.get("resume_filename"),
    }

    # ถ้า employer → เพิ่มข้อมูลบริษัท
    if "employer" in roles:
        emp = db.session.execute(
            text("""
                SELECT company_name, industry, company_logo
                FROM employer_profiles WHERE user_id = :uid
            """),
            {"uid": user_id},
        ).mappings().first()
        if emp:
            response_user["company_name"] = emp["company_name"]
            response_user["industry"] = emp["industry"]
            response_user["company_logo"] = emp["company_logo"]

    response = jsonify({
        "status": "success",
        "message": "เข้าสู่ระบบสำเร็จ",
        "user": response_user,
        "csrf_token": tokens["csrf_token"],
        "expires_in": tokens["expires_in"],
    })

    set_auth_cookies(
        response,
        tokens["access_token"],
        tokens["refresh_token"],
        tokens["csrf_token"],
    )

    logger.info("login_success", user_id=user_id, role=selected_role)

    return response, 200


# ============================================================
# REFRESH
# ============================================================

@auth_bp.route("/refresh", methods=["POST"])
@limiter.limit("30 per hour")
@jwt_required(refresh=True)
def refresh():
    """Rotating refresh token"""
    claims = get_jwt()
    user_id = int(claims["sub"])
    jti = claims.get("jti")
    iat = claims.get("iat", 0)

    # ---------- Blocklist check ----------
    if jti and is_token_revoked(jti):
        return _error("TOKEN_REVOKED", "Token ถูกยกเลิก", 401)

    if is_user_token_revoked(user_id, iat):
        return _error("TOKEN_REVOKED", "Token ถูกยกเลิกทั้งหมด", 401)

    # ---------- Revoke ตัวเก่า (rotating) ----------
    if jti:
        ttl = 60 * 60 * 24 * 7  # 7 วัน
        revoke_token(jti, ttl)

    # ---------- ดึง user ----------
    row = db.session.execute(
        text("""
            SELECT id, username, email, full_name, role
            FROM users WHERE id = :uid
        """),
        {"uid": user_id},
    ).mappings().first()

    if not row:
        return _error("USER_NOT_FOUND", "ไม่พบผู้ใช้", 404)

    # ---------- ออก tokens ใหม่ ----------
    roles = _get_user_roles(user_id) or [row["role"] or "candidate"]
    selected_role = claims.get("role") or roles[0]

    tokens = create_tokens_for_user(user_id, selected_role)

    response = jsonify({
        "status": "success",
        "csrf_token": tokens["csrf_token"],
        "expires_in": tokens["expires_in"],
    })
    set_auth_cookies(
        response,
        tokens["access_token"],
        tokens["refresh_token"],
        tokens["csrf_token"],
    )

    logger.info("token_refreshed", user_id=user_id)
    return response, 200


# ============================================================
# LOGOUT
# ============================================================

@auth_bp.route("/logout", methods=["POST"])
@jwt_required(refresh=True)
def logout():
    """ออกจากระบบ + revoke"""
    claims = get_jwt()
    jti = claims.get("jti")

    if jti:
        revoke_token(jti, 60 * 60 * 24 * 7)
        logger.info("user_logged_out", jti=jti[:8] + "...")

    response = jsonify({
        "status": "success",
        "message": "ออกจากระบบสำเร็จ",
    })
    clear_all_auth_cookies(response)
    return response, 200


# ============================================================
# ME
# ============================================================

@auth_bp.route("/me", methods=["GET"])
@require_auth
def me():
    """ข้อมูลผู้ใช้ปัจจุบัน"""
    row = db.session.execute(
        text("""
            SELECT id, username, email, full_name, phone, location, bio,
                   role, profile_image, industry, resume_url, resume_filename,
                   created_at, updated_at
            FROM users WHERE id = :uid
        """),
        {"uid": g.user_id},
    ).mappings().first()

    if not row:
        return _error("USER_NOT_FOUND", "ไม่พบผู้ใช้", 404)

    user = _user_dict(row)
    user["roles"] = _get_user_roles(g.user_id)

    # ถ้า employer → เพิ่มข้อมูลบริษัท
    if "employer" in user["roles"]:
        emp = db.session.execute(
            text("""
                SELECT company_name, industry, company_logo
                FROM employer_profiles WHERE user_id = :uid
            """),
            {"uid": g.user_id},
        ).mappings().first()
        if emp:
            user["company_name"] = emp["company_name"]
            user["industry"] = emp["industry"]
            user["company_logo"] = emp["company_logo"]

    return jsonify({"user": user}), 200
