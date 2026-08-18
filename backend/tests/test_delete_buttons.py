"""Iter 9 — Backend tests for the new DELETE buttons feature.

Tests DELETE endpoints for:
- Survival tournaments  (DELETE /api/sv/tournaments/{tid})
- ScoreAndLive tournaments (DELETE /api/sal/tournaments/{tid})
- TheBestTiket rooms (DELETE /api/rooms/{room_id})
- FantaGiornata leagues (DELETE /api/fg/leagues/{league_id})

Each test creates a throwaway "ZZ_DELETE_TEST" entity and then deletes it,
verifying the entity really disappeared from the collection listing.
It also verifies non-admin players get 403 when attempting DELETE.
"""
import os
import uuid
import pytest
import requests

def _load_backend_url():
    v = os.environ.get("REACT_APP_BACKEND_URL")
    if not v:
        try:
            with open("/app/frontend/.env") as f:
                for line in f:
                    if line.startswith("REACT_APP_BACKEND_URL="):
                        v = line.split("=", 1)[1].strip()
                        break
        except Exception:
            pass
    if not v:
        raise RuntimeError("REACT_APP_BACKEND_URL not set")
    return v.rstrip("/")

BASE_URL = _load_backend_url()
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "e1qa.admin@gmail.com"
ADMIN_PASSWORD = "Test1234!"
PLAYER_USERNAME = "e1_qa_player"
PLAYER_PASSWORD = "Test1234!"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/admin/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text}")
    return r.json()["token"]


@pytest.fixture(scope="module")
def player_token():
    r = requests.post(f"{API}/auth/player/login",
                      json={"username": PLAYER_USERNAME, "password": PLAYER_PASSWORD}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"Player login failed: {r.status_code} {r.text}")
    return r.json()["token"]


def _h(tok): return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _uniq(prefix): return f"ZZ_DELETE_TEST_{prefix}_{uuid.uuid4().hex[:6]}"


# ---------- Survival ----------
class TestSurvivalDelete:
    def test_create_and_delete(self, admin_token):
        name = _uniq("SV")
        r = requests.post(f"{API}/sv/tournaments", headers=_h(admin_token),
                          json={"name": name, "season": "2026-27",
                                "initial_lives": 3, "start_matchday": 1}, timeout=30)
        assert r.status_code == 200, f"create failed: {r.status_code} {r.text}"
        tid = r.json()["id"]
        assert r.json()["name"] == name

        # Delete
        r = requests.delete(f"{API}/sv/tournaments/{tid}", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200, f"delete failed: {r.status_code} {r.text}"
        assert r.json().get("ok") is True

        # Verify gone
        r = requests.get(f"{API}/sv/tournaments", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200
        ids = [t["id"] for t in r.json()]
        assert tid not in ids, "Tournament still listed after delete"

        # Detail 404
        r = requests.get(f"{API}/sv/tournaments/{tid}", headers=_h(admin_token), timeout=30)
        assert r.status_code == 404

    def test_player_cannot_delete(self, admin_token, player_token):
        name = _uniq("SVP")
        r = requests.post(f"{API}/sv/tournaments", headers=_h(admin_token),
                          json={"name": name, "season": "2026-27",
                                "initial_lives": 3, "start_matchday": 1}, timeout=30)
        assert r.status_code == 200
        tid = r.json()["id"]
        try:
            r = requests.delete(f"{API}/sv/tournaments/{tid}",
                                headers=_h(player_token), timeout=30)
            assert r.status_code in (401, 403), f"expected 403, got {r.status_code}"
        finally:
            requests.delete(f"{API}/sv/tournaments/{tid}", headers=_h(admin_token), timeout=30)


# ---------- ScoreAndLive ----------
class TestSalDelete:
    def test_create_and_delete_no_picks(self, admin_token):
        name = _uniq("SAL")
        r = requests.post(f"{API}/sal/tournaments", headers=_h(admin_token),
                          json={"name": name, "initial_lives": 10,
                                "start_matchday": 1, "season": "2026-27"}, timeout=30)
        assert r.status_code == 200, f"create failed: {r.status_code} {r.text}"
        tid = r.json()["id"]

        r = requests.delete(f"{API}/sal/tournaments/{tid}", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200, f"delete failed: {r.status_code} {r.text}"
        body = r.json()
        assert body.get("ok") is True
        assert body.get("deleted_picks") == 0

        # Verify gone
        r = requests.get(f"{API}/sal/tournaments/{tid}", headers=_h(admin_token), timeout=30)
        assert r.status_code == 404

    def test_player_cannot_delete(self, admin_token, player_token):
        name = _uniq("SALP")
        r = requests.post(f"{API}/sal/tournaments", headers=_h(admin_token),
                          json={"name": name, "initial_lives": 10,
                                "start_matchday": 1, "season": "2026-27"}, timeout=30)
        assert r.status_code == 200
        tid = r.json()["id"]
        try:
            r = requests.delete(f"{API}/sal/tournaments/{tid}",
                                headers=_h(player_token), timeout=30)
            assert r.status_code in (401, 403)
        finally:
            requests.delete(f"{API}/sal/tournaments/{tid}", headers=_h(admin_token), timeout=30)


# ---------- TheBestTiket rooms ----------
class TestTiketDelete:
    def test_create_and_delete(self, admin_token):
        name = _uniq("TK")
        r = requests.post(f"{API}/rooms", headers=_h(admin_token),
                          json={"name": name, "matchday": 1, "max_events": 5,
                                "game": "thebesttiket"}, timeout=30)
        assert r.status_code == 200, f"create failed: {r.status_code} {r.text}"
        rid = r.json()["id"]

        r = requests.delete(f"{API}/rooms/{rid}", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200, f"delete failed: {r.status_code} {r.text}"
        assert r.json().get("ok") is True

        r = requests.get(f"{API}/rooms?game=thebesttiket", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200
        ids = [x["id"] for x in r.json()]
        assert rid not in ids

    def test_player_cannot_delete(self, admin_token, player_token):
        name = _uniq("TKP")
        r = requests.post(f"{API}/rooms", headers=_h(admin_token),
                          json={"name": name, "matchday": 1, "max_events": 5,
                                "game": "thebesttiket"}, timeout=30)
        assert r.status_code == 200
        rid = r.json()["id"]
        try:
            r = requests.delete(f"{API}/rooms/{rid}", headers=_h(player_token), timeout=30)
            assert r.status_code in (401, 403)
        finally:
            requests.delete(f"{API}/rooms/{rid}", headers=_h(admin_token), timeout=30)


# ---------- FantaGiornata ----------
class TestFgDelete:
    def test_create_and_delete(self, admin_token):
        name = _uniq("FG")
        r = requests.post(f"{API}/fg/leagues", headers=_h(admin_token),
                          json={"name": name}, timeout=30)
        assert r.status_code == 200, f"create failed: {r.status_code} {r.text}"
        lid = r.json()["id"]

        r = requests.delete(f"{API}/fg/leagues/{lid}", headers=_h(admin_token), timeout=30)
        assert r.status_code == 200, f"delete failed: {r.status_code} {r.text}"
        assert r.json().get("ok") is True

        r = requests.get(f"{API}/fg/leagues/{lid}", headers=_h(admin_token), timeout=30)
        assert r.status_code == 404

    def test_player_cannot_delete(self, admin_token, player_token):
        name = _uniq("FGP")
        r = requests.post(f"{API}/fg/leagues", headers=_h(admin_token),
                          json={"name": name}, timeout=30)
        assert r.status_code == 200
        lid = r.json()["id"]
        try:
            r = requests.delete(f"{API}/fg/leagues/{lid}", headers=_h(player_token), timeout=30)
            # fg uses _require_league_admin (403 for non-admin)
            assert r.status_code in (401, 403)
        finally:
            requests.delete(f"{API}/fg/leagues/{lid}", headers=_h(admin_token), timeout=30)
