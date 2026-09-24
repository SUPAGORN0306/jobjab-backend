"""test_profile.py — Profile CRUD + ownership"""


class TestProfileRead:

    def test_get_own_profile(self, client, make_user, login):
        user = make_user(email="read@test.local", password="TestPass123")
        login(user)
        response = client.get(f"/api/profile/{user['id']}")
        assert response.status_code == 200
        data = response.get_json()
        assert data["profile"]["id"] == user["id"]
        assert data["profile"]["email"] == "read@test.local"

    def test_get_full_profile(self, client, make_user, login):
        user = make_user(email="full@test.local", password="TestPass123")
        login(user)
        response = client.get(f"/api/profile/{user['id']}/full")
        assert response.status_code == 200
        data = response.get_json()
        assert "profile" in data
        assert "skills" in data
        assert "experiences" in data
        assert "educations" in data


class TestProfileUpdate:

    def test_update_full_name(self, client, make_user, login):
        user = make_user(email="upd@test.local", password="TestPass123")
        login(user)
        response = client.put(f"/api/profile/{user['id']}", json={
            "full_name": "Updated Name",
            "bio": "New bio",
        })
        assert response.status_code == 200
        response = client.get(f"/api/profile/{user['id']}")
        data = response.get_json()
        assert data["profile"]["full_name"] == "Updated Name"

    def test_update_invalid_phone(self, client, make_user, login):
        user = make_user(email="badphone@test.local", password="TestPass123")
        login(user)
        response = client.put(f"/api/profile/{user['id']}", json={
            "phone": "abc-not-phone",
        })
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "INVALID_PHONE"

    def test_update_sanitizes_xss(self, client, make_user, login):
        user = make_user(email="xss@test.local", password="TestPass123")
        login(user)
        client.put(f"/api/profile/{user['id']}", json={
            "bio": "<script>alert('xss')</script>Hello",
        })
        response = client.get(f"/api/profile/{user['id']}")
        data = response.get_json()
        assert "<script>" not in (data["profile"]["bio"] or "")
