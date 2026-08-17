"""RinoMagic — Iteration 3 backend E2E for the 4 new features.

Covers:
  - /api/games: scoreandlive now enabled=True (was False in iter 2)
  - Schedina OCR (Tiket): create room → player join → OCR endpoint reachable
    (may 400 on bad image or return 0 events — both acceptable, only auth+route)
  - Admin panel: deadlines PUT/GET, push/broadcast, voti PDF/XLSX (auth check
    only — malformed file 400 acceptable), settle state + commit reachable.
  - ScoreAndLive: full E2E flow (create → auto-enrolment → by-code preview →
    join → detail with participants+matchdays+invite → matchday detail with
    fixtures/expected_picks_count/my_lives_remaining → players by team → submit
    picks).
  - Bonus: /bonus/available for survival returns eligible+config+subscriptions;
    submit /bonus/picks/exact for a subscription the user owns.
"""
import base64
import io
import os
import time
import pytest
import requests

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") + "/api"

ADMIN_EMAIL = "e1qa.admin@gmail.com"
PLAYER_USERNAME = "e1_qa_player"
PASSWORD = "Test1234!"
TS = int(time.time())


# --- fixtures ---------------------------------------------------------------
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
def player_id(s, player_token):
    r = s.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {player_token}"})
    return r.json()["id"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


# --- /api/games (regression: scoreandlive now enabled) ---------------------
class TestGamesEnabledFlags:
    def test_scoreandlive_enabled(self, s, player_token):
        r = s.get(f"{BASE}/games", headers=_h(player_token))
        assert r.status_code == 200
        by_id = {g["id"]: g for g in r.json()}
        assert by_id["scoreandlive"]["enabled"] is True
        assert by_id["thebesttiket"]["enabled"] is True
        assert by_id["surviva"]["enabled"] is True
        assert by_id["fantagiornata"]["enabled"] is False


# --- Schedina OCR ----------------------------------------------------------
class TestSchedinaOCR:
    _room = None
    _invite = None

    def test_create_room_and_join(self, s, admin_token, player_token):
        r = s.post(f"{BASE}/rooms", headers=_h(admin_token), json={
            "name": f"TEST_OCR_{TS}", "matchday": 1, "max_events": 5,
            "game": "thebesttiket",
        })
        assert r.status_code == 200, r.text
        room = r.json()
        TestSchedinaOCR._room = room["id"]
        TestSchedinaOCR._invite = room["invite_code"]
        # player joins
        rj = s.post(f"{BASE}/rooms/join", headers=_h(player_token),
                    json={"invite_code": TestSchedinaOCR._invite})
        assert rj.status_code == 200, rj.text

    def test_ocr_endpoint_reachable(self, s, player_token):
        # Build a valid solid-black 32x32 PNG using Pillow so PIL doesn't
        # choke in the Tesseract fallback path. Vision LLM will return 0
        # events, then the STARYES color anti-cheat rejects it with 400.
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (0, 0, 0)).save(buf, format="PNG")
        tiny_png = base64.b64encode(buf.getvalue()).decode()
        r = s.post(
            f"{BASE}/rooms/{TestSchedinaOCR._room}/schedina/ocr",
            headers=_h(player_token),
            json={"image_base64": tiny_png},
            timeout=60,
        )
        # Accept: 200 (unlikely with 1x1), or 400 with "staryes"/"base64"/OCR-err
        assert r.status_code in (200, 400), f"unexpected {r.status_code}: {r.text}"

    def test_get_my_schedina(self, s, player_token):
        r = s.get(f"{BASE}/rooms/{TestSchedinaOCR._room}/schedina",
                  headers=_h(player_token))
        # 200 with events (may be empty) OR 404 if never uploaded.
        assert r.status_code in (200, 404), r.text


# --- Admin panel -----------------------------------------------------------
class TestAdminPanel:
    def test_deadlines_put_current(self, s, admin_token):
        # PUT a fake deadline in the future for matchday 38 (unused season slot)
        iso = "2099-12-31T22:00:00+00:00"
        r = s.put(f"{BASE}/deadlines/38", headers=_h(admin_token),
                  json={"deadline_at": iso})
        assert r.status_code == 200, r.text
        # GET current deadline (won't be 38 but endpoint should respond 200)
        r2 = s.get(f"{BASE}/deadlines/current", headers=_h(admin_token))
        assert r2.status_code == 200
        assert isinstance(r2.json(), dict)

    def test_deadlines_requires_admin(self, s, player_token):
        r = s.put(f"{BASE}/deadlines/38", headers=_h(player_token),
                  json={"deadline_at": None})
        assert r.status_code in (401, 403)

    def test_push_broadcast(self, s, admin_token):
        r = s.post(f"{BASE}/push/broadcast", headers=_h(admin_token),
                   json={"title": f"TEST_BC_{TS}", "body": "test", "url": "/"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert "sent" in data

    def test_broadcast_requires_admin(self, s, player_token):
        r = s.post(f"{BASE}/push/broadcast", headers=_h(player_token),
                   json={"title": "x", "body": "y"})
        assert r.status_code in (401, 403)

    def test_voti_upload_pdf_auth(self, s, admin_token):
        # Send a bogus file to verify route exists & permission ok — a 400
        # (invalid PDF) is acceptable, 401/403/404/500 is not.
        files = {"file": ("dummy.pdf", b"not a pdf", "application/pdf")}
        r = s.post(f"{BASE}/admin/voti/upload-pdf", headers=_h(admin_token),
                   files=files, data={"matchday": 1, "season": "2026-27"})
        assert r.status_code in (200, 400, 422), r.text

    def test_voti_upload_xlsx_auth(self, s, admin_token):
        files = {"file": ("dummy.xlsx", b"not a xlsx",
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        r = s.post(f"{BASE}/admin/voti/upload-xlsx", headers=_h(admin_token),
                   files=files, data={"matchday": 1, "season": "2026-27"})
        assert r.status_code in (200, 400, 422), r.text

    def test_settle_state(self, s, admin_token):
        r = s.get(f"{BASE}/admin/settle-matchday/state?matchday=1&season=2026-27",
                  headers=_h(admin_token))
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), dict)

    def test_settle_commit_reachable(self, s, admin_token):
        # We only verify route reachable + permission. Missing voti is
        # acceptable → 400. 200 also OK. 401/403/404/500 not OK.
        r = s.post(f"{BASE}/admin/settle-matchday/commit", headers=_h(admin_token),
                   json={"matchday": 1, "season": "2026-27"})
        assert r.status_code in (200, 400, 409), r.text


# --- ScoreAndLive full flow ------------------------------------------------
class TestScoreAndLive:
    _tid = None
    _code = None
    _md = None
    _md_id = None

    def test_create_tournament_auto_enroll_admin(self, s, admin_token):
        r = s.post(f"{BASE}/sal/tournaments", headers=_h(admin_token), json={
            "name": f"TEST_SAL_{TS}", "initial_lives": 3, "start_matchday": 1,
        })
        assert r.status_code == 200, r.text
        t = r.json()
        assert t.get("initial_lives") == 3
        assert t.get("invite_code")
        TestScoreAndLive._tid = t["id"]
        TestScoreAndLive._code = t["invite_code"]

    def test_by_code_preview(self, s, player_token):
        r = s.get(f"{BASE}/sal/tournaments/by-code/{TestScoreAndLive._code}",
                  headers=_h(player_token))
        assert r.status_code == 200, r.text
        assert r.json()["id"] == TestScoreAndLive._tid

    def test_player_joins(self, s, player_token):
        r = s.post(f"{BASE}/sal/tournaments/{TestScoreAndLive._tid}/join",
                   headers=_h(player_token),
                   json={"invite_code": TestScoreAndLive._code})
        assert r.status_code == 200, r.text

    def test_get_tournament_detail(self, s, player_token):
        r = s.get(f"{BASE}/sal/tournaments/{TestScoreAndLive._tid}",
                  headers=_h(player_token))
        assert r.status_code == 200, r.text
        t = r.json()
        assert "participants" in t
        assert "matchdays" in t
        # invite_code visible from _tournament_dict
        assert t.get("invite_code")
        # Find open matchday
        open_md = [m for m in t["matchdays"] if m["status"] == "open"]
        assert open_md, "no open matchday returned"
        TestScoreAndLive._md_id = open_md[0]["id"]

    def test_matchday_detail(self, s, player_token):
        r = s.get(
            f"{BASE}/sal/tournaments/{TestScoreAndLive._tid}"
            f"/matchdays/{TestScoreAndLive._md_id}",
            headers=_h(player_token),
        )
        assert r.status_code == 200, r.text
        md = r.json()
        assert md.get("fixtures")
        for f in md["fixtures"]:
            assert "idx" in f and "home_team" in f and "away_team" in f
        assert "expected_picks_count" in md
        assert "my_lives_remaining" in md
        assert md["my_lives_remaining"] == 3
        TestScoreAndLive._md = md

    def test_players_by_team(self, s, player_token):
        # pick the first fixture's home team
        team = TestScoreAndLive._md["fixtures"][0]["home_team"]
        r = s.get(f"{BASE}/sal/players?team={team}", headers=_h(player_token))
        assert r.status_code == 200, r.text
        players = r.json()
        assert isinstance(players, list)
        # Team may have 0 players in seeded db, but endpoint must work.

    def test_submit_picks(self, s, player_token):
        md = TestScoreAndLive._md
        needed = md["expected_picks_count"]
        playable = [f for f in md["fixtures"] if not f.get("postponed_before")]
        # For each of the first `needed` playable fixtures, look up any player
        # from either team; skip if none available.
        picks = []
        used_fx = set()
        for f in playable:
            if len(picks) >= needed:
                break
            for team in (f["home_team"], f["away_team"]):
                r = s.get(f"{BASE}/sal/players?team={team}&limit=1",
                          headers=_h(player_token))
                if r.status_code != 200:
                    continue
                pls = r.json()
                if pls:
                    picks.append({"fixture_idx": f["idx"], "player_id": pls[0]["id"]})
                    used_fx.add(f["idx"])
                    break
        if len(picks) < needed:
            pytest.skip(
                f"seed roster too thin to build {needed} picks (got {len(picks)})"
            )
        r = s.post(
            f"{BASE}/sal/tournaments/{TestScoreAndLive._tid}"
            f"/matchdays/{TestScoreAndLive._md_id}/picks",
            headers=_h(player_token), json={"picks": picks},
        )
        assert r.status_code == 200, r.text


# --- Bonus -----------------------------------------------------------------
class TestBonus:
    def test_available_survival(self, s, player_token):
        r = s.get(f"{BASE}/bonus/available?game=survival&season=2026-27",
                  headers=_h(player_token))
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["game"] == "survival"
        assert data["bonus_type"] == "exact_score"
        assert "eligible" in data
        assert "subscriptions" in data
        assert isinstance(data["subscriptions"], list)

    def test_available_score_first_scorer(self, s, player_token):
        r = s.get(f"{BASE}/bonus/available?game=score&season=2026-27",
                  headers=_h(player_token))
        assert r.status_code == 200
        assert r.json()["bonus_type"] == "first_scorer"

    def test_eligibility_endpoint(self, s, player_token):
        r = s.get(f"{BASE}/bonus/eligibility", headers=_h(player_token))
        assert r.status_code == 200
        data = r.json()
        for g in ("tiket", "survival", "score", "fanta"):
            assert g in data
            assert "eligible" in data[g] and "subscriptions" in data[g]

    def test_submit_exact_survival(self, s, player_token):
        # We need: player subscribed to a survival tournament + an open
        # exact_score config. Both are true (iter2 already joined a TEST_SV_*
        # tournament; ensure_bonus_draft auto-creates the draft).
        avail = s.get(
            f"{BASE}/bonus/available?game=survival&season=2026-27",
            headers=_h(player_token),
        ).json()
        if not avail.get("eligible"):
            pytest.skip("player not subscribed to any survival tournament")
        if not avail.get("config"):
            pytest.skip("no open exact_score bonus draft available")
        subs = avail["subscriptions"]
        if not subs:
            pytest.skip("no subscriptions for survival bonus")
        sid = subs[0]["id"]
        r = s.post(f"{BASE}/bonus/picks/exact", headers=_h(player_token), json={
            "game": "survival", "season": "2026-27",
            "subscription_id": sid, "home_score": 1, "away_score": 1,
        })
        assert r.status_code in (200, 400), r.text
        # 400 acceptable if config is locked past deadline; 200 is the happy path.
        if r.status_code == 200:
            data = r.json()
            assert data.get("pick", {}).get("home_score") == 1
            assert data.get("pick", {}).get("away_score") == 1

    def test_submit_exact_wrong_subscription(self, s, player_token):
        # Bogus subscription id → 403 (not in subscription).
        r = s.post(f"{BASE}/bonus/picks/exact", headers=_h(player_token), json={
            "game": "survival", "season": "2026-27",
            "subscription_id": "does-not-exist",
            "home_score": 0, "away_score": 0,
        })
        assert r.status_code in (403, 404), r.text
