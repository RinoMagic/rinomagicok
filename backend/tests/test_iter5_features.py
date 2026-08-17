"""RinoMagic — Iteration 5 backend E2E for the 3 new features.

Covers:
  1. Targeted Push (Notifiche Mirate) — /api/push/broadcast with user_ids filter.
  2. Storico ScoreAndLive — GET /api/sal/tournaments/{id}/history
  3. Storico Survival — GET /api/sv/tournaments/{tid}/matchdays + /summary
  4. Punteggi FantaGiornata — /fg/leagues/{id}/results/{matchday} + /settle + /leaderboard
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


@pytest.fixture(scope="module")
def s():
    return requests.Session()


@pytest.fixture(scope="module")
def admin_token(s):
    r = s.post(f"{BASE}/auth/admin/login", json={"email": ADMIN_EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def player_token(s):
    r = s.post(f"{BASE}/auth/player/login", json={"username": PLAYER_USERNAME, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def player_id(s, admin_token):
    r = s.get(f"{BASE}/auth/users", headers=_h(admin_token))
    assert r.status_code == 200
    for u in r.json():
        if u.get("username") == PLAYER_USERNAME:
            return u["id"]
    pytest.fail("qa player not found in /auth/users")


def _h(t):
    return {"Authorization": f"Bearer {t}"}


# ---------------- 1) Targeted push ----------------------------------------
class TestTargetedPush:
    def test_broadcast_targeted_qa_player(self, s, admin_token, player_id):
        r = s.post(f"{BASE}/push/broadcast", headers=_h(admin_token), json={
            "title": "TEST_PUSH_TARGETED",
            "body": f"iter5 {TS}",
            "user_ids": [player_id],
        })
        assert r.status_code == 200, r.text
        data = r.json()
        for k in ("sent", "expired_removed", "failed", "total_targeted"):
            assert k in data, f"missing {k} in {data}"
        # total_targeted is #of push_subscriptions matching filter — may be 0
        assert isinstance(data["total_targeted"], int)

    def test_broadcast_targeted_nonexistent_user_zero(self, s, admin_token):
        r = s.post(f"{BASE}/push/broadcast", headers=_h(admin_token), json={
            "title": "TEST_PUSH_NONE",
            "body": "no one",
            "user_ids": ["nonexistent-uid-xyz-12345"],
        })
        assert r.status_code == 200, r.text
        assert r.json()["total_targeted"] == 0

    def test_broadcast_non_admin_forbidden(self, s, player_token):
        r = s.post(f"{BASE}/push/broadcast", headers=_h(player_token), json={
            "title": "x", "body": "y",
        })
        assert r.status_code in (401, 403)

    def test_broadcast_without_user_ids_broadcasts_all(self, s, admin_token):
        # No filter → total_targeted == total push_subscriptions in DB (0+ ok)
        r = s.post(f"{BASE}/push/broadcast", headers=_h(admin_token), json={
            "title": "TEST_BROADCAST_ALL", "body": "all",
        })
        assert r.status_code == 200, r.text
        assert "total_targeted" in r.json()


# ---------------- 2) Storico ScoreAndLive --------------------------------
class TestScoreAndLiveHistory:
    def test_history_endpoint(self, s, admin_token, player_token):
        # Prefer existing tournament, else create fresh
        r = s.get(f"{BASE}/sal/tournaments", headers=_h(admin_token))
        assert r.status_code == 200
        tours = r.json()
        if tours:
            tid = tours[0]["id"]
        else:
            r2 = s.post(f"{BASE}/sal/tournaments", headers=_h(admin_token), json={
                "name": f"TEST_HIST_SAL_{TS}", "initial_lives": 3, "start_matchday": 1,
            })
            assert r2.status_code == 200, r2.text
            tid = r2.json()["id"]

        r = s.get(f"{BASE}/sal/tournaments/{tid}/history", headers=_h(player_token))
        assert r.status_code == 200, r.text
        data = r.json()
        assert "tournament" in data
        assert "matchdays" in data and isinstance(data["matchdays"], list)
        assert len(data["matchdays"]) >= 1
        for md in data["matchdays"]:
            for k in ("matchday_number", "status", "scorers", "picks_visible", "picks"):
                assert k in md, f"missing {k} in history matchday: {md}"
            assert isinstance(md["picks"], list)


# ---------------- 3) Storico Survival ------------------------------------
class TestSurvivalHistory:
    _tid = None
    _md_id = None

    def test_create_sv_and_list_matchdays(self, s, admin_token):
        r = s.post(f"{BASE}/sv/tournaments", headers=_h(admin_token), json={
            "name": f"TEST_HIST_SV_{TS}", "initial_lives": 3, "start_matchday": 1,
        })
        assert r.status_code == 200, r.text
        TestSurvivalHistory._tid = r.json()["id"]

        r2 = s.get(f"{BASE}/sv/tournaments/{TestSurvivalHistory._tid}/matchdays",
                   headers=_h(admin_token))
        assert r2.status_code == 200, r2.text
        arr = r2.json()
        assert isinstance(arr, list) and len(arr) >= 1
        first = arr[0]
        assert "id" in first
        assert "status" in first
        # matchday or matchday_number
        assert ("matchday" in first) or ("matchday_number" in first)
        TestSurvivalHistory._md_id = first["id"]

    def test_summary_by_id(self, s, player_token):
        assert TestSurvivalHistory._md_id
        r = s.get(
            f"{BASE}/sv/tournaments/{TestSurvivalHistory._tid}/matchdays/{TestSurvivalHistory._md_id}/summary",
            headers=_h(player_token),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "fixtures" in data and isinstance(data["fixtures"], list)
        for f in data["fixtures"]:
            assert "counts" in f
            assert set(f["counts"].keys()) == {"1", "X", "2"}


# ---------------- 4) FantaGiornata Punteggi ------------------------------
class TestFantaGiornataResults:
    _lid = None
    _md = None

    def test_create_league(self, s, admin_token):
        r = s.post(f"{BASE}/fg/leagues", headers=_h(admin_token),
                   json={"name": f"TEST_FG_PTS_{TS}"})
        assert r.status_code == 200, r.text
        TestFantaGiornataResults._lid = r.json()["id"]
        r2 = s.get(f"{BASE}/fg/leagues/{TestFantaGiornataResults._lid}",
                   headers=_h(admin_token))
        TestFantaGiornataResults._md = r2.json().get("current_matchday_number") or 1

    def test_results_empty(self, s, admin_token):
        r = s.get(
            f"{BASE}/fg/leagues/{TestFantaGiornataResults._lid}/results/{TestFantaGiornataResults._md}",
            headers=_h(admin_token))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["league_id"] == TestFantaGiornataResults._lid
        assert d["matchday"] == TestFantaGiornataResults._md
        assert isinstance(d["results"], list)

    def test_settle_no_voti_documents_behavior(self, s, admin_token):
        """Report behavior; may 400 (no voti) — that's EXPECTED per request."""
        r = s.post(
            f"{BASE}/fg/leagues/{TestFantaGiornataResults._lid}/settle",
            headers=_h(admin_token),
            json={"matchday": TestFantaGiornataResults._md},
        )
        # Accept either "no voti loaded" 400 or success (unlikely)
        assert r.status_code in (200, 400), r.text
        print(f"FG settle result -> {r.status_code}: {r.text[:200]}")

    def test_leaderboard(self, s, admin_token):
        r = s.get(f"{BASE}/fg/leagues/{TestFantaGiornataResults._lid}/leaderboard",
                  headers=_h(admin_token))
        assert r.status_code == 200, r.text
        d = r.json()
        rows = d if isinstance(d, list) else d.get("leaderboard")
        assert isinstance(rows, list)
