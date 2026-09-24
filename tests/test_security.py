"""
test_security.py — Pure unit tests สำหรับ security.py
Layer 1: ไม่ใช้ DB
"""
import pytest

from security import (
    BCRYPT_ROUNDS,
    hash_password,
    verify_password,
    needs_rehash,
    rehash_if_needed,
    is_valid_email,
    is_valid_password,
    is_valid_phone,
    is_supabase_url,
    is_safe_filename,
    detect_file_type,
    validate_file_magic,
    sanitize_text,
    sanitize_html,
)


# ============================================================
# PASSWORD HASHING
# ============================================================

class TestPasswordHashing:

    def test_bcrypt_default_rounds_is_12(self):
        """Verify ว่า default BCRYPT_ROUNDS = 12 (production value)"""
        # หมายเหตุ: conftest monkeypatch BCRYPT_ROUNDS=4
        # → ต้อง reload module เพื่อดูค่าจริง
        import importlib
        import security as sec_mod
        importlib.reload(sec_mod)
        assert sec_mod.BCRYPT_ROUNDS == 12
        # reload กลับ (ให้ monkeypatch ทำงานต่อ)
        importlib.reload(sec_mod)

    def test_hash_password_returns_bcrypt_format(self):
        """Hash ต้องเริ่มด้วย $2b$ (bcrypt format)"""
        h = hash_password("TestPass123")
        assert h.startswith("$2b$")
        assert len(h) == 60

    def test_hash_password_produces_unique_hashes(self):
        """Hash เดียวกัน 2 ครั้ง → ได้ค่าต่างกัน (salt)"""
        h1 = hash_password("TestPass123")
        h2 = hash_password("TestPass123")
        assert h1 != h2

    def test_hash_password_short_raises(self):
        """Password < 8 ตัว → raise ValueError"""
        with pytest.raises(ValueError, match="8 ตัวอักษร"):
            hash_password("short")

    def test_hash_password_empty_raises(self):
        """Password ว่าง → raise"""
        with pytest.raises(ValueError):
            hash_password("")

    def test_hash_password_8_chars_ok(self):
        """Password 8 ตัวพอดี → OK"""
        h = hash_password("Pass1234")
        assert h.startswith("$2b$")

    def test_verify_password_correct(self):
        """Password ถูก → True"""
        h = hash_password("TestPass123")
        assert verify_password("TestPass123", h) is True

    def test_verify_password_wrong(self):
        """Password ผิด → False"""
        h = hash_password("TestPass123")
        assert verify_password("WrongPass456", h) is False

    def test_verify_password_empty_plain(self):
        """plain ว่าง → False"""
        h = hash_password("TestPass123")
        assert verify_password("", h) is False

    def test_verify_password_empty_hash(self):
        """hash ว่าง → False"""
        assert verify_password("TestPass123", "") is False

    def test_verify_password_invalid_hash_format(self):
        """hash format ผิด → False (ไม่ raise)"""
        assert verify_password("TestPass123", "not_a_hash") is False

    def test_needs_rehash_returns_false_for_cost_12(self):
        """hash cost 12 (มาตรฐาน) → ไม่ต้อง rehash"""
        # สร้าง hash ที่ cost=12 (ใช้ bcrypt ตรงๆ)
        import bcrypt
        salt = bcrypt.gensalt(rounds=12)
        h = bcrypt.hashpw(b"TestPass123", salt).decode()
        assert needs_rehash(h) is False

    def test_needs_rehash_returns_true_for_cost_10(self, monkeypatch):
        """hash cost 10 (เก่า) → ต้อง rehash"""
        import security as sec_mod
        # override monkeypatch: ใช้ 12 จริงเพื่อทดสอบ rehash
        monkeypatch.setattr(sec_mod, "BCRYPT_ROUNDS", 12)

        import bcrypt
        salt = bcrypt.gensalt(rounds=10)
        h = bcrypt.hashpw(b"TestPass123", salt).decode()
        assert needs_rehash(h) is True

    def test_needs_rehash_invalid_format(self):
        """hash format ผิด → True (ต้อง rehash)"""
        assert needs_rehash("garbage") is True
        assert needs_rehash("") is True

    def test_rehash_if_needed_returns_new_hash_for_old(self, monkeypatch):
        """hash เก่า → rehash คืน hash ใหม่"""
        import security as sec_mod
        monkeypatch.setattr(sec_mod, "BCRYPT_ROUNDS", 12)

        import bcrypt
        salt = bcrypt.gensalt(rounds=10)
        old_hash = bcrypt.hashpw(b"TestPass123", salt).decode()
        new_hash = rehash_if_needed("TestPass123", old_hash)
        assert new_hash is not None
        assert new_hash != old_hash
        assert verify_password("TestPass123", new_hash) is True

    def test_rehash_if_needed_returns_none_for_current(self):
        """hash ปัจจุบัน → ไม่ rehash คืน None"""
        import bcrypt
        salt = bcrypt.gensalt(rounds=12)
        h = bcrypt.hashpw(b"TestPass123", salt).decode()
        assert rehash_if_needed("TestPass123", h) is None


# ============================================================
# EMAIL VALIDATION
# ============================================================

class TestEmailValidation:

    @pytest.mark.parametrize("email", [
        "user@example.com",
        "test.user@example.com",
        "test+tag@example.co.uk",
        "a@b.co",
        "user123@test-domain.com",
    ])
    def test_valid_emails(self, email):
        assert is_valid_email(email) is True

    @pytest.mark.parametrize("email", [
        "",
        None,
        "not-an-email",
        "@example.com",
        "user@",
        "user@.com",
        "user @example.com",
        "user@example",
    ])
    def test_invalid_emails(self, email):
        assert is_valid_email(email) is False


# ============================================================
# PASSWORD VALIDATION
# ============================================================

class TestPasswordValidation:

    def test_valid_password(self):
        ok, msg = is_valid_password("TestPass123")
        assert ok is True
        assert msg == ""

    @pytest.mark.parametrize("password,expected_msg", [
        ("", "8 ตัวอักษร"),
        (None, "8 ตัวอักษร"),
        ("short1", "8 ตัวอักษร"),
        ("a" * 129, "ยาวเกินไป"),
        ("alllowercase", "ตัวเลข"),  # ไม่มีตัวเลข
        ("12345678", "ตัวอักษร"),    # ไม่มีตัวอักษร
    ])
    def test_invalid_passwords(self, password, expected_msg):
        ok, msg = is_valid_password(password)
        assert ok is False
        assert expected_msg in msg

    def test_password_boundary_128_chars(self):
        """128 chars พอดี → OK"""
        pwd = "A" + "a" * 126 + "1"
        assert len(pwd) == 128
        ok, _ = is_valid_password(pwd)
        assert ok is True

    def test_password_boundary_129_chars(self):
        """129 chars → ไม่ผ่าน"""
        pwd = "A" + "a" * 127 + "1"
        assert len(pwd) == 129
        ok, _ = is_valid_password(pwd)
        assert ok is False


# ============================================================
# PHONE VALIDATION
# ============================================================

class TestPhoneValidation:

    @pytest.mark.parametrize("phone", [
        "+66891234567",      # E.164
        "+14155552671",      # US
        "0891234567",        # TH mobile
        "021234567",         # TH landline
        "+66 89 123 4567",   # spaces
        "089-123-4567",      # dashes
    ])
    def test_valid_phones(self, phone):
        assert is_valid_phone(phone) is True

    @pytest.mark.parametrize("phone", [
        "",
        None,
        "123",
        "abc",
        "+1234567890123456",  # ยาวเกิน E.164
        "08123456789012",     # ยาวเกิน TH
        "phone number",
    ])
    def test_invalid_phones(self, phone):
        assert is_valid_phone(phone) is False


# ============================================================
# SUPABASE URL VALIDATION
# ============================================================

class TestSupabaseUrl:

    @pytest.mark.parametrize("url", [
        "https://xxx.supabase.co/storage/v1/object/public/resumes/abc.pdf",
        "https://wmmgtejbpzznqgswuvac.supabase.co/storage/v1/object/public/avatars/a.png",
    ])
    def test_valid_supabase_urls(self, url):
        assert is_supabase_url(url) is True

    @pytest.mark.parametrize("url", [
        "",
        None,
        "http://xxx.supabase.co/storage/v1/object/public/x",  # http ไม่ใช่ https
        "https://evil.com/storage/v1/object/public/x",       # domain ผิด
        "https://xxx.supabase.co/other/path",                # path ผิด
        "https://xxx.supabase.co.evil.com/storage/v1/object/public/x",  # fake
        "not a url",
    ])
    def test_invalid_urls(self, url):
        assert is_supabase_url(url) is False


# ============================================================
# FILENAME SAFETY
# ============================================================

class TestFilenameSafety:

    @pytest.mark.parametrize("filename", [
        "resume.pdf",
        "my_resume_v2.pdf",
        "Resume-2024.PDF",
        "file with spaces.pdf",
        "文档.pdf",
    ])
    def test_safe_filenames(self, filename):
        assert is_safe_filename(filename) is True

    @pytest.mark.parametrize("filename", [
        "",
        None,
        "../etc/passwd",
        "..\\windows\\system32",
        "file/with/slash.pdf",
        "file\\with\\backslash.pdf",
        "file\x00null.pdf",
        "file%2e%2e.pdf",
        "file%2f.pdf",
        "a" * 256 + ".pdf",  # ยาวเกิน 255
    ])
    def test_unsafe_filenames(self, filename):
        assert is_safe_filename(filename) is False


# ============================================================
# FILE MAGIC BYTES
# ============================================================

class TestFileMagic:

    def test_detect_pdf(self):
        assert detect_file_type(b"%PDF-1.4\n\x00\x00\x00\x00") == "pdf"

    def test_detect_png(self):
        assert detect_file_type(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00") == "png"

    def test_detect_jpg(self):
        # JPEG magic: ff d8 ff — ต้องมี bytes ≥ 8
        assert detect_file_type(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "jpg"

    def test_detect_gif87(self):
        assert detect_file_type(b"GIF87a\x00\x00\x00\x00") == "gif"

    def test_detect_gif89(self):
        assert detect_file_type(b"GIF89a\x00\x00\x00\x00") == "gif"

    def test_detect_webp(self):
        # RIFF + 4 bytes size + WEBP + VP8
        assert detect_file_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"

    def test_detect_unknown(self):
        assert detect_file_type(b"random bytes here") is None

    def test_detect_empty(self):
        assert detect_file_type(b"") is None
        assert detect_file_type(b"abc") is None  # < 8 bytes

    def test_validate_magic_pdf_ok(self):
        assert validate_file_magic(b"%PDF-1.4\n\x00\x00\x00\x00", ["pdf"]) is True

    def test_validate_magic_pdf_wrong_type(self):
        """Extension .pdf แต่เป็น PNG → False"""
        assert validate_file_magic(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00", ["pdf"]) is False

    def test_validate_magic_multiple_allowed(self):
        assert validate_file_magic(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00", ["png", "jpg"]) is True
        assert validate_file_magic(b"\xff\xd8\xff\xe0\x00\x10JFIF", ["png", "jpg"]) is True
        assert validate_file_magic(b"%PDF-1.4\n\x00\x00\x00\x00", ["png", "jpg"]) is False


# ============================================================
# SANITIZATION
# ============================================================

class TestSanitization:

    def test_sanitize_text_removes_html(self):
        assert sanitize_text("<script>alert('xss')</script>Hello") == "alert('xss')Hello"

    def test_sanitize_text_strips_tags(self):
        assert sanitize_text("<b>Bold</b> text") == "Bold text"

    def test_sanitize_text_max_length(self):
        long_text = "a" * 2000
        result = sanitize_text(long_text, max_length=100)
        assert len(result) == 100

    def test_sanitize_text_empty(self):
        assert sanitize_text("") == ""
        assert sanitize_text(None) == ""

    def test_sanitize_text_trims_whitespace(self):
        assert sanitize_text("  hello  ") == "hello"

    def test_sanitize_html_allows_safe_tags(self):
        result = sanitize_html("<b>Bold</b> <i>italic</i>")
        assert "<b>" in result
        assert "<i>" in result

    def test_sanitize_html_removes_script(self):
        result = sanitize_html("<script>alert(1)</script><p>Safe</p>")
        assert "<script>" not in result
        assert "<p>Safe</p>" in result

    def test_sanitize_html_empty(self):
        assert sanitize_html("") == ""
        assert sanitize_html(None) == ""
