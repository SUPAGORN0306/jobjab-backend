"""
services/users.py — User helpers

Functions:
- normalize_phone: normalize phone to +<digits>
- generate_username_from_email: generate unique username from email
"""
import re

from sqlalchemy import text


def normalize_phone(phone):
    """
    Normalize phone to +<digits>. Returns None if invalid.
    Sanity check: 8-15 digits (E.164 standard).
    """
    if not phone:
        return None
    digits = re.sub(r'\D', '', str(phone))
    if not digits:
        return None
    if not (8 <= len(digits) <= 15):
        return None
    return f"+{digits}"


def generate_username_from_email(email, db_session):
    """Generate unique username from email."""
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