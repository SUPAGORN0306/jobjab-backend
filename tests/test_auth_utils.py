"""
test_auth_utils.py — Pure unit tests สำหรับ auth_utils.py
Layer 1: ต้องการ Flask app context (สำหรับ JWT)
"""
import time

import pytest


# ============================================================
# CSRF TOKEN
# ============================================================

class TestCsrfToken:

    def test_generate_csrf_token_returns_string(self):
        from auth_utils import generate_csrf_token
        token = generate_csrf_token()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_generate_csrf_token_unique(self):
        from auth_utils import generate_csrf_token
        tokens = {generate_csrf_token() for _ in range(50)}
        assert len(tokens) == 50  # ไม่ซ้ำ

    def test_generate_csrf_token_urlsafe(self):
        """URL-safe: มีแค่ A-Za-z0-9-_ เท่านั้น"""
        from auth_utils import generate_csrf_token
        token = generate_csrf_token()
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert set(token).issubset(allowed)


# ============================================================
# TOKEN CREATION (ต้องมี app context)
# ============================================================

class TestTokenCreation:

    def test_create_tokens_returns_all_keys(self, app):
        from auth_utils import create_tokens_for_user
        with app.app_context():
            result = create_tokens_for_user(user_id=1, role="candidate")

        assert "access_token" in result
        assert "refresh_token" in result
        assert "csrf_token" in result
        assert "expires_in" in result

    def test_access_token_is_string(self, app):
        from auth_utils import create_tokens_for_user
        with app.app_context():
            result = create_tokens_for_user(user_id=1, role="candidate")
        assert isinstance(result["access_token"], str)
        assert result["access_token"].count(".") == 2  # JWT = 3 ส่วน

    def test_refresh_token_is_string(self, app):
        from auth_utils import create_tokens_for_user
        with app.app_context():
            result = create_tokens_for_user(user_id=1, role="candidate")
        assert isinstance(result["refresh_token"], str)
        assert result["refresh_token"].count(".") == 2

    def test_expires_in_is_seconds(self, app):
        """expires_in = JWT_ACCESS_TOKEN_EXPIRES_MINUTES * 60"""
        from auth_utils import create_tokens_for_user
        from config import settings
        with app.app_context():
            result = create_tokens_for_user(user_id=1, role="candidate")
        expected = settings.JWT_ACCESS_TOKEN_EXPIRES_MINUTES * 60
        assert result["expires_in"] == expected

    def test_tokens_unique_per_call(self, app):
        """เรียก 2 ครั้ง → ได้ token ต่างกัน (เพราะ jti ไม่ซ้ำ)"""
        from auth_utils import create_tokens_for_user
        with app.app_context():
            r1 = create_tokens_for_user(user_id=1, role="candidate")
            time.sleep(1.1)  # รอ iat เปลี่ยน
            r2 = create_tokens_for_user(user_id=1, role="candidate")
        assert r1["access_token"] != r2["access_token"]
        assert r1["refresh_token"] != r2["refresh_token"]


# ============================================================
# TOKEN DECODE
# ============================================================

class TestTokenDecode:

    def test_decode_valid_token(self, app):
        from auth_utils import create_tokens_for_user, decode_token_payload
        with app.app_context():
            tokens = create_tokens_for_user(user_id=42, role="employer")
            payload = decode_token_payload(tokens["access_token"])

        assert payload is not None
        assert payload["sub"] == "42"
        assert payload["role"] == "employer"
        assert payload["type"] == "access"

    def test_decode_refresh_token(self, app):
        from auth_utils import create_tokens_for_user, decode_token_payload, is_token_type_refresh
        with app.app_context():
            tokens = create_tokens_for_user(user_id=1, role="candidate")
            payload = decode_token_payload(tokens["refresh_token"])

        assert payload is not None
        assert payload["type"] == "refresh"
        assert is_token_type_refresh(payload) is True

    def test_decode_invalid_token(self):
        from auth_utils import decode_token_payload
        assert decode_token_payload("invalid.token.here") is None

    def test_decode_empty_token(self):
        from auth_utils import decode_token_payload
        assert decode_token_payload("") is None


# ============================================================
# TOKEN INSPECT HELPERS
# ============================================================

class TestTokenHelpers:

    def test_get_ttl_seconds_returns_positive(self, app):
        from auth_utils import create_tokens_for_user, get_token_ttl_seconds
        with app.app_context():
            tokens = create_tokens_for_user(user_id=1, role="candidate")
            ttl = get_token_ttl_seconds(tokens["access_token"])

        # access token อายุ 15 นาที → TTL ควรอยู่ระหว่าง 800-900
        assert 800 < ttl <= 900

    def test_get_ttl_seconds_refresh_token(self, app):
        from auth_utils import create_tokens_for_user, get_token_ttl_seconds
        with app.app_context():
            tokens = create_tokens_for_user(user_id=1, role="candidate")
            ttl = get_token_ttl_seconds(tokens["refresh_token"])

        # refresh token อายุ 7 วัน = 604800 วิ
        assert 604700 < ttl <= 604800

    def test_get_ttl_seconds_invalid_token(self):
        from auth_utils import get_token_ttl_seconds
        assert get_token_ttl_seconds("invalid") == 0

    def test_get_user_id_from_payload(self, app):
        from auth_utils import create_tokens_for_user, decode_token_payload, get_user_id_from_payload
        with app.app_context():
            tokens = create_tokens_for_user(user_id=99, role="candidate")
            payload = decode_token_payload(tokens["access_token"])
        assert get_user_id_from_payload(payload) == 99

    def test_get_user_id_invalid_sub(self):
        from auth_utils import get_user_id_from_payload
        assert get_user_id_from_payload({"sub": "not_a_number"}) is None
        assert get_user_id_from_payload({}) is None

    def test_get_role_from_payload(self):
        from auth_utils import get_role_from_payload
        assert get_role_from_payload({"role": "employer"}) == "employer"
        assert get_role_from_payload({}) is None

    def test_get_jti_from_payload(self):
        from auth_utils import get_jti_from_payload
        assert get_jti_from_payload({"jti": "abc-123"}) == "abc-123"
        assert get_jti_from_payload({}) is None

    def test_is_token_type_refresh_false_for_access(self, app):
        from auth_utils import create_tokens_for_user, decode_token_payload, is_token_type_refresh
        with app.app_context():
            tokens = create_tokens_for_user(user_id=1, role="candidate")
            payload = decode_token_payload(tokens["access_token"])
        assert is_token_type_refresh(payload) is False
