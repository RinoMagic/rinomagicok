"""Iter 10 — Backend tests for the 'Archivia Torneo' feature.

Tests archive/unarchive endpoints for:
- Survival tournaments   (POST /api/sv/tournaments/{tid}/archive?archived=true|false)
- ScoreAndLive tournaments (POST /api/sal/tournaments/{tid}/archive)
- TheBestTiket rooms       (POST /api/rooms/{room_id}/archive)
- FantaGiornata leagues    (POST /api/fg/leagues/{league_id}/archive)

Each test creates a throwaway "ZZ_ARCH" entity, verifies the gate
(400 if not in the required 'concluso' state), forces the state via DB,
archives → unarchives, and cleans up with DELETE.
Also verifies non-admin players get 401/403 on archive.
"""
import os
import uuid
import pytest
import requests
from pymongo import MongoClient


def _load_env(key):
    v = os.environ.get(key)
    if v:
        return v
    try:
        for path in ("/app/frontend/.env", "/app/backend/.env"):
            with open(path) as f:
                for line in f:
                    if line.startswith(f"{key}="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
    except Exception:
        pass
    return None


BASE_URL = _load_env("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"
MONGO_URL = _load_env("MONGO_URL")
DB_NAME = _load_env("DB_NAME") or "schedinabar"

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


@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    return client[DB_NAME]


def _h(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _uniq(prefix):
    return f"ZZ_ARCH_{prefix}_{uuid.uuid4().hex[:6]}"


def _run(res):
    # Compat shim: pymongo calls are synchronous — just return result.
    return res


# ---------- Survival ----------
class TestSurvivalArchive:
    def test_archive_flow(self, admin_token, db):
        name = _uniq("SV")
        r = requests.post(f"{API}/sv/tournaments", headers=_h(admin_token),
                          json={"name": name, "season": "2026-27",
                                "initial_lives": 3, "start_matchday": 1}, timeout=30)
        assert r.status_code == 200, r.text
        tid = r.json()["id"]

        try:
            # Gate: cannot archive non-finished
            r = requests.post(f"{API}/sv/tournaments/{tid}/archive?archived=true",
                              headers=_h(admin_token), timeout=30)
            assert r.status_code == 400, f"expected 400 gate, got {r.status_code} {r.text}"
            assert "conclusi" in r.text.lower() or "finished" in r.text.lower()

            # Force finished via DB
            _run(db.sv_tournaments.update_one({"id": tid}, {"$set": {"status": "finished"}}))

            # Archive
            r = requests.post(f"{API}/sv/tournaments/{tid}/archive?archived=true",
                              headers=_h(admin_token), timeout=30)
            assert r.status_code == 200, r.text
            assert r.json().get("archived") is True

            # Present in list (include_finished=true) with archived=true
            r = requests.get(f"{API}/sv/tournaments?include_finished=true",
                             headers=_h(admin_token), timeout=30)
            assert r.status_code == 200
            match = next((t for t in r.json() if t["id"] == tid), None)
            assert match is not None, "archived tournament missing from include_finished list"
            assert match.get("archived") is True

            # Unarchive
            r = requests.post(f"{API}/sv/tournaments/{tid}/archive?archived=false",
                              headers=_h(admin_token), timeout=30)
            assert r.status_code == 200, r.text
            assert r.json().get("archived") is False
        finally:
            requests.delete(f"{API}/sv/tournaments/{tid}", headers=_h(admin_token), timeout=30)

    def test_player_cannot_archive(self, admin_token, player_token, db):
        name = _uniq("SVP")
        r = requests.post(f"{API}/sv/tournaments", headers=_h(admin_token),
                          json={"name": name, "season": "2026-27",
                                "initial_lives": 3, "start_matchday": 1}, timeout=30)
        assert r.status_code == 200
        tid = r.json()["id"]
        try:
            _run(db.sv_tournaments.update_one({"id": tid}, {"$set": {"status": "finished"}}))
            r = requests.post(f"{API}/sv/tournaments/{tid}/archive?archived=true",
                              headers=_h(player_token), timeout=30)
            assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"
        finally:
            requests.delete(f"{API}/sv/tournaments/{tid}", headers=_h(admin_token), timeout=30)


# ---------- ScoreAndLive ----------
class TestSalArchive:
    def test_archive_flow(self, admin_token, db):
        name = _uniq("SAL")
        r = requests.post(f"{API}/sal/tournaments", headers=_h(admin_token),
                          json={"name": name, "initial_lives": 10,
                                "start_matchday": 1, "season": "2026-27"}, timeout=30)
        assert r.status_code == 200, r.text
        tid = r.json()["id"]
        try:
            # gate
            r = requests.post(f"{API}/sal/tournaments/{tid}/archive?archived=true",
                              headers=_h(admin_token), timeout=30)
            assert r.status_code == 400, r.text

            _run(db.sal_tournaments.update_one({"id": tid}, {"$set": {"status": "finished"}}))

            r = requests.post(f"{API}/sal/tournaments/{tid}/archive?archived=true",
                              headers=_h(admin_token), timeout=30)
            assert r.status_code == 200, r.text
            assert r.json().get("archived") is True

            # Unarchive
            r = requests.post(f"{API}/sal/tournaments/{tid}/archive?archived=false",
                              headers=_h(admin_token), timeout=30)
            assert r.status_code == 200
            assert r.json().get("archived") is False
        finally:
            requests.delete(f"{API}/sal/tournaments/{tid}", headers=_h(admin_token), timeout=30)

    def test_player_cannot_archive(self, admin_token, player_token, db):
        name = _uniq("SALP")
        r = requests.post(f"{API}/sal/tournaments", headers=_h(admin_token),
                          json={"name": name, "initial_lives": 10,
                                "start_matchday": 1, "season": "2026-27"}, timeout=30)
        assert r.status_code == 200
        tid = r.json()["id"]
        try:
            _run(db.sal_tournaments.update_one({"id": tid}, {"$set": {"status": "finished"}}))
            r = requests.post(f"{API}/sal/tournaments/{tid}/archive?archived=true",
                              headers=_h(player_token), timeout=30)
            assert r.status_code in (401, 403)
        finally:
            requests.delete(f"{API}/sal/tournaments/{tid}", headers=_h(admin_token), timeout=30)


# ---------- TheBestTiket rooms ----------
class TestTiketArchive:
    def test_archive_flow(self, admin_token, db):
        name = _uniq("TK")
        r = requests.post(f"{API}/rooms", headers=_h(admin_token),
                          json={"name": name, "matchday": 1, "max_events": 5,
                                "game": "thebesttiket"}, timeout=30)
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        try:
            # gate — room is not settled
            r = requests.post(f"{API}/rooms/{rid}/archive?archived=true",
                              headers=_h(admin_token), timeout=30)
            assert r.status_code == 400, r.text
            assert "settled" in r.text.lower() or "concluse" in r.text.lower()

            _run(db.rooms.update_one({"id": rid}, {"$set": {"status": "settled"}}))

            r = requests.post(f"{API}/rooms/{rid}/archive?archived=true",
                              headers=_h(admin_token), timeout=30)
            assert r.status_code == 200, r.text
            assert r.json().get("archived") is True

            r = requests.post(f"{API}/rooms/{rid}/archive?archived=false",
                              headers=_h(admin_token), timeout=30)
            assert r.status_code == 200
            assert r.json().get("archived") is False
        finally:
            requests.delete(f"{API}/rooms/{rid}", headers=_h(admin_token), timeout=30)

    def test_player_cannot_archive(self, admin_token, player_token, db):
        name = _uniq("TKP")
        r = requests.post(f"{API}/rooms", headers=_h(admin_token),
                          json={"name": name, "matchday": 1, "max_events": 5,
                                "game": "thebesttiket"}, timeout=30)
        assert r.status_code == 200
        rid = r.json()["id"]
        try:
            _run(db.rooms.update_one({"id": rid}, {"$set": {"status": "settled"}}))
            r = requests.post(f"{API}/rooms/{rid}/archive?archived=true",
                              headers=_h(player_token), timeout=30)
            assert r.status_code in (401, 403)
        finally:
            requests.delete(f"{API}/rooms/{rid}", headers=_h(admin_token), timeout=30)


# ---------- FantaGiornata ----------
class TestFgArchive:
    def test_archive_flow(self, admin_token, db):
        name = _uniq("FG")
        r = requests.post(f"{API}/fg/leagues", headers=_h(admin_token),
                          json={"name": name}, timeout=30)
        assert r.status_code == 200, r.text
        lid = r.json()["id"]
        try:
            # gate — current_matchday is null
            r = requests.post(f"{API}/fg/leagues/{lid}/archive?archived=true",
                              headers=_h(admin_token), timeout=30)
            assert r.status_code == 400, r.text

            _run(db.fg_leagues.update_one({"id": lid}, {"$set": {"current_matchday": 1}}))

            r = requests.post(f"{API}/fg/leagues/{lid}/archive?archived=true",
                              headers=_h(admin_token), timeout=30)
            assert r.status_code == 200, r.text
            assert r.json().get("archived") is True

            r = requests.post(f"{API}/fg/leagues/{lid}/archive?archived=false",
                              headers=_h(admin_token), timeout=30)
            assert r.status_code == 200
            assert r.json().get("archived") is False
        finally:
            requests.delete(f"{API}/fg/leagues/{lid}", headers=_h(admin_token), timeout=30)

    def test_player_cannot_archive(self, admin_token, player_token, db):
        name = _uniq("FGP")
        r = requests.post(f"{API}/fg/leagues", headers=_h(admin_token),
                          json={"name": name}, timeout=30)
        assert r.status_code == 200
        lid = r.json()["id"]
        try:
            _run(db.fg_leagues.update_one({"id": lid}, {"$set": {"current_matchday": 1}}))
            r = requests.post(f"{API}/fg/leagues/{lid}/archive?archived=true",
                              headers=_h(player_token), timeout=30)
            assert r.status_code in (401, 403)
        finally:
            requests.delete(f"{API}/fg/leagues/{lid}", headers=_h(admin_token), timeout=30)
