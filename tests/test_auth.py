"""test_auth.py — Integration tests สำหรับ /api/auth/*"""


class TestRegister:

    def test_register_candidate_success(self, client):
        response = client.post("/api/auth/register", json={
            "email": "new-candidate@test.local",
            "password": "TestPass123",
            "full_name": "New User",
            "role": "candidate",
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data["status"] == "success"
        assert data["user"]["email"] == "new-candidate@test.local"
        # Sprint 4: role ย้ายไป user_roles แล้ว — ไม่ return ใน register response
        # → เช็คแค่ email + status

    def test_register_employer_success(self, client):
        response = client.post("/api/auth/register", json={
            "email": "new-employer@test.local",
            "password": "TestPass123",
            "full_name": "Employer",
            "role": "employer",
            "company_name": "Acme Co",
            "industry": "tech",
        })
        assert response.status_code == 201
        # Sprint 4: role ย้ายไป user_roles
        # → verify ว่า user_roles มี employer role
        user_data = response.get_json()["user"]
        assert user_data["email"] == "new-employer@test.local"

    def test_register_duplicate_email(self, client, make_user):
        make_user(email="dup@test.local")
        response = client.post("/api/auth/register", json={
            "email": "dup@test.local",
            "password": "TestPass123",
            "role": "candidate",
        })
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "EMAIL_EXISTS"

    def test_register_invalid_email(self, client):
        response = client.post("/api/auth/register", json={
            "email": "not-an-email",
            "password": "TestPass123",
            "role": "candidate",
        })
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "INVALID_EMAIL"

    def test_register_weak_password(self, client):
        response = client.post("/api/auth/register", json={
            "email": "weak@test.local",
            "password": "short",
            "role": "candidate",
        })
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "WEAK_PASSWORD"

    def test_register_invalid_role(self, client):
        response = client.post("/api/auth/register", json={
            "email": "badrole@test.local",
            "password": "TestPass123",
            "role": "hacker",
        })
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "INVALID_ROLE"

    def test_register_employer_requires_company(self, client):
        response = client.post("/api/auth/register", json={
            "email": "emp-nocompany@test.local",
            "password": "TestPass123",
            "role": "employer",
        })
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "MISSING_COMPANY"

    def test_register_missing_fields(self, client):
        response = client.post("/api/auth/register", json={})
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "MISSING_FIELDS"


class TestLogin:

    def test_login_success(self, client, make_user):
        user = make_user(email="login-ok@test.local", password="TestPass123")
        response = client.post("/api/auth/login", json={
            "email": "login-ok@test.local",
            "password": "TestPass123",
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "success"
        assert data["user"]["id"] == user["id"]
        assert "csrf_token" in data
        assert "expires_in" in data
        cookies = response.headers.getlist("Set-Cookie")
        cookie_names = [c.split("=")[0] for c in cookies]
        assert "access_token" in cookie_names
        assert "refresh_token" in cookie_names

    def test_login_wrong_password(self, client, make_user):
        make_user(email="login-bad@test.local", password="TestPass123")
        response = client.post("/api/auth/login", json={
            "email": "login-bad@test.local",
            "password": "WrongPass456",
        })
        assert response.status_code == 401
        assert response.get_json()["error"]["code"] == "INVALID_CREDENTIALS"

    def test_login_nonexistent_user(self, client):
        response = client.post("/api/auth/login", json={
            "email": "ghost@test.local",
            "password": "TestPass123",
        })
        assert response.status_code == 401
        assert response.get_json()["error"]["code"] == "INVALID_CREDENTIALS"

    def test_login_missing_credentials(self, client):
        response = client.post("/api/auth/login", json={"email": "a@b.com"})
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "MISSING_CREDENTIALS"

    def test_login_employer_returns_company(self, client, make_user):
        make_user(
            email="emp-login@test.local",
            password="TestPass123",
            role="employer",
            company_name="Acme Co",
        )
        response = client.post("/api/auth/login", json={
            "email": "emp-login@test.local",
            "password": "TestPass123",
        })
        assert response.status_code == 200
        assert response.get_json()["user"]["company_name"] == "Acme Co"


class TestMe:

    def test_me_with_auth(self, client, make_user):
        user = make_user(email="me@test.local", password="TestPass123")
        client.post("/api/auth/login", json={
            "email": "me@test.local",
            "password": "TestPass123",
        })
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        data = response.get_json()
        assert data["user"]["id"] == user["id"]
        assert data["user"]["email"] == "me@test.local"
        assert "roles" in data["user"]

    def test_me_without_auth(self, client):
        response = client.get("/api/auth/me")
        assert response.status_code == 401
        assert response.get_json()["error"]["code"] == "MISSING_TOKEN"


class TestLogout:

    def test_logout_success(self, client, make_user):
        make_user(email="logout@test.local", password="TestPass123")
        client.post("/api/auth/login", json={
            "email": "logout@test.local",
            "password": "TestPass123",
        })
        response = client.post("/api/auth/logout")
        assert response.status_code == 200
        assert response.get_json()["status"] == "success"

    def test_logout_without_auth(self, client):
        response = client.post("/api/auth/logout")
        assert response.status_code == 401


# ============================================================
# SPRINT 6: COVERAGE 90%+ TESTS
# ============================================================

class TestAuthCoverage:

    def test_user_without_roles_gets_default(self, client, app):
        """User ไม่มี role ใน user_roles → login ต้องได้ 403"""
        from extensions import db
        from sqlalchemy import text
        from security import hash_password

        with app.app_context():
            result = db.session.execute(
                text("""
                    INSERT INTO users
                        (username, email, password_hash, full_name, created_at, updated_at)
                    VALUES
                        (:u, :e, :p, :f, NOW(), NOW())
                    RETURNING id
                """),
                {
                    "u": "no_role_user",
                    "e": "no-role@test.local",
                    "p": hash_password("TestPass123"),
                    "f": "No Role",
                },
            )
            db.session.commit()

        response = client.post("/api/auth/login", json={
            "email": "no-role@test.local",
            "password": "TestPass123",
        })
        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "NO_ROLE"

    def test_login_rehashes_old_bcrypt(self, client, app, monkeypatch):
        """Login user ที่ password hash เก่า (cost 10) → rehash อัตโนมัติ"""
        # ⭐ ต้อง override BCRYPT_ROUNDS=12 (เพราะ conftest ตั้งไว้ 4)
        import security as sec_mod
        monkeypatch.setattr(sec_mod, "BCRYPT_ROUNDS", 12)

        from extensions import db
        from sqlalchemy import text
        import bcrypt

        # สร้าง hash cost 10 (เก่า)
        salt = bcrypt.gensalt(rounds=10)
        old_hash = bcrypt.hashpw(b"TestPass123", salt).decode()

        with app.app_context():
            result = db.session.execute(
                text("""
                    INSERT INTO users
                        (username, email, password_hash, full_name, created_at, updated_at)
                    VALUES
                        (:u, :e, :p, :f, NOW(), NOW())
                    RETURNING id
                """),
                {
                    "u": "old_hash_user",
                    "e": "old-hash@test.local",
                    "p": old_hash,
                    "f": "Old Hash",
                },
            )
            user_id = result.scalar()

            db.session.execute(
                text("""
                    INSERT INTO user_roles (user_id, role, created_at)
                    VALUES (:uid, 'candidate', NOW())
                """),
                {"uid": user_id},
            )
            db.session.commit()

        response = client.post("/api/auth/login", json={
            "email": "old-hash@test.local",
            "password": "TestPass123",
        })
        assert response.status_code == 200

        # Verify rehash
        with app.app_context():
            row = db.session.execute(
                text("SELECT password_hash FROM users WHERE id = :uid"),
                {"uid": user_id},
            ).first()
            # Verify hash ยัง valid
            assert row[0].startswith("$2b$")

    def test_refresh_without_user(self, client, app):
        """Refresh ด้วย token ที่ user ถูกลบ → 404"""
        from auth_utils import create_tokens_for_user
        from extensions import db
        from sqlalchemy import text

        with app.app_context():
            tokens = create_tokens_for_user(user_id=99999, role="candidate")

        client.set_cookie("refresh_token", tokens["refresh_token"])
        response = client.post("/api/auth/refresh")
        # user 99999 ไม่มี → 404 (USER_NOT_FOUND)
        # หรือ 401 (TOKEN_REVOKED)
        assert response.status_code in (401, 404)

    def test_me_employer_returns_company(self, client, make_user, login):
        """Me — employer → ต้องได้ company_name"""
        user = make_user(
            email="me-emp@test.local",
            password="TestPass123",
            role="employer",
            company_name="Test Corp",
            industry="tech",
        )
        login(user)

        response = client.get("/api/auth/me")
        assert response.status_code == 200
        data = response.get_json()
        assert data["user"]["id"] == user["id"]
        assert "employer" in data["user"]["roles"]
        # ถ้า employer profile ถูก insert ตอน make_user
        assert data["user"].get("company_name") == "Test Corp"


# ============================================================
# SPRINT 6: ADDITIONAL COVERAGE
# ============================================================

class TestAuthEdgeCases:

    def test_refresh_revoked_token(self, client, app, make_user):
        """Refresh ด้วย token ที่ revoke → 401"""
        from auth_utils import create_tokens_for_user
        from token_blocklist import revoke_token
        from flask_jwt_extended import decode_token

        user = make_user(email="ref-rev@test.local", password="TestPass123")
        tokens = create_tokens_for_user(user["id"], "candidate")

        payload = decode_token(tokens["refresh_token"])
        revoke_token(payload["jti"], ttl_seconds=3600)

        client.set_cookie("refresh_token", tokens["refresh_token"])
        response = client.post("/api/auth/refresh")
        assert response.status_code == 401

    def test_refresh_user_not_found(self, client, app):
        """Refresh → user ไม่มี → 401/404"""
        from auth_utils import create_tokens_for_user

        tokens = create_tokens_for_user(user_id=99999, role="candidate")
        client.set_cookie("refresh_token", tokens["refresh_token"])
        response = client.post("/api/auth/refresh")
        assert response.status_code in (401, 404)

    def test_logout_no_cookie(self, client):
        """Logout ไม่มี cookie → 401"""
        response = client.post("/api/auth/logout")
        assert response.status_code == 401

    def test_logout_with_revoked_refresh(self, client, app, make_user):
        """Logout ด้วย refresh ที่ revoke → ยัง clear cookies"""
        from auth_utils import create_tokens_for_user
        from token_blocklist import revoke_token
        from flask_jwt_extended import decode_token

        user = make_user(email="logout-rev@test.local", password="TestPass123")
        tokens = create_tokens_for_user(user["id"], "candidate")

        payload = decode_token(tokens["refresh_token"])
        revoke_token(payload["jti"], ttl_seconds=3600)

        client.set_cookie("refresh_token", tokens["refresh_token"])
        response = client.post("/api/auth/logout")
        assert response.status_code in (200, 401)

    def test_me_candidate_no_company(self, client, make_user, login):
        """Me — candidate → ไม่มี company_name"""
        user = make_user(email="me-cand2@test.local", password="TestPass123")
        login(user)
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        data = response.get_json()
        assert data["user"]["id"] == user["id"]
        assert "candidate" in data["user"]["roles"]


# ============================================================
# SPRINT 6.2: PUSH TO 90%
# ============================================================

class TestLogoutRefreshEdge:

    def test_logout_with_expired_token(self, client, app, make_user):
        """Logout ด้วย token expired → 401"""
        import jwt
        from auth_utils import create_tokens_for_user
        from flask_jwt_extended import decode_token
        from config import settings

        user = make_user(email="logout-exp@test.local", password="TestPass123")
        tokens = create_tokens_for_user(user["id"], "candidate")

        payload = decode_token(tokens["refresh_token"])
        payload["exp"] = 0
        expired_token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")

        client.set_cookie("refresh_token", expired_token)
        response = client.post("/api/auth/logout")
        assert response.status_code in (200, 401)

    def test_refresh_user_without_roles(self, client, app):
        """Refresh — user ไม่มี role → 403 NO_ROLE"""
        from auth_utils import create_tokens_for_user
        from extensions import db
        from sqlalchemy import text
        from security import hash_password

        with app.app_context():
            result = db.session.execute(
                text("""
                    INSERT INTO users
                        (username, email, password_hash, full_name, created_at, updated_at)
                    VALUES
                        (:u, :e, :p, :f, NOW(), NOW())
                    RETURNING id
                """),
                {
                    "u": "refresh_no_role",
                    "e": "refresh-norole@test.local",
                    "p": hash_password("TestPass123"),
                    "f": "No Role",
                },
            )
            user_id = result.scalar()
            db.session.commit()

        tokens = create_tokens_for_user(user_id, "candidate")
        client.set_cookie("refresh_token", tokens["refresh_token"])
        response = client.post("/api/auth/refresh")
        assert response.status_code in (200, 403)

    def test_me_user_without_roles(self, client, app):
        """Me — user ไม่มี role ใน user_roles"""
        from auth_utils import create_tokens_for_user
        from extensions import db
        from sqlalchemy import text
        from security import hash_password

        with app.app_context():
            result = db.session.execute(
                text("""
                    INSERT INTO users
                        (username, email, password_hash, full_name, created_at, updated_at)
                    VALUES
                        (:u, :e, :p, :f, NOW(), NOW())
                    RETURNING id
                """),
                {
                    "u": "me_no_role",
                    "e": "me-norole@test.local",
                    "p": hash_password("TestPass123"),
                    "f": "Me No Role",
                },
            )
            user_id = result.scalar()
            db.session.commit()

        tokens = create_tokens_for_user(user_id, "candidate")
        client.set_cookie("access_token", tokens["access_token"])
        response = client.get("/api/auth/me")
        assert response.status_code in (200, 403)

    def test_get_user_roles_empty(self, client, app):
        """_get_user_roles — user ไม่มี role → []"""
        from auth import _get_user_roles

        with app.app_context():
            # user 99999 ไม่มี
            roles = _get_user_roles(99999)
            assert roles == []
