"""Iteration 6 tests: Import Voti Guidato (dry_run), Esporta Storico PDF,
Notifiche Automatiche (background loop health + deadline endpoints)."""
import os
import io
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    pytest.skip("REACT_APP_BACKEND_URL not set", allow_module_level=True)

ADMIN_EMAIL = "e1qa.admin@gmail.com"
ADMIN_PASS = "Test1234!"
PLAYER_USER = "e1_qa_player"
PLAYER_PASS = "Test1234!"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/admin/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def player_token():
    r = requests.post(f"{BASE_URL}/api/auth/player/login",
                      json={"username": PLAYER_USER, "password": PLAYER_PASS}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def player_headers(player_token):
    return {"Authorization": f"Bearer {player_token}"}


# ---------------------------------------------------------------------------
# 1) Backend health / auto-notify loop startup
# ---------------------------------------------------------------------------
class TestHealthAndAutoNotify:
    def test_root_ok(self):
        r = requests.get(f"{BASE_URL}/api/", timeout=10)
        assert r.status_code == 200
        assert r.json().get("status") == "ok"

    def test_admin_me(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/auth/me", headers=admin_headers, timeout=10)
        assert r.status_code == 200
        assert r.json().get("role") == "admin"

    def test_player_me(self, player_headers):
        r = requests.get(f"{BASE_URL}/api/auth/me", headers=player_headers, timeout=10)
        assert r.status_code == 200
        assert r.json().get("role") == "player"


# ---------------------------------------------------------------------------
# 2) Import Voti Guidato — dry_run
# ---------------------------------------------------------------------------
class TestImportVotiGuidato:
    def test_upload_pdf_requires_pdf_extension(self, admin_headers):
        files = {"file": ("not_a_pdf.txt", b"hello", "text/plain")}
        r = requests.post(
            f"{BASE_URL}/api/admin/voti/upload-pdf?dry_run=true",
            headers=admin_headers, files=files, timeout=20,
        )
        assert r.status_code == 400
        assert "pdf" in r.json().get("detail", "").lower()

    def test_upload_pdf_invalid_content_dryrun(self, admin_headers):
        # Real .pdf name but garbage content -> parser returns 0 rows OR raises,
        # both surfaced as 400 with Italian message.
        fake_pdf = b"%PDF-1.4\n%not really\n"
        files = {"file": ("fake.pdf", fake_pdf, "application/pdf")}
        r = requests.post(
            f"{BASE_URL}/api/admin/voti/upload-pdf?dry_run=true",
            headers=admin_headers, files=files, timeout=30,
        )
        # Expected: 400 (either "Errore nell'analisi" or "Nessun giocatore riconosciuto")
        assert r.status_code == 400, r.text
        detail = r.json().get("detail", "")
        assert any(k in detail.lower() for k in ["errore", "nessun", "giornata"])

    def test_upload_pdf_dryrun_param_accepted(self, admin_headers):
        # Endpoint must accept dry_run=false&replace=true as query params
        # (we send garbage so we expect 400 still, but not 422 due to param types)
        fake_pdf = b"%PDF-1.4 garbage"
        files = {"file": ("fake.pdf", fake_pdf, "application/pdf")}
        r = requests.post(
            f"{BASE_URL}/api/admin/voti/upload-pdf?dry_run=false&replace=true",
            headers=admin_headers, files=files, timeout=30,
        )
        assert r.status_code != 422, r.text  # params accepted
        assert r.status_code == 400  # still bogus content

    def test_upload_pdf_non_admin_forbidden(self, player_headers):
        files = {"file": ("fake.pdf", b"%PDF-1.4", "application/pdf")}
        r = requests.post(
            f"{BASE_URL}/api/admin/voti/upload-pdf?dry_run=true",
            headers=player_headers, files=files, timeout=15,
        )
        assert r.status_code == 403

    def test_upload_xlsx_dryrun_400_on_bogus(self, admin_headers):
        files = {"file": ("fake.xlsx", b"not really xlsx", "application/vnd.openxmlformats")}
        r = requests.post(
            f"{BASE_URL}/api/admin/voti/upload-xlsx?dry_run=true",
            headers=admin_headers, files=files, timeout=20,
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# 3) Esporta Storico PDF
# ---------------------------------------------------------------------------
class TestExportPDF:
    PAYLOAD = {
        "title": "TEST Riepilogo Giornata 1",
        "subtitle": "TEST — iter6",
        "filename": "TEST_riepilogo_iter6",
        "sections": [
            {
                "heading": "Classifica",
                "columns": ["#", "Utente", "Punti"],
                "rows": [[1, "Alice", 10], [2, "Bob", 8]],
            },
            {"heading": "Vuota", "rows": []},
        ],
    }

    def test_export_pdf_admin_ok(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/export/pdf",
                          json=self.PAYLOAD, headers=admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert len(r.content) > 200
        assert r.content[:4] == b"%PDF"
        cd = r.headers.get("content-disposition", "")
        assert "TEST_riepilogo_iter6" in cd

    def test_export_pdf_non_admin_forbidden(self, player_headers):
        r = requests.post(f"{BASE_URL}/api/export/pdf",
                          json=self.PAYLOAD, headers=player_headers, timeout=15)
        assert r.status_code == 403

    def test_export_pdf_unauth(self):
        r = requests.post(f"{BASE_URL}/api/export/pdf", json=self.PAYLOAD, timeout=15)
        assert r.status_code in (401, 403)

    def test_export_pdf_empty_sections(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/export/pdf",
                          json={"title": "X", "sections": []},
                          headers=admin_headers, timeout=20)
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# 4) Notifiche Automatiche — deadline endpoints reachable, no crash
# ---------------------------------------------------------------------------
class TestDeadlinesAndAutoNotify:
    def test_deadlines_current_ok(self, player_headers):
        r = requests.get(f"{BASE_URL}/api/deadlines/current",
                         headers=player_headers, timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "season" in data
        assert "server_now" in data

    def test_deadlines_list_ok(self, player_headers):
        r = requests.get(f"{BASE_URL}/api/deadlines", headers=player_headers, timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert len(data.get("deadlines", [])) == 38

    def test_set_and_get_future_deadline(self, admin_headers):
        # Use matchday 38 (least likely to interfere).
        from datetime import datetime, timedelta, timezone
        future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
        # Save current value first to restore later
        cur = requests.get(f"{BASE_URL}/api/deadlines/38", headers=admin_headers, timeout=10).json()
        prev = cur.get("deadline_at")
        try:
            r = requests.put(f"{BASE_URL}/api/deadlines/38",
                             json={"deadline_at": future},
                             headers=admin_headers, timeout=10)
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["matchday"] == 38
            assert data["deadline_at"] is not None
            assert data["locked"] is False

            g = requests.get(f"{BASE_URL}/api/deadlines/38",
                             headers=admin_headers, timeout=10)
            assert g.status_code == 200
            assert g.json()["deadline_at"] is not None
        finally:
            # Restore previous deadline (or clear if none)
            restore_body = {"deadline_at": prev} if prev else {"deadline_at": None}
            requests.put(f"{BASE_URL}/api/deadlines/38",
                         json=restore_body, headers=admin_headers, timeout=10)

    def test_backend_still_healthy_after_deadline_ops(self):
        # If auto-notify loop crashed the app it would surface as 5xx here
        r = requests.get(f"{BASE_URL}/api/", timeout=10)
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# 5) Regression smoke — hub & auth
# ---------------------------------------------------------------------------
class TestRegressionSmoke:
    def test_hub_games(self, player_headers):
        r = requests.get(f"{BASE_URL}/api/games", headers=player_headers, timeout=10)
        assert r.status_code == 200
        data = r.json()
        games = data.get("games") if isinstance(data, dict) else data
        assert games and len(games) >= 4

    def test_admin_login_invalid(self):
        r = requests.post(f"{BASE_URL}/api/auth/admin/login",
                          json={"email": ADMIN_EMAIL, "password": "wrong"}, timeout=10)
        assert r.status_code in (400, 401, 403)
