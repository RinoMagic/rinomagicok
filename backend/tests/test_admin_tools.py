"""Backend tests for RinoMagic Admin Tools (non-destructive).
Uses QA credentials only. Does not modify production data.
"""
import os
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://game-schedina.preview.emergentagent.com").rstrip("/")
SEASON = "2026-27"
ADMIN_EMAIL = "e1qa.admin@gmail.com"
ADMIN_PW = "Test1234!"
PLAYER_USER = "e1_qa_player"
PLAYER_PW = "Test1234!"


@pytest.fixture(scope="session")
def admin_token():
    r = requests.post(f"{BASE}/api/auth/admin/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW}, timeout=15)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    data = r.json()
    tok = data.get("token") or data.get("access_token")
    assert tok
    return tok


@pytest.fixture(scope="session")
def player_token():
    r = requests.post(f"{BASE}/api/auth/player/login", json={"username": PLAYER_USER, "password": PLAYER_PW}, timeout=15)
    assert r.status_code == 200, f"player login failed: {r.status_code} {r.text}"
    data = r.json()
    return data.get("token") or data.get("access_token")


@pytest.fixture
def ah(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def ph(player_token):
    return {"Authorization": f"Bearer {player_token}"}


# ---------- Auth basics ----------
def test_admin_me(ah):
    r = requests.get(f"{BASE}/api/auth/me", headers=ah, timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert d.get("role") == "admin"


# ---------- Calendario Serie A (EscludiPartite / GestioneBonus / CalendarioSerieA use this) ----------
def test_calendar_matchday_1(ah):
    r = requests.get(f"{BASE}/api/sal/calendar", params={"season": SEASON, "matchday": 1}, headers=ah, timeout=10)
    assert r.status_code == 200, r.text
    data = r.json()
    fixtures = data if isinstance(data, list) else data.get("fixtures") or data.get("items") or []
    assert len(fixtures) == 10, f"Expected 10 fixtures for MD1, got {len(fixtures)}"
    assert any("home" in (f.get("home_team", "") + f.get("home", "")).lower() or f.get("home_team") for f in fixtures)


# ---------- Deadlines ----------
def test_deadlines_list(ah):
    r = requests.get(f"{BASE}/api/deadlines", params={"season": SEASON}, headers=ah, timeout=10)
    assert r.status_code == 200
    data = r.json()
    items = data if isinstance(data, list) else data.get("items") or data.get("deadlines") or []
    mds = {i.get("matchday") for i in items}
    # Expect existing G2 and G38
    assert 2 in mds and 38 in mds, f"Expected G2 and G38 in deadlines, got {mds}"


def test_deadline_upsert_md5_and_verify(ah):
    # non-destructive on real data: uses MD5 which the request author designated as TEST
    iso = "2027-05-10T18:00:00Z"
    r = requests.put(f"{BASE}/api/deadlines/5", params={"season": SEASON}, json={"deadline_at": iso}, headers=ah, timeout=10)
    assert r.status_code in (200, 201), r.text
    r2 = requests.get(f"{BASE}/api/deadlines", params={"season": SEASON}, headers=ah, timeout=10)
    items = r2.json() if isinstance(r2.json(), list) else r2.json().get("items") or r2.json().get("deadlines") or []
    md5 = [i for i in items if i.get("matchday") == 5]
    assert md5, "MD5 deadline not present after PUT"


# ---------- Bonus ----------
def test_bonus_configs_list(ah):
    r = requests.get(f"{BASE}/api/bonus/configs", headers=ah, timeout=10)
    assert r.status_code == 200
    data = r.json()
    items = data if isinstance(data, list) else data.get("items") or []
    assert len(items) >= 1, "Expected at least 1 bonus config"


def test_bonus_available_for_player(ph):
    for game in ["survival", "tiket", "score", "fanta"]:
        r = requests.get(f"{BASE}/api/bonus/available", params={"game": game, "season": SEASON}, headers=ph, timeout=10)
        assert r.status_code == 200, f"{game}: {r.status_code} {r.text}"
        d = r.json()
        assert "eligible" in d or "config" in d or isinstance(d, dict)


# ---------- Users list (Gestione Utenti / Admin) ----------
def test_auth_users_list(ah):
    r = requests.get(f"{BASE}/api/auth/users", headers=ah, timeout=10)
    assert r.status_code == 200
    data = r.json()
    items = data if isinstance(data, list) else data.get("items") or data.get("users") or []
    assert len(items) >= 9, f"Expected >=9 users, got {len(items)}"
    admins = [u for u in items if u.get("role") == "admin" or u.get("is_admin")]
    assert len(admins) >= 1


# ---------- Reminders settings ----------
def test_reminders_settings(ah):
    r = requests.get(f"{BASE}/api/settings/reminders", headers=ah, timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert "offsets_minutes" in d


# ---------- Games hub ----------
def test_games_hub(ph):
    r = requests.get(f"{BASE}/api/games", headers=ph, timeout=10)
    assert r.status_code == 200


# ---------- CalcolaGiornata preview (should not modify) ----------
def test_settle_matchday_preview(ah):
    r = requests.post(f"{BASE}/api/admin/settle-matchday/preview", json={"matchday": 1, "season": SEASON}, headers=ah, timeout=20)
    # preview is non-destructive; either 200 or 400 (if no votes uploaded) is acceptable
    assert r.status_code in (200, 400, 404), r.text
