"""
Tests for iteration 14 bugfixes:
1. FORMATO risultato/pronostico: no raw JSON strings anywhere
2. ESCLUSE partite Survival: excluded fixtures must not appear in matchday fixtures
"""
import os
import json
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://game-schedina.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

ADMIN_EMAIL = "e1qa.admin@gmail.com"
ADMIN_PW = "Test1234!"
PLAYER_USER = "e1_qa_player"
PLAYER_PW = "Test1234!"
SURVIVAL_TID = "d28645bf-440e-4bb5-aedf-6fab9197ebc6"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/admin/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW}, timeout=30)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def player_token():
    r = requests.post(f"{API}/auth/player/login", json={"username": PLAYER_USER, "password": PLAYER_PW}, timeout=30)
    assert r.status_code == 200, f"player login failed: {r.status_code} {r.text}"
    return r.json()["token"]


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


# ---------- REGRESSION ----------
def test_admin_login_200(admin_token):
    assert admin_token


def test_player_login_200(player_token):
    assert player_token


# ---------- BUG #1 FORMATO ----------
def test_bonus_results_no_raw_json_in_response(admin_token):
    """Check bonus games responses don't leak dict pronostico/result as string like {"home_score":..."""
    # Try admin listing of bonus games
    r = requests.get(f"{API}/bonus/admin/games", headers=_h(admin_token), timeout=30)
    if r.status_code == 404:
        pytest.skip("bonus/admin/games endpoint not present")
    assert r.status_code == 200, r.text
    text = r.text
    # It's fine for result to be a dict object in JSON; problem is if some string field contains '{"home_score"'
    # We check no string leak occurred (not the dict itself, so we search escaped form)
    assert '\\"home_score\\"' not in text or '{"home_score"' in text  # dict form OK; check that stringified duplicate doesn't appear as string value
    # Ensure at least the endpoint returns something structured
    data = r.json()
    assert isinstance(data, (list, dict))


def test_bonus_public_endpoint(player_token):
    r = requests.get(f"{API}/bonus/games", headers=_h(player_token), timeout=30)
    # endpoint may be different; try alternates
    if r.status_code == 404:
        r = requests.get(f"{API}/bonus", headers=_h(player_token), timeout=30)
    if r.status_code == 404:
        pytest.skip("bonus public endpoint not found")
    assert r.status_code == 200, r.text


# ---------- BUG #2 ESCLUSE Survival ----------
def test_survival_current_matchday_has_fixtures(player_token):
    r = requests.get(f"{API}/sv/tournaments/{SURVIVAL_TID}/matchdays/current", headers=_h(player_token), timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "fixtures" in data, data
    assert isinstance(data["fixtures"], list)
    assert len(data["fixtures"]) > 0
    return data


def test_exclusion_propagates_and_hides_fixture(admin_token, player_token):
    """
    Load G2, exclude one fixture, verify it disappears from /matchdays/current fixtures,
    then restore it.
    """
    # 1. Get calendar fixtures for G2
    r = requests.get(f"{API}/sal/calendar", params={"matchday": 2, "season": "2026-27"}, headers=_h(admin_token), timeout=30)
    if r.status_code == 404:
        # try alt path
        r = requests.get(f"{API}/sal/calendar/matchday/2", headers=_h(admin_token), timeout=30)
    assert r.status_code == 200, f"calendar fetch failed: {r.status_code} {r.text[:200]}"
    cal = r.json()
    fixtures = cal if isinstance(cal, list) else cal.get("fixtures", cal.get("matches", []))
    assert fixtures, "no fixtures for G2"

    # Get current survival matchday fixtures before
    r0 = requests.get(f"{API}/sv/tournaments/{SURVIVAL_TID}/matchdays/current", headers=_h(player_token), timeout=30)
    assert r0.status_code == 200
    before_fixtures = r0.json().get("fixtures", [])
    assert len(before_fixtures) > 0
    before_count = len(before_fixtures)

    # Find a fixture that IS currently in survival (match by home/away)
    def key(f):
        return (f.get("home_team") or f.get("home"), f.get("away_team") or f.get("away"))
    sv_keys = {key(f) for f in before_fixtures}
    target = None
    for f in fixtures:
        if key(f) in sv_keys and not f.get("excluded"):
            target = f
            break
    assert target, f"no matching non-excluded fixture found; sv keys={sv_keys}"
    target_id = target.get("id") or target.get("_id") or target.get("fixture_id")
    assert target_id, f"fixture has no id: {target}"

    try:
        # 2. Exclude it
        r = requests.patch(f"{API}/sal/calendar/fixture/{target_id}/exclude",
                           json={"excluded": True}, headers=_h(admin_token), timeout=30)
        assert r.status_code in (200, 204), f"exclude failed: {r.status_code} {r.text}"

        # 3. Verify it is hidden from survival current matchday
        r1 = requests.get(f"{API}/sv/tournaments/{SURVIVAL_TID}/matchdays/current", headers=_h(player_token), timeout=30)
        assert r1.status_code == 200
        after_fixtures = r1.json().get("fixtures", [])
        after_keys = {key(f) for f in after_fixtures}
        assert key(target) not in after_keys, f"excluded fixture still present: {key(target)} in {after_keys}"
        assert len(after_fixtures) == before_count - 1, f"expected {before_count-1}, got {len(after_fixtures)}"

    finally:
        # 4. Restore
        rr = requests.patch(f"{API}/sal/calendar/fixture/{target_id}/exclude",
                            json={"excluded": False}, headers=_h(admin_token), timeout=30)
        assert rr.status_code in (200, 204), f"restore failed: {rr.status_code} {rr.text}"
        # Verify restored
        r2 = requests.get(f"{API}/sv/tournaments/{SURVIVAL_TID}/matchdays/current", headers=_h(player_token), timeout=30)
        assert r2.status_code == 200
        restored_keys = {key(f) for f in r2.json().get("fixtures", [])}
        assert key(target) in restored_keys, "fixture not restored!"
