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
        assert data["user"]["role"] == "candidate"

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
        assert response.get_json()["user"]["role"] == "employer"

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
