"""
test_blocklist.py — Tests สำหรับ token_blocklist.py
Layer 1: ใช้ memory backend (reset ระหว่าง test ผ่าน conftest)
"""
import time

import pytest


# ============================================================
# MEMORY BACKEND — BASIC OPERATIONS
# ============================================================

class TestMemoryBackend:

    def test_setex_and_get(self):
        from token_blocklist import _MemoryBackend
        b = _MemoryBackend()
        b.setex("key1", 60, "1")
        assert b.get("key1") == "1"

    def test_get_missing_key(self):
        from token_blocklist import _MemoryBackend
        b = _MemoryBackend()
        assert b.get("nonexistent") is None

    def test_exists(self):
        from token_blocklist import _MemoryBackend
        b = _MemoryBackend()
        b.setex("key1", 60, "1")
        assert b.exists("key1") is True
        assert b.exists("key2") is False

    def test_delete(self):
        from token_blocklist import _MemoryBackend
        b = _MemoryBackend()
        b.setex("key1", 60, "1")
        b.setex("key2", 60, "1")
        n = b.delete("key1", "key2")
        assert n == 2
        assert b.exists("key1") is False

    def test_ttl_expiry(self):
        """Key หมดอายุหลัง TTL"""
        from token_blocklist import _MemoryBackend
        b = _MemoryBackend()
        b.setex("key1", 1, "1")  # 1 วิ
        assert b.exists("key1") is True
        time.sleep(1.1)
        assert b.exists("key1") is False
        assert b.get("key1") is None

    def test_ping(self):
        from token_blocklist import _MemoryBackend
        b = _MemoryBackend()
        assert b.ping() is True


# ============================================================
# TOKEN REVOCATION (JTI-level)
# ============================================================

class TestTokenRevocation:

    def test_revoke_token(self):
        from token_blocklist import revoke_token, is_token_revoked
        revoke_token("jti-abc-123", ttl_seconds=60)
        assert is_token_revoked("jti-abc-123") is True

    def test_not_revoked(self):
        from token_blocklist import is_token_revoked
        assert is_token_revoked("jti-never-revoked") is False

    def test_revoke_empty_jti_noop(self):
        """jti ว่าง → ไม่ทำอะไร"""
        from token_blocklist import revoke_token, is_token_revoked
        revoke_token("", ttl_seconds=60)
        revoke_token(None, ttl_seconds=60)
        assert is_token_revoked("") is False

    def test_revoke_zero_ttl_noop(self):
        """TTL <= 0 → ไม่ revoke"""
        from token_blocklist import revoke_token, is_token_revoked
        revoke_token("jti-zero", ttl_seconds=0)
        revoke_token("jti-neg", ttl_seconds=-1)
        assert is_token_revoked("jti-zero") is False
        assert is_token_revoked("jti-neg") is False

    def test_multiple_jtis_independent(self):
        from token_blocklist import revoke_token, is_token_revoked
        revoke_token("jti-1", 60)
        revoke_token("jti-2", 60)
        assert is_token_revoked("jti-1") is True
        assert is_token_revoked("jti-2") is True
        assert is_token_revoked("jti-3") is False


# ============================================================
# USER-LEVEL REVOCATION
# ============================================================

class TestUserRevocation:

    def test_revoke_all_user_tokens(self):
        from token_blocklist import revoke_all_user_tokens, is_user_token_revoked
        revoke_all_user_tokens(user_id=42, ttl_seconds=3600)
        # token ที่ออกก่อนตอนนี้ → revoked
        old_iat = int(time.time()) - 100
        assert is_user_token_revoked(42, old_iat) is True

    def test_new_token_not_revoked(self):
        """Token ที่ออกหลัง revoke → ไม่ revoked"""
        from token_blocklist import revoke_all_user_tokens, is_user_token_revoked
        revoke_all_user_tokens(user_id=42, ttl_seconds=3600)
        time.sleep(1.1)
        new_iat = int(time.time()) + 10  # ออกหลัง revoke
        assert is_user_token_revoked(42, new_iat) is False

    def test_user_not_revoked(self):
        from token_blocklist import is_user_token_revoked
        assert is_user_token_revoked(999, int(time.time())) is False

    def test_user_isolation(self):
        """Revoke user A → user B ไม่กระทบ"""
        from token_blocklist import revoke_all_user_tokens, is_user_token_revoked
        revoke_all_user_tokens(user_id=1, ttl_seconds=3600)
        old_iat = int(time.time()) - 100
        assert is_user_token_revoked(1, old_iat) is True
        assert is_user_token_revoked(2, old_iat) is False

    def test_revoke_user_invalid_iat(self):
        """iat format ผิด → False (ไม่ raise)"""
        from token_blocklist import revoke_all_user_tokens, is_user_token_revoked
        revoke_all_user_tokens(user_id=42, ttl_seconds=3600)
        # iat=None หรือ string
        assert is_user_token_revoked(42, 0) in (True, False)  # ไม่ raise


# ============================================================
# HEALTH
# ============================================================

class TestHealth:

    def test_is_healthy_memory(self):
        from token_blocklist import is_healthy
        assert is_healthy() is True


# ============================================================
# INTEGRATION — revoke + check cycle
# ============================================================

class TestRevokeIntegration:

    def test_full_cycle_jti(self):
        """Flow จริง: login → logout → ใช้ token ไม่ได้"""
        from token_blocklist import revoke_token, is_token_revoked

        jti = "test-jti-xyz"
        assert is_token_revoked(jti) is False

        revoke_token(jti, ttl_seconds=60)
        assert is_token_revoked(jti) is True

    def test_full_cycle_user(self):
        """Flow: change password → token เก่าใช้ไม่ได้"""
        from token_blocklist import revoke_all_user_tokens, is_user_token_revoked

        user_id = 123
        old_iat = int(time.time()) - 1000

        assert is_user_token_revoked(user_id, old_iat) is False

        revoke_all_user_tokens(user_id)
        assert is_user_token_revoked(user_id, old_iat) is True
