"""
conftest.py — Root fixtures for all tests

ลำดับความสำคัญ:
1. โหลด .env.test (override .env)
2. Import config settings (หลัง env ถูก set)
3. Import app + extensions
4. Reset state ระหว่าง test
"""
import os
import sys
from pathlib import Path

# ============================================================
# PATH SETUP — ให้ import module ของ backend ได้
# ============================================================
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# ============================================================
# ENV SETUP — ต้องทำก่อน import config (เพราะ lru_cache)
# ============================================================
from dotenv import load_dotenv

# โหลด .env ก่อน (เพื่อให้ TEST_DATABASE_URL มีค่า)
load_dotenv(BACKEND_DIR / ".env")
# โหลด .env.test ทับ (override ค่าที่ต้องเปลี่ยน)
load_dotenv(BACKEND_DIR / ".env.test", override=True)

# ใช้ TEST_DATABASE_URL แทน DATABASE_URL สำหรับ test
if os.getenv("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

# ============================================================
# IMPORTS (หลัง env setup แล้วเท่านั้น)
# ============================================================
import pytest
from flask import Flask


# ============================================================
# SESSION-SCOPED FIXTURES (สร้างครั้งเดียวต่อ session)
# ============================================================

@pytest.fixture(scope="session")
def app() -> Flask:
    """
    สร้าง Flask app สำหรับ test (session-scoped)
    - ใช้ TEST_DATABASE_URL
    - ปิด rate limiter
    - ปิด CSRF
    """
    # Import แบบ lazy — หลัง env set แล้ว
    from app import app as flask_app

    # Override config สำหรับ test
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["RATELIMIT_ENABLED"] = False
    flask_app.config["JWT_COOKIE_CSRF_PROTECT"] = False
    flask_app.config["SERVER_NAME"] = "localhost"

    yield flask_app


@pytest.fixture(scope="session")
def client(app):
    """Flask test client (session-scoped)"""
    return app.test_client()


# ============================================================
# FUNCTION-SCOPED FIXTURES (รีเซ็ตทุก test)
# ============================================================

@pytest.fixture(autouse=True)
def _disable_limiter(app):
    """ปิด rate limiter ทุก test (autouse)"""
    from extensions import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch):
    """
    ลด BCRYPT_ROUNDS เป็น 4 เพื่อให้ test เร็ว (จาก ~250ms → ~4ms)
    ⚠️ test_security.py::test_bcrypt_default_rounds จะ verify ว่าค่าจริง = 12
    """
    import security
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)  # test only - เร็วขึ้น
    yield


@pytest.fixture(autouse=True)
def _reset_blocklist():
    """Reset token blocklist ระหว่าง test (เพราะเป็น module-level singleton)"""
    import token_blocklist

    # เก็บของเก่า
    old_backend = token_blocklist._backend

    # สร้างใหม่ (memory backend ใหม่)
    token_blocklist._backend = token_blocklist._MemoryBackend()

    yield

    # คืนค่าเดิม (หรือทิ้งก็ได้)
    token_blocklist._backend = old_backend


@pytest.fixture(autouse=True)
def _clean_db(app):
    """
    ลบข้อมูลใน tables ที่ test สร้าง (ระหว่าง test)
    ใช้ TRUNCATE ... RESTART IDENTITY CASCADE
    ⚠️ ระวัง: ลบทุกอย่างใน public schema ยกเว้นข้อมูลที่ต้อง preserve
    """
    from extensions import db
    from sqlalchemy import text

    yield  # ← test รันตรงนี้

    # Cleanup หลัง test — ใช้ DELETE (เร็วกว่า TRUNCATE มาก)
    with app.app_context():
        try:
            for table in [
                "application_educations",
                "application_experiences",
                "application_skills",
                "applications",
                "favorites",
                "employer_profiles",
                "user_education",
                "user_experience",
                "user_skills",
                "user_roles",
                "users",
                "job_market_data",
            ]:
                db.session.execute(text(f"DELETE FROM {table}"))
            db.session.commit()
        except Exception:
            db.session.rollback()


# ============================================================
# HELPER FIXTURES
# ============================================================

@pytest.fixture
def app_ctx(app):
    """App context สำหรับ query DB ตรงๆ"""
    with app.app_context():
        yield


@pytest.fixture
def db_session(app):
    """DB session สำหรับ test ที่ต้อง query เอง"""
    from extensions import db
    with app.app_context():
        yield db.session
        db.session.rollback()


# ============================================================
# CLEAR COOKIES BETWEEN TESTS
# ============================================================

@pytest.fixture(autouse=True)
def _clear_client_cookies(client):
    """ล้าง cookie ของ Flask test_client ระหว่าง test"""
    yield
    try:
        client._cookies.clear()
    except AttributeError:
        try:
            client.cookie_jar.clear()
        except AttributeError:
            pass


# ============================================================
# LAYER 2: FACTORY FIXTURES
# ============================================================

import uuid as _uuid


@pytest.fixture
def make_user(app):
    """สร้าง user ใน DB → return dict {id, email, password, role}"""
    from extensions import db
    from sqlalchemy import text
    from security import hash_password

    def _make(
        email=None,
        password="TestPass123",
        role="candidate",
        full_name="Test User",
        company_name=None,
        industry=None,
    ):
        email = email or f"test-{_uuid.uuid4().hex[:8]}@test.local"
        username = f"user_{_uuid.uuid4().hex[:8]}"
        pw_hash = hash_password(password)

        with app.app_context():
            result = db.session.execute(
                text("""
                    INSERT INTO users
                        (username, email, password_hash, full_name, created_at, updated_at)
                    VALUES
                        (:username, :email, :pw, :fn, NOW(), NOW())
                    RETURNING id
                """),
                {
                    "username": username,
                    "email": email,
                    "pw": pw_hash,
                    "fn": full_name,
                },
            )
            user_id = result.scalar()

            db.session.execute(
                text("""
                    INSERT INTO user_roles (user_id, role, created_at)
                    VALUES (:uid, :role, NOW())
                """),
                {"uid": user_id, "role": role},
            )

            if role == "employer":
                db.session.execute(
                    text("""
                        INSERT INTO employer_profiles
                            (user_id, company_name, industry, created_at, updated_at)
                        VALUES (:uid, :cn, :ind, NOW(), NOW())
                    """),
                    {
                        "uid": user_id,
                        "cn": company_name or f"Test Co {user_id}",
                        "ind": industry or "tech",
                    },
                )

            db.session.commit()

        return {
            "id": user_id,
            "email": email,
            "username": username,
            "password": password,
            "role": role,
        }

    return _make


@pytest.fixture
def login(app, client):
    """Login → return response"""
    def _login(user):
        response = client.post(
            "/api/auth/login",
            json={"email": user["email"], "password": user["password"]},
        )
        assert response.status_code == 200, f"Login failed: {response.get_json()}"
        return response
    return _login


@pytest.fixture
def make_job(app, make_user):
    """สร้าง job ใน DB → return job dict"""
    from extensions import db
    from sqlalchemy import text

    def _make(employer=None, **kwargs):
        if employer is None:
            employer = make_user(role="employer")

        defaults = {
            "job_title": "Data Scientist",
            "company_name": "Test Co",
            "location": "Bangkok",
            "employment_type": "Full-time",
            "experience_level": "Mid",
            "salary_min": 50000,
            "salary_max": 100000,
            "skills_required": "Python, SQL",
            "tools_preferred": "Docker",
            "industry": "tech",
            "company_size": "50-100",
            "about_role": "Test role",
            "responsibilities": "Test",
            "requirements": "Test",
        }
        defaults.update(kwargs)

        with app.app_context():
            result = db.session.execute(
                text("""
                    INSERT INTO job_market_data (
                        job_title, company_name, location,
                        employment_type, experience_level,
                        salary_min, salary_max,
                        skills_required, tools_preferred,
                        industry, company_size,
                        about_role, responsibilities, requirements,
                        posted_by_user_id, posted_date
                    ) VALUES (
                        :job_title, :company_name, :location,
                        :employment_type, :experience_level,
                        :salary_min, :salary_max,
                        :skills_required, :tools_preferred,
                        :industry, :company_size,
                        :about_role, :responsibilities, :requirements,
                        :employer_id, NOW()
                    )
                    RETURNING id
                """),
                {**defaults, "employer_id": employer["id"]},
            )
            job_id = result.scalar()
            db.session.commit()

        return {"id": job_id, "employer": employer, **defaults}

    return _make


@pytest.fixture
def make_application(app, make_user, make_job):
    """สร้าง application ใน DB"""
    from extensions import db
    from sqlalchemy import text

    def _make(user=None, job=None, **kwargs):
        if user is None:
            user = make_user(role="candidate")
        if job is None:
            job = make_job()

        defaults = {
            "full_name": "Test User",
            "email": user["email"],
            "phone": "0891234567",
            "location": "Bangkok",
            "resume_filename": "resume.pdf",
            "resume_url": "https://xxx.supabase.co/storage/v1/object/public/resumes/r.pdf",
            "cover_letter": "Test cover",
            "status": "applied",
        }
        defaults.update(kwargs)

        with app.app_context():
            result = db.session.execute(
                text("""
                    INSERT INTO applications (
                        user_id, job_id, full_name, email, phone, location,
                        resume_filename, resume_url, cover_letter, status, applied_date
                    ) VALUES (
                        :user_id, :job_id, :full_name, :email, :phone, :location,
                        :resume_filename, :resume_url, :cover_letter, :status, NOW()
                    )
                    RETURNING id
                """),
                {**defaults, "user_id": user["id"], "job_id": job["id"]},
            )
            app_id = result.scalar()
            db.session.commit()

        return {"id": app_id, "user": user, "job": job, **defaults}

    return _make


# ============================================================
# CLEAR COOKIES BETWEEN TESTS
# ============================================================

@pytest.fixture(autouse=True)
def _clear_client_cookies(client):
    """ล้าง cookie ของ Flask test_client ระหว่าง test"""
    yield
    try:
        client._cookies.clear()
    except AttributeError:
        try:
            client.cookie_jar.clear()
        except AttributeError:
            pass
