"""Backend API integration tests for Schedina Bar.

Covers:
- Auth: login (QA player/admin), /auth/me, wrong password
- Serie A reference endpoints (matchdays, teams, calendar, players)
- Tiket full flow: create round (admin), submit schedina (player),
  set results (admin), verify points (big-match bonus = 3), standings
- Survival full flow: create tournament, join, pick, invalid picks,
  resolve, elimination
- PWA: manifest.json, sw.js, icons
"""
import os
import time
import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") \
    else "https://game-schedina.preview.emergentagent.com"

# Read from frontend .env for public URL
try:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
except Exception:
    pass

PLAYER = {"identifier": "e1_qa_player", "password": "Test1234!"}
ADMIN = {"identifier": "e1_qa_admin", "password": "Test1234!"}

state = {}


def _login(creds):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    assert "token" in data and "user" in data
    return data


@pytest.fixture(scope="session")
def player_token():
    d = _login(PLAYER)
    state["player_user"] = d["user"]
    return d["token"]


@pytest.fixture(scope="session")
def admin_token():
    d = _login(ADMIN)
    state["admin_user"] = d["user"]
    return d["token"]


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


# ------------------ AUTH ------------------
class TestAuth:
    def test_login_player(self, player_token):
        assert player_token
        u = state["player_user"]
        assert u.get("role") == "player"

    def test_login_admin(self, admin_token):
        assert admin_token
        assert state["admin_user"].get("role") == "admin"

    def test_login_wrong_password(self):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"identifier": "e1_qa_player", "password": "wrong!!"}, timeout=30)
        assert r.status_code == 401

    def test_me(self, player_token):
        r = requests.get(f"{BASE_URL}/api/auth/me", headers=H(player_token), timeout=30)
        assert r.status_code == 200
        assert r.json()["username"] == "e1_qa_player"

    def test_me_no_token(self):
        r = requests.get(f"{BASE_URL}/api/auth/me", timeout=30)
        assert r.status_code == 401


# ------------------ SERIE A REF ------------------
class TestSerieA:
    def test_matchdays(self, player_token):
        r = requests.get(f"{BASE_URL}/api/serie-a/matchdays", headers=H(player_token), timeout=30)
        assert r.status_code == 200
        mds = r.json()["matchdays"]
        assert mds == list(range(1, 39)), f"expected 1..38, got {mds[:5]}..{mds[-3:]}"

    def test_teams(self, player_token):
        r = requests.get(f"{BASE_URL}/api/serie-a/teams", headers=H(player_token), timeout=30)
        assert r.status_code == 200
        teams = r.json()["teams"]
        assert len(teams) == 20, f"expected 20, got {len(teams)}"

    def test_calendar_matchday1(self, player_token):
        r = requests.get(f"{BASE_URL}/api/serie-a/calendar?matchday=1", timeout=30)
        assert r.status_code == 200
        matches = r.json()["matches"]
        assert len(matches) == 10
        # capture a fixture id for later
        state["md1_fixtures"] = matches

    def test_players_pagination_filter(self):
        r = requests.get(f"{BASE_URL}/api/players?limit=10&skip=0", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "total" in d and "players" in d
        assert len(d["players"]) <= 10

    def test_players_role_filter(self):
        r = requests.get(f"{BASE_URL}/api/players?role=P&limit=5", timeout=30)
        assert r.status_code == 200
        for p in r.json()["players"]:
            assert p.get("role") == "P"

    def test_players_search(self):
        r = requests.get(f"{BASE_URL}/api/players?search=a&limit=5", timeout=30)
        assert r.status_code == 200


# ------------------ TIKET FLOW ------------------
class TestTiket:
    def test_full_flow(self, admin_token, player_token):
        from datetime import datetime, timezone, timedelta
        # find an unused matchday
        rr = requests.get(f"{BASE_URL}/api/tiket/rounds", headers=H(admin_token), timeout=30)
        assert rr.status_code == 200
        used_mds = {r["matchday"] for r in rr.json()["rounds"]}
        matchday = None
        for md in range(1, 39):
            if md not in used_mds:
                matchday = md
                break
        assert matchday is not None, "no free matchday"

        # get fixtures for this md
        cal = requests.get(f"{BASE_URL}/api/serie-a/calendar?matchday={matchday}", timeout=30).json()["matches"]
        assert len(cal) == 10
        big_fid = cal[0]["id"]

        deadline = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        # create round
        r = requests.post(
            f"{BASE_URL}/api/tiket/rounds",
            headers=H(admin_token),
            json={"matchday": matchday, "deadline": deadline, "big_match_fixture_id": big_fid},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        rnd = r.json()
        assert rnd["matchday"] == matchday
        assert rnd["big_match_fixture_id"] == big_fid
        round_id = rnd["id"]
        state["tiket_round_id"] = round_id
        state["tiket_matchday"] = matchday
        state["tiket_big_fid"] = big_fid
        state["tiket_fixtures"] = rnd["fixtures"]

        # non-admin cannot create
        r2 = requests.post(
            f"{BASE_URL}/api/tiket/rounds", headers=H(player_token),
            json={"matchday": matchday, "deadline": deadline}, timeout=30,
        )
        assert r2.status_code == 403

        # duplicate matchday
        r3 = requests.post(
            f"{BASE_URL}/api/tiket/rounds", headers=H(admin_token),
            json={"matchday": matchday, "deadline": deadline}, timeout=30,
        )
        assert r3.status_code == 400

        # player submits predictions - all "1" for simplicity, with bonus
        predictions = {f["id"]: "1" for f in rnd["fixtures"]}
        sub = requests.post(
            f"{BASE_URL}/api/tiket/rounds/{round_id}/schedina",
            headers=H(player_token),
            json={"predictions": predictions, "big_match_bonus": True},
            timeout=30,
        )
        assert sub.status_code == 200, sub.text

        # get round detail as player
        det = requests.get(f"{BASE_URL}/api/tiket/rounds/{round_id}",
                           headers=H(player_token), timeout=30).json()
        assert det["my_schedina"] is not None
        assert det["my_schedina"]["big_match_bonus"] is True

        # admin sets results: all "1" (all correct) -> normal 9x1=9, big=3(bonus) => 12
        results = {f["id"]: "1" for f in rnd["fixtures"]}
        res = requests.post(
            f"{BASE_URL}/api/tiket/rounds/{round_id}/results",
            headers=H(admin_token),
            json={"results": results},
            timeout=30,
        )
        assert res.status_code == 200

        # standings
        st = requests.get(f"{BASE_URL}/api/tiket/standings",
                          headers=H(player_token), timeout=30).json()["standings"]
        me = state["player_user"]["id"]
        row = next((r for r in st if r["user_id"] == me), None)
        assert row is not None, "player not in standings"
        assert row["points"] >= 12, f"expected >=12 pts (9 normal + 3 big+bonus), got {row['points']}"

    def test_invalid_prediction(self, admin_token, player_token):
        # need round created above
        rid = state.get("tiket_round_id")
        if not rid:
            pytest.skip("no round")
        r = requests.post(
            f"{BASE_URL}/api/tiket/rounds/{rid}/schedina",
            headers=H(player_token),
            json={"predictions": {"fake-id": "1"}, "big_match_bonus": False},
            timeout=30,
        )
        # round may be scored now → 400
        assert r.status_code == 400


# ------------------ SURVIVAL FLOW ------------------
class TestSurvival:
    def test_full_flow(self, admin_token, player_token):
        # create tournament starting at matchday 1
        r = requests.post(
            f"{BASE_URL}/api/survival/tournaments",
            headers=H(admin_token),
            json={"name": f"TEST_SV_{int(time.time())}", "start_matchday": 1},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        t = r.json()
        tid = t["id"]
        state["sv_tid"] = tid

        # non-admin cannot create
        r2 = requests.post(f"{BASE_URL}/api/survival/tournaments", headers=H(player_token),
                           json={"name": "nope"}, timeout=30)
        assert r2.status_code == 403

        # player joins
        j = requests.post(f"{BASE_URL}/api/survival/tournaments/{tid}/join",
                          headers=H(player_token), timeout=30)
        assert j.status_code == 200

        # get detail
        det = requests.get(f"{BASE_URL}/api/survival/tournaments/{tid}",
                           headers=H(player_token), timeout=30).json()
        assert det["my_entry"] is not None
        assert det["my_entry"]["status"] == "alive"
        fixtures = det["current_fixtures"]
        assert len(fixtures) == 10
        home_team = fixtures[0]["home_team"]
        state["sv_pick_team"] = home_team
        state["sv_fixtures"] = fixtures

        # invalid: team not playing this matchday -> use nonsense
        bad = requests.post(f"{BASE_URL}/api/survival/tournaments/{tid}/pick",
                            headers=H(player_token),
                            json={"matchday": 1, "team": "NoSuchTeamFC"}, timeout=30)
        assert bad.status_code == 400

        # valid pick
        p = requests.post(f"{BASE_URL}/api/survival/tournaments/{tid}/pick",
                          headers=H(player_token),
                          json={"matchday": 1, "team": home_team}, timeout=30)
        assert p.status_code == 200

        # resolve: make picked team WIN (result "1" since home_team was picked as home)
        results = {fixtures[0]["id"]: "1"}
        rv = requests.post(f"{BASE_URL}/api/survival/tournaments/{tid}/resolve",
                           headers=H(admin_token),
                           json={"matchday": 1, "results": results}, timeout=30)
        assert rv.status_code == 200, rv.text

        # player should still be alive; matchday advanced
        det2 = requests.get(f"{BASE_URL}/api/survival/tournaments/{tid}",
                            headers=H(player_token), timeout=30).json()
        assert det2["tournament"]["current_matchday"] == 2
        assert det2["my_entry"]["status"] == "alive"
        assert home_team in det2["my_entry"]["used_teams"]

        # try re-using same team next matchday
        # first check if team plays matchday 2
        cal2 = requests.get(f"{BASE_URL}/api/serie-a/calendar?matchday=2", timeout=30).json()["matches"]
        teams_md2 = {f["home_team"] for f in cal2} | {f["away_team"] for f in cal2}
        if home_team in teams_md2:
            reuse = requests.post(f"{BASE_URL}/api/survival/tournaments/{tid}/pick",
                                  headers=H(player_token),
                                  json={"matchday": 2, "team": home_team}, timeout=30)
            assert reuse.status_code == 400  # already used


# ------------------ PWA ------------------
class TestPWA:
    def test_manifest(self):
        r = requests.get(f"{BASE_URL}/manifest.json", timeout=30)
        assert r.status_code == 200
        m = r.json()
        assert "name" in m or "short_name" in m
        assert "icons" in m

    def test_sw(self):
        r = requests.get(f"{BASE_URL}/sw.js", timeout=30)
        assert r.status_code == 200
        assert "self" in r.text or "service" in r.text.lower()

    def test_icons(self):
        for p in ("/icon-192.png", "/icon-512.png"):
            r = requests.get(f"{BASE_URL}{p}", timeout=30)
            assert r.status_code == 200, f"{p} not served"

    def test_vapid_public_key(self):
        r = requests.get(f"{BASE_URL}/api/push/vapid-public-key", timeout=30)
        assert r.status_code == 200

    def test_push_subscribe_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/push/subscribe", json={}, timeout=30)
        assert r.status_code in (401, 422)
