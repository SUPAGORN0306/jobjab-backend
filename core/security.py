"""
security.py — Security utilities

- Password hashing (bcrypt cost 12)
- Password rehash (auto when cost changes)
- JWT decorators (@require_auth, @require_role)
- Input sanitization
- Validation helpers (email, phone, url, file)
"""
from functools import wraps
from typing import Callable

import bcrypt
import bleach
from flask import g, jsonify
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

import re
from urllib.parse import urlparse


# ============================================================
# PASSWORD HASHING (bcrypt cost 12)
# ============================================================

BCRYPT_ROUNDS = 12


def hash_password(plain: str) -> str:
    """Hash password with bcrypt cost 12."""
    if not plain or len(plain) < 8:
        raise ValueError("Password must be at least 8 characters")

    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify password against hash."""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def needs_rehash(hashed: str) -> bool:
    """Check if hash needs rehashing (cost too low)."""
    try:
        parts = hashed.split("$")
        if len(parts) < 4:
            return True
        current_rounds = int(parts[2])
        return current_rounds < BCRYPT_ROUNDS
    except (ValueError, IndexError):
        return True


def rehash_if_needed(plain: str, hashed: str) -> str | None:
    """Rehash if needed. Return new hash or None."""
    if needs_rehash(hashed):
        return hash_password(plain)
    return None


# ============================================================
# AUTHORIZATION DECORATORS
# ============================================================

def require_auth(fn: Callable) -> Callable:
    """Decorator: require valid JWT. Sets g.user_id + g.user_role."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        user_id = get_jwt_identity()
        if not user_id:
            return jsonify({
                "error": {"code": "UNAUTHORIZED", "message": "User not found"}
            }), 401

        from flask_jwt_extended import get_jwt
        claims = get_jwt()

        g.user_id = int(user_id)
        g.user_role = claims.get("role", "candidate")
        return fn(*args, **kwargs)
    return wrapper


def require_role(*allowed_roles: str) -> Callable:
    """Decorator: require one of the allowed roles. Use with @require_auth."""
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            role = getattr(g, "user_role", None)
            if not role:
                return jsonify({
                    "error": {"code": "FORBIDDEN", "message": "No role assigned"}
                }), 403
            if role not in allowed_roles:
                return jsonify({
                    "error": {
                        "code": "FORBIDDEN",
                        "message": f"Must be one of: {', '.join(allowed_roles)}",
                    }
                }), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_owner(get_owner_id: Callable) -> Callable:
    """Decorator: require ownership. Protects against IDOR."""
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            owner_id = get_owner_id(*args, **kwargs)
            if owner_id is None:
                return jsonify({
                    "error": {"code": "NOT_FOUND", "message": "Not found"}
                }), 404
            if owner_id != g.user_id:
                return jsonify({
                    "error": {"code": "FORBIDDEN", "message": "Forbidden"}
                }), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ============================================================
# INPUT SANITIZATION
# ============================================================

ALLOWED_TAGS = ["b", "i", "u", "em", "strong", "p", "br", "ul", "ol", "li"]
ALLOWED_ATTRS: dict = {}


def sanitize_html(text: str) -> str:
    """Strip dangerous HTML (XSS protection)."""
    if not text:
        return ""
    return bleach.clean(
        text,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        strip=True,
    )


def sanitize_text(text: str, max_length: int = 1000) -> str:
    """Strip all HTML + limit length."""
    if not text:
        return ""
    cleaned = bleach.clean(text, tags=[], strip=True).strip()
    return cleaned[:max_length]


# ============================================================
# VALIDATION HELPERS
# ============================================================

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def is_valid_email(email: str) -> bool:
    """Validate email format."""
    return bool(email and EMAIL_RE.match(email))


def is_valid_password(password: str) -> tuple[bool, str]:
    """Validate password strength. Returns (ok, error_message)."""
    if not password or len(password) < 8:
        return False, "Password must be at least 8 characters"
    if len(password) > 128:
        return False, "Password is too long (max 128)"
    if not re.search(r"[A-Za-z]", password):
        return False, "Password must contain at least one letter"
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number"
    return True, ""


# ---------- Phone ----------
PHONE_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")
PHONE_TH_RE = re.compile(r"^0\d{8,9}$")


def is_valid_phone(phone: str) -> bool:
    """
    Validate phone format.
    - E.164: +66891234567
    - Thai: 0891234567
    """
    if not phone:
        return False
    phone = phone.strip().replace(" ", "").replace("-", "")
    return bool(PHONE_E164_RE.match(phone) or PHONE_TH_RE.match(phone))


# ---------- URL (Supabase only) ----------
def is_supabase_url(url: str) -> bool:
    """Validate URL is a real Supabase Storage URL (prevents SSRF)."""
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
    """Validate filename (prevents path traversal)."""
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
    Detect file type from magic bytes (do not trust extension).
    Returns: 'pdf', 'png', 'jpg', 'gif', 'webp' or None
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
    """Validate file type from magic bytes is in allowed_types."""
    detected = detect_file_type(file_bytes)
    if not detected:
        return False
    return detected in allowed_types