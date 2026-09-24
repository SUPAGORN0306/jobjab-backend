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
    เก็บ user_id + role ใน g
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        user_id = get_jwt_identity()
        if not user_id:
            return jsonify({
                "error": {"code": "UNAUTHORIZED", "message": "ไม่พบข้อมูลผู้ใช้"}
            }), 401

        # ดึง role จาก JWT claims
        from flask_jwt_extended import get_jwt
        claims = get_jwt()

        g.user_id = int(user_id)
        g.user_role = claims.get("role", "candidate")
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

# ============================================================
# SPRINT 3: Extended Validation
# ============================================================

import re as _re
from urllib.parse import urlparse


# ---------- Phone ----------
PHONE_E164_RE = _re.compile(r"^\+[1-9]\d{1,14}$")
PHONE_TH_RE = _re.compile(r"^0\d{8,9}$")


def is_valid_phone(phone: str) -> bool:
    """
    ตรวจ phone format
    - E.164: +66891234567
    - ไทย: 0891234567
    """
    if not phone:
        return False
    phone = phone.strip().replace(" ", "").replace("-", "")
    return bool(PHONE_E164_RE.match(phone) or PHONE_TH_RE.match(phone))


# ---------- URL (Supabase only) ----------
def is_supabase_url(url: str) -> bool:
    """
    ตรวจว่า URL เป็น Supabase Storage จริง
    ป้องกัน SSRF + malicious URL
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False
        if not parsed.netloc.endswith(".supabase.co"):
            return False
        if "/storage/v1/object/public/" not in parsed.path:
            return False
        return True
    except Exception:
        return False


# ---------- Filename safety ----------
def is_safe_filename(filename: str) -> bool:
    """
    ตรวจชื่อไฟล์ — ห้าม path traversal
    """
    if not filename or len(filename) > 255:
        return False
    forbidden = ["..", "/", "\\", "\x00", "%2e", "%2f"]
    lower = filename.lower()
    for f in forbidden:
        if f in lower:
            return False
    return True


# ---------- File magic bytes ----------
MAGIC_BYTES = {
    b"%PDF": "pdf",
    b"\x89PNG": "png",
    b"\xff\xd8\xff": "jpg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"RIFF": "webp",
}


def detect_file_type(file_bytes: bytes) -> str | None:
    """
    ตรวจ file type จาก magic bytes (ไม่เชื่อ extension)
    Returns: 'pdf', 'png', 'jpg', 'gif', 'webp' หรือ None
    """
    if not file_bytes or len(file_bytes) < 8:
        return None

    header = file_bytes[:16]

    for magic, file_type in MAGIC_BYTES.items():
        if header.startswith(magic):
            if file_type == "webp":
                if b"WEBP" not in file_bytes[:16]:
                    continue
            return file_type

    return None


def validate_file_magic(file_bytes: bytes, allowed_types: list[str]) -> bool:
    """
    ตรวจ file type จาก magic bytes ว่าอยู่ใน allowed_types
    """
    detected = detect_file_type(file_bytes)
    if not detected:
        return False
    return detected in allowed_types
