"""RinoMagic (Schedina Bar) — backend E2E for the ORIGINAL ported backend.

Covers:
  - Auth: admin (email) + player (username) login, /auth/me, wrong pw 401
  - Hub /api/games: 4 games, thebesttiket+surviva enabled, others disabled
  - Survival (/api/sv): create → auto-enroll admin → invite create → player
    join → current matchday → locked-teams → submit picks (correct count &
    wrong count 400) → participants
  - Tiket (/api/rooms): create → list → join → detail → leaderboard →
    members
"""
import os
import time
import pytest
import requests

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") + "/api"

ADMIN_EMAIL = "e1qa.admin@gmail.com"
PLAYER_USERNAME = "e1_qa_player"
PASSWORD = "Test1234!"

TS = int(time.time())


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------
@pytest.fixture(scope="module")
def s():
    return requests.Session()


@pytest.fixture(scope="module")
def admin_token(s):
    r = s.post(f"{BASE}/auth/admin/login",
               json={"email": ADMIN_EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def player_token(s):
    r = s.post(f"{BASE}/auth/player/login",
               json={"username": PLAYER_USERNAME, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


# -------------------------------------------------------------------------
# Auth
# -------------------------------------------------------------------------
class TestAuth:
    def test_admin_login(self, s):
        r = s.post(f"{BASE}/auth/admin/login",
                   json={"email": ADMIN_EMAIL, "password": PASSWORD})
        assert r.status_code == 200
        data = r.json()
        assert "token" in data and isinstance(data["token"], str)
        assert data["user"]["role"] == "admin"
        assert data["user"]["email"] == ADMIN_EMAIL

    def test_player_login(self, s):
        r = s.post(f"{BASE}/auth/player/login",
                   json={"username": PLAYER_USERNAME, "password": PASSWORD})
        assert r.status_code == 200
        data = r.json()
        assert data["user"]["role"] == "player"
        assert data["user"]["username"] == PLAYER_USERNAME

    def test_auth_me_admin(self, s, admin_token):
        r = s.get(f"{BASE}/auth/me", headers=_h(admin_token))
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL

    def test_auth_me_player(self, s, player_token):
        r = s.get(f"{BASE}/auth/me", headers=_h(player_token))
        assert r.status_code == 200
        assert r.json()["username"] == PLAYER_USERNAME

    def test_admin_wrong_password(self, s):
        r = s.post(f"{BASE}/auth/admin/login",
                   json={"email": ADMIN_EMAIL, "password": "WrongPass!!"})
        assert r.status_code == 401

    def test_player_wrong_password(self, s):
        r = s.post(f"{BASE}/auth/player/login",
                   json={"username": PLAYER_USERNAME, "password": "nope"})
        assert r.status_code == 401

    def test_me_requires_token(self, s):
        r = s.get(f"{BASE}/auth/me")
        assert r.status_code == 401


# -------------------------------------------------------------------------
# Hub /api/games
# -------------------------------------------------------------------------
class TestGames:
    def test_list_games_shape(self, s, player_token):
        r = s.get(f"{BASE}/games", headers=_h(player_token))
        assert r.status_code == 200
        games = r.json()
        assert isinstance(games, list) and len(games) == 4
        by_id = {g["id"]: g for g in games}
        assert set(by_id.keys()) == {
            "thebesttiket", "scoreandlive", "fantagiornata", "surviva",
        }
        assert by_id["thebesttiket"]["enabled"] is True
        assert by_id["surviva"]["enabled"] is True
        assert by_id["scoreandlive"]["enabled"] is False
        assert by_id["fantagiornata"]["enabled"] is False


# -------------------------------------------------------------------------
# Survival full flow
# -------------------------------------------------------------------------
class TestSurvival:
    _t_id = None
    _invite_code = None
    _md = None
    _md_id = None

    def test_create_tournament_auto_enroll_admin(self, s, admin_token):
        r = s.post(f"{BASE}/sv/tournaments", headers=_h(admin_token), json={
            "name": f"TEST_SV_{TS}",
            "initial_lives": 3,
            "start_matchday": 1,
        })
        assert r.status_code == 200, r.text
        t = r.json()
        assert t["initial_lives"] == 3
        assert t.get("invite_code")
        # admin auto-enrolled
        assert t.get("joined") is True
        TestSurvival._t_id = t["id"]
        TestSurvival._invite_code = t["invite_code"]

    def test_get_tournament(self, s, admin_token):
        r = s.get(f"{BASE}/sv/tournaments/{TestSurvival._t_id}",
                  headers=_h(admin_token))
        assert r.status_code == 200
        assert r.json()["id"] == TestSurvival._t_id

    def test_current_matchday(self, s, admin_token):
        r = s.get(f"{BASE}/sv/tournaments/{TestSurvival._t_id}/matchdays/current",
                  headers=_h(admin_token))
        assert r.status_code == 200
        md = r.json()
        assert md.get("fixtures") and len(md["fixtures"]) >= 3
        assert md.get("id")
        TestSurvival._md = md
        TestSurvival._md_id = md["id"]

    def test_player_join_via_invite(self, s, player_token):
        # If player is already enrolled from a previous run, the join is
        # idempotent (200 back). If invite was already used by them, still ok.
        r = s.post(f"{BASE}/sv/tournaments/join", headers=_h(player_token),
                   json={"invite_code": TestSurvival._invite_code})
        # 200 = ok / already joined; 410 accepted only if invite got consumed
        # by another test — should not happen since we generate fresh code.
        assert r.status_code == 200, r.text
        assert r.json()["id"] == TestSurvival._t_id

    def test_locked_teams_initial(self, s, player_token):
        r = s.get(
            f"{BASE}/sv/tournaments/{TestSurvival._t_id}/locked-teams",
            headers=_h(player_token),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["lives_left"] == 3
        assert data["locked_teams"] == []

    def test_submit_wrong_count_400(self, s, player_token):
        # Only 2 picks when 3 required
        fixtures = TestSurvival._md["fixtures"]
        picks = [{"home_team": f["home_team"], "away_team": f["away_team"],
                  "pick": "1"} for f in fixtures[:2]]
        r = s.post(
            f"{BASE}/sv/tournaments/{TestSurvival._t_id}"
            f"/matchdays/{TestSurvival._md_id}/picks",
            headers=_h(player_token), json={"picks": picks},
        )
        assert r.status_code == 400

    def test_submit_picks_ok(self, s, player_token):
        fixtures = TestSurvival._md["fixtures"]
        picks = []
        for i, f in enumerate(fixtures[:3]):
            picks.append({
                "home_team": f["home_team"],
                "away_team": f["away_team"],
                "pick": ["1", "X", "2"][i],
            })
        r = s.post(
            f"{BASE}/sv/tournaments/{TestSurvival._t_id}"
            f"/matchdays/{TestSurvival._md_id}/picks",
            headers=_h(player_token), json={"picks": picks},
        )
        assert r.status_code == 200, r.text

    def test_participants_lists_players(self, s, admin_token):
        r = s.get(
            f"{BASE}/sv/tournaments/{TestSurvival._t_id}/participants",
            headers=_h(admin_token),
        )
        assert r.status_code == 200
        parts = r.json()
        assert isinstance(parts, list) and len(parts) >= 2
        # every participant has lives info
        for p in parts:
            assert "lives_left" in p or "lives" in p


# -------------------------------------------------------------------------
# Tiket rooms full flow
# -------------------------------------------------------------------------
class TestTiket:
    _room_id = None
    _invite = None

    def test_create_room(self, s, admin_token):
        r = s.post(f"{BASE}/rooms", headers=_h(admin_token), json={
            "name": f"TEST_TIK_{TS}",
            "matchday": 1,
            "max_events": 5,
            "game": "thebesttiket",
        })
        assert r.status_code == 200, r.text
        room = r.json()
        assert room["name"].startswith("TEST_TIK_")
        assert room["game"] == "thebesttiket"
        assert room.get("invite_code")
        TestTiket._room_id = room["id"]
        TestTiket._invite = room["invite_code"]

    def test_list_rooms_filter_game(self, s, admin_token):
        r = s.get(f"{BASE}/rooms?game=thebesttiket", headers=_h(admin_token))
        assert r.status_code == 200
        rooms = r.json()
        assert any(rm["id"] == TestTiket._room_id for rm in rooms)

    def test_player_joins_room(self, s, player_token):
        r = s.post(f"{BASE}/rooms/join", headers=_h(player_token),
                   json={"invite_code": TestTiket._invite})
        assert r.status_code == 200, r.text
        assert r.json()["id"] == TestTiket._room_id

    def test_get_room(self, s, player_token):
        r = s.get(f"{BASE}/rooms/{TestTiket._room_id}",
                  headers=_h(player_token))
        assert r.status_code == 200
        assert r.json()["id"] == TestTiket._room_id

    def test_leaderboard(self, s, player_token):
        r = s.get(f"{BASE}/rooms/{TestTiket._room_id}/leaderboard",
                  headers=_h(player_token))
        assert r.status_code == 200
        # accept list or dict wrapper
        assert isinstance(r.json(), (list, dict))

    def test_members(self, s, player_token):
        r = s.get(f"{BASE}/rooms/{TestTiket._room_id}/members",
                  headers=_h(player_token))
        assert r.status_code == 200
        members = r.json()
        assert isinstance(members, list) and len(members) >= 2


# -------------------------------------------------------------------------
# PWA static assets
# -------------------------------------------------------------------------
class TestPWA:
    ROOT = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")

    def test_manifest(self, s):
        r = s.get(f"{self.ROOT}/manifest.json")
        assert r.status_code == 200
        m = r.json()
        assert m.get("name") or m.get("short_name")

    def test_sw(self, s):
        r = s.get(f"{self.ROOT}/sw.js")
        assert r.status_code == 200
        assert "self" in r.text or "service" in r.text.lower()

    def test_vapid(self, s, player_token):
        r = s.get(f"{BASE}/push/vapid-public-key", headers=_h(player_token))
        assert r.status_code == 200
        assert r.json().get("key") or r.json().get("public_key") or r.json()
