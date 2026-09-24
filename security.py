"""
security.py — Security utilities

- Password hashing (bcrypt cost 12)
- Password rehash (อัตโนมัติเมื่อ cost เปลี่ยน)
- JWT decorators (@require_auth, @require_role)
- CSRF helpers
- Input sanitization
"""
from functools import wraps
from typing import Callable

import bcrypt
import bleach
from flask import g, jsonify
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

# ============================================================
# PASSWORD HASHING (bcrypt cost 12)
# ============================================================

BCRYPT_ROUNDS = 12


def hash_password(plain: str) -> str:
    """Hash password ด้วย bcrypt cost 12"""
    if not plain or len(plain) < 8:
        raise ValueError("Password ต้องมีอย่างน้อย 8 ตัวอักษร")

    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """ตรวจ password กับ hash"""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def needs_rehash(hashed: str) -> bool:
    """ตรวจว่า hash เก่าเกินไปไหม → ต้อง rehash"""
    try:
        # bcrypt hash format: $2b$12$...
        parts = hashed.split("$")
        if len(parts) < 4:
            return True
        current_rounds = int(parts[2])
        return current_rounds < BCRYPT_ROUNDS
    except (ValueError, IndexError):
        return True


def rehash_if_needed(plain: str, hashed: str) -> str | None:
    """
    ถ้า hash เก่า → rehash แล้ว return hash ใหม่
    ถ้าไม่เก่า → return None (ไม่ต้องอัปเดต)
    """
    if needs_rehash(hashed):
        return hash_password(plain)
    return None


# ============================================================
# AUTHORIZATION DECORATORS
# ============================================================

def require_auth(fn: Callable) -> Callable:
    """
    Decorator: ต้อง login (มี valid JWT)
    เก็บ user_id ใน g.user_id
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        user_id = get_jwt_identity()
        if not user_id:
            return jsonify({
                "error": {"code": "UNAUTHORIZED", "message": "ไม่พบข้อมูลผู้ใช้"}
            }), 401
        g.user_id = int(user_id)
        return fn(*args, **kwargs)
    return wrapper


def require_role(*allowed_roles: str) -> Callable:
    """
    Decorator: ต้อง login + มี role ที่อนุญาต
    ใช้คู่กับ @require_auth

    ตัวอย่าง:
        @require_auth
        @require_role("employer", "admin")
        def create_job(): ...
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            role = getattr(g, "user_role", None)
            if not role:
                return jsonify({
                    "error": {"code": "FORBIDDEN", "message": "ไม่พบสิทธิ์"}
                }), 403
            if role not in allowed_roles:
                return jsonify({
                    "error": {
                        "code": "FORBIDDEN",
                        "message": f"ต้องเป็น {' หรือ '.join(allowed_roles)}",
                    }
                }), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_owner(get_owner_id: Callable) -> Callable:
    """
    Decorator: ตรวจว่า user เป็นเจ้าของ resource
    ป้องกัน IDOR (Insecure Direct Object Reference)

    ตัวอย่าง:
        @require_auth
        @require_owner(lambda app_id: Application.query.get(app_id).user_id)
        def get_application(app_id): ...
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            owner_id = get_owner_id(*args, **kwargs)
            if owner_id is None:
                return jsonify({
                    "error": {"code": "NOT_FOUND", "message": "ไม่พบข้อมูล"}
                }), 404
            if owner_id != g.user_id:
                return jsonify({
                    "error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์เข้าถึง"}
                }), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ============================================================
# INPUT SANITIZATION
# ============================================================

# แท็ก HTML ที่อนุญาต (ถ้าต้องการให้ user กรอก rich text)
ALLOWED_TAGS = ["b", "i", "u", "em", "strong", "p", "br", "ul", "ol", "li"]
ALLOWED_ATTRS: dict = {}


def sanitize_html(text: str) -> str:
    """ลบ HTML ที่อันตราย (XSS)"""
    if not text:
        return ""
    return bleach.clean(
        text,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        strip=True,
    )


def sanitize_text(text: str, max_length: int = 1000) -> str:
    """ลบ HTML ทั้งหมด + จำกัดความยาว"""
    if not text:
        return ""
    cleaned = bleach.clean(text, tags=[], strip=True).strip()
    return cleaned[:max_length]


# ============================================================
# VALIDATION HELPERS
# ============================================================

import re

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def is_valid_email(email: str) -> bool:
    """ตรวจ email format"""
    return bool(email and EMAIL_RE.match(email))


def is_valid_password(password: str) -> tuple[bool, str]:
    """
    ตรวจ password strength
    คืน (ผ่าน, ข้อความ error)
    """
    if not password or len(password) < 8:
        return False, "Password ต้องมีอย่างน้อย 8 ตัวอักษร"
    if len(password) > 128:
        return False, "Password ยาวเกินไป (สูงสุด 128)"
    if not re.search(r"[A-Za-z]", password):
        return False, "Password ต้องมีตัวอักษรอย่างน้อย 1 ตัว"
    if not re.search(r"\d", password):
        return False, "Password ต้องมีตัวเลขอย่างน้อย 1 ตัว"
    return True, ""