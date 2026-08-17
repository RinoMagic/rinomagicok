"""Iteration 7 — Notifiche Personalizzate: reminder offsets settings API."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # frontend/.env fallback
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL"):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")

ADMIN_EMAIL = "e1qa.admin@gmail.com"
ADMIN_PASSWORD = "Test1234!"
PLAYER_USERNAME = "e1_qa_player"
PLAYER_PASSWORD = "Test1234!"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/admin/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return r.json().get("access_token") or r.json().get("token")


@pytest.fixture(scope="module")
def player_token():
    r = requests.post(f"{BASE_URL}/api/auth/player/login",
                      json={"username": PLAYER_USERNAME, "password": PLAYER_PASSWORD}, timeout=15)
    assert r.status_code == 200, f"player login failed: {r.status_code} {r.text}"
    return r.json().get("access_token") or r.json().get("token")


@pytest.fixture(scope="module")
def original_offsets(admin_token):
    r = requests.get(f"{BASE_URL}/api/settings/reminders",
                     headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)
    if r.status_code == 200:
        return r.json().get("offsets_minutes", [1440, 180, 60])
    return [1440, 180, 60]


def test_backend_healthy():
    r = requests.get(f"{BASE_URL}/api/games", timeout=10)
    # 200 or 401 both indicate app healthy (not 5xx)
    assert r.status_code < 500


def test_get_reminders_admin_default(admin_token, original_offsets):
    r = requests.get(f"{BASE_URL}/api/settings/reminders",
                     headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert "offsets_minutes" in data
    assert isinstance(data["offsets_minutes"], list)


def test_get_reminders_unauthenticated():
    r = requests.get(f"{BASE_URL}/api/settings/reminders", timeout=10)
    assert r.status_code in (401, 403)


def test_get_reminders_player_forbidden(player_token):
    r = requests.get(f"{BASE_URL}/api/settings/reminders",
                     headers={"Authorization": f"Bearer {player_token}"}, timeout=10)
    assert r.status_code == 403


def test_put_reminders_player_forbidden(player_token):
    r = requests.put(f"{BASE_URL}/api/settings/reminders",
                     headers={"Authorization": f"Bearer {player_token}"},
                     json={"offsets_minutes": [60]}, timeout=10)
    assert r.status_code == 403


def test_put_reminders_unauthenticated():
    r = requests.put(f"{BASE_URL}/api/settings/reminders",
                     json={"offsets_minutes": [60]}, timeout=10)
    assert r.status_code in (401, 403)


def test_put_reminders_validation_dedupe_sort_clamp(admin_token):
    # 3 is below 5 -> dropped; 20000 above 10080 -> dropped; 60 duplicated
    payload = {"offsets_minutes": [1440, 720, 60, 30, 3, 60, 20000]}
    r = requests.put(f"{BASE_URL}/api/settings/reminders",
                     headers={"Authorization": f"Bearer {admin_token}"},
                     json=payload, timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["offsets_minutes"] == [1440, 720, 60, 30], data


def test_put_reminders_persistence(admin_token):
    payload = {"offsets_minutes": [1440, 360, 60]}
    r = requests.put(f"{BASE_URL}/api/settings/reminders",
                     headers={"Authorization": f"Bearer {admin_token}"},
                     json=payload, timeout=10)
    assert r.status_code == 200
    assert r.json()["offsets_minutes"] == [1440, 360, 60]

    r2 = requests.get(f"{BASE_URL}/api/settings/reminders",
                      headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)
    assert r2.status_code == 200
    assert r2.json()["offsets_minutes"] == [1440, 360, 60]


def test_put_reminders_empty_list_uses_default_on_get(admin_token):
    # Empty list saved -> _get returns DEFAULT since offs falsy
    r = requests.put(f"{BASE_URL}/api/settings/reminders",
                     headers={"Authorization": f"Bearer {admin_token}"},
                     json={"offsets_minutes": []}, timeout=10)
    assert r.status_code == 200
    assert r.json()["offsets_minutes"] == []

    r2 = requests.get(f"{BASE_URL}/api/settings/reminders",
                      headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)
    assert r2.status_code == 200
    # per implementation: empty falls back to default
    assert r2.json()["offsets_minutes"] == [1440, 180, 60]


def test_restore_defaults(admin_token, original_offsets):
    # Restore to sensible default [1440,180,60] for the user
    r = requests.put(f"{BASE_URL}/api/settings/reminders",
                     headers={"Authorization": f"Bearer {admin_token}"},
                     json={"offsets_minutes": [1440, 180, 60]}, timeout=10)
    assert r.status_code == 200
    assert r.json()["offsets_minutes"] == [1440, 180, 60]
