"""test_favorites.py — Favorites toggle + list"""


class TestFavorites:

    def test_list_empty(self, client, make_user, login):
        user = make_user(email="fav0@test.local", password="TestPass123")
        login(user)
        response = client.get("/api/favorites")
        assert response.status_code == 200
        assert response.get_json()["favorites"] == []

    def test_toggle_add(self, client, make_user, make_job, login):
        user = make_user(email="fav1@test.local", password="TestPass123")
        job = make_job()
        login(user)
        response = client.post("/api/favorites/toggle", json={"job_id": job["id"]})
        assert response.status_code == 200
        assert response.get_json()["favorited"] is True
        response = client.get("/api/favorites")
        assert len(response.get_json()["favorites"]) == 1

    def test_toggle_remove(self, client, make_user, make_job, login):
        user = make_user(email="fav2@test.local", password="TestPass123")
        job = make_job()
        login(user)
        client.post("/api/favorites/toggle", json={"job_id": job["id"]})
        response = client.post("/api/favorites/toggle", json={"job_id": job["id"]})
        assert response.status_code == 200
        assert response.get_json()["favorited"] is False
        response = client.get("/api/favorites")
        assert response.get_json()["favorites"] == []

    def test_toggle_missing_job_id(self, client, make_user, login):
        user = make_user(email="fav3@test.local", password="TestPass123")
        login(user)
        response = client.post("/api/favorites/toggle", json={})
        assert response.status_code == 400

    def test_toggle_nonexistent_job(self, client, make_user, login):
        user = make_user(email="fav4@test.local", password="TestPass123")
        login(user)
        response = client.post("/api/favorites/toggle", json={"job_id": 999999})
        assert response.status_code == 404

    def test_favorites_isolated_per_user(self, client, make_user, make_job, login):
        user_a = make_user(email="fava@test.local", password="TestPass123")
        user_b = make_user(email="favb@test.local", password="TestPass123")
        job = make_job()
        login(user_a)
        client.post("/api/favorites/toggle", json={"job_id": job["id"]})
        client.post("/api/auth/logout")
        login(user_b)
        response = client.get("/api/favorites")
        assert response.get_json()["favorites"] == []
