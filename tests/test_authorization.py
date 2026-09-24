"""test_authorization.py — Tests สำหรับ 401/403/IDOR"""
import pytest


class TestAuthenticationRequired:

    @pytest.mark.parametrize("method,path", [
        ("GET", "/api/auth/me"),
        ("GET", "/api/applications"),
        ("GET", "/api/favorites"),
        ("GET", "/api/employer/jobs"),
    ])
    def test_requires_auth(self, client, method, path):
        response = client.open(path, method=method)
        assert response.status_code == 401
        assert response.get_json()["error"]["code"] in ("MISSING_TOKEN", "UNAUTHORIZED")


class TestRoleRequired:

    def test_employer_jobs_requires_employer_role(self, client, make_user):
        make_user(email="cand@test.local", password="TestPass123", role="candidate")
        client.post("/api/auth/login", json={
            "email": "cand@test.local",
            "password": "TestPass123",
        })
        response = client.get("/api/employer/jobs")
        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "FORBIDDEN"

    def test_employer_can_access_employer_jobs(self, client, make_user):
        make_user(email="emp@test.local", password="TestPass123",
                  role="employer", company_name="Acme")
        client.post("/api/auth/login", json={
            "email": "emp@test.local",
            "password": "TestPass123",
        })
        response = client.get("/api/employer/jobs")
        assert response.status_code == 200


class TestOwnershipIDOR:

    def test_profile_idor_blocked(self, client, make_user, login):
        user_a = make_user(email="a@test.local", password="TestPass123")
        user_b = make_user(email="b@test.local", password="TestPass123")
        login(user_a)
        response = client.get(f"/api/profile/{user_b['id']}")
        assert response.status_code == 403

    def test_full_profile_idor_blocked(self, client, make_user, login):
        user_a = make_user(email="a2@test.local", password="TestPass123")
        user_b = make_user(email="b2@test.local", password="TestPass123")
        login(user_a)
        response = client.get(f"/api/profile/{user_b['id']}/full")
        assert response.status_code == 403

    def test_user_applications_idor_blocked(self, client, make_user, login):
        user_a = make_user(email="a3@test.local", password="TestPass123")
        user_b = make_user(email="b3@test.local", password="TestPass123")
        login(user_a)
        response = client.get(f"/api/applications/user/{user_b['id']}")
        assert response.status_code == 403

    def test_application_detail_idor_blocked(self, client, make_user, make_application, login):
        user_a = make_user(email="a4@test.local", password="TestPass123")
        user_b = make_user(email="b4@test.local", password="TestPass123")
        app_b = make_application(user=user_b)
        login(user_a)
        response = client.get(f"/api/applications/{app_b['id']}/detail")
        assert response.status_code == 403

    def test_delete_resume_idor_blocked(self, client, make_user, login):
        user_a = make_user(email="a5@test.local", password="TestPass123")
        user_b = make_user(email="b5@test.local", password="TestPass123")
        login(user_a)
        response = client.delete(f"/api/resume/{user_b['id']}")
        assert response.status_code == 403

    def test_employer_job_applications_idor_blocked(self, client, make_user, make_job, login):
        emp_a = make_user(email="empa@test.local", password="TestPass123",
                          role="employer", company_name="A")
        emp_b = make_user(email="empb@test.local", password="TestPass123",
                          role="employer", company_name="B")
        job_b = make_job(employer=emp_b)
        login(emp_a)
        response = client.get(f"/api/employer/jobs/{job_b['id']}/applications")
        assert response.status_code == 403

    def test_employer_can_view_own_job_applications(self, client, make_user, make_job, login):
        emp = make_user(email="emp-own@test.local", password="TestPass123",
                        role="employer", company_name="Own")
        job = make_job(employer=emp)
        login(emp)
        response = client.get(f"/api/employer/jobs/{job['id']}/applications")
        assert response.status_code == 200

    def test_employer_update_status_idor_blocked(self, client, make_user, make_application, make_job, login):
        emp_a = make_user(email="empa2@test.local", password="TestPass123",
                          role="employer", company_name="A2")
        emp_b = make_user(email="empb2@test.local", password="TestPass123",
                          role="employer", company_name="B2")
        cand = make_user(email="cand2@test.local", password="TestPass123")
        job_b = make_job(employer=emp_b)
        app = make_application(user=cand, job=job_b)
        login(emp_a)
        response = client.put(
            f"/api/employer/applications/{app['id']}/status",
            json={"status": "reviewing"},
        )
        assert response.status_code == 403


class TestOwnershipAllowed:

    def test_own_profile_ok(self, client, make_user, login):
        user = make_user(email="own@test.local", password="TestPass123")
        login(user)
        response = client.get(f"/api/profile/{user['id']}")
        assert response.status_code == 200

    def test_own_applications_ok(self, client, make_user, login):
        user = make_user(email="ownapp@test.local", password="TestPass123")
        login(user)
        response = client.get(f"/api/applications/user/{user['id']}")
        assert response.status_code == 200
