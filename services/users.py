"""
services/users.py — User helpers

Functions:
- normalize_phone: แปลงเบอร์โทรเป็น +<digits>
- generate_username_from_email: สร้าง username จาก email (unique)
"""
import re

from sqlalchemy import text


def normalize_phone(phone):
    """Normalize phone to +<digits> for DB storage."""
    if not phone:
        return None
    digits = re.sub(r'\D', '', str(phone))
    if not digits:
        return None
    return f"+{digits}"


def generate_username_from_email(email, db_session):
    base = email.split("@")[0].lower()
    base = re.sub(r"[^a-z0-9_]", "_", base)
    username = base
    counter = 1
    while True:
        exists = db_session.execute(
            text("SELECT id FROM users WHERE username = :u"),
            {"u": username}
        ).first()
        if not exists:
            return username
        counter += 1
        username = f"{base}_{counter}"
