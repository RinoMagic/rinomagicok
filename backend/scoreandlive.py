"""ScoreAndLive — mini-game module for the RinoMagic umbrella app.

Elimination tournament based on guessing goalscorers. Each matchday a player
picks one scorer per playable fixture; a missed pick costs a life. Zero lives
means elimination. Once a scorer is hit, ONLY THAT specific player is off-
limits for the rest of the tournament — the rest of the team stays available.
Postponed matches never cost lives.

Data model (all collections prefixed with `sal_`):

* ``sal_players``        — reference roster (imported from Excel/CSV/PDF)
* ``sal_tournaments``    — one running elimination tournament
* ``sal_matchdays``      — a matchday inside a tournament
* ``sal_picks``          — the picks a player submits for a matchday
* ``sal_participants``   — per-tournament state (lives, blocked players, ...)
"""
from __future__ import annotations

import re
import uuid
import string
import random
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel, Field, field_validator

from deadlines import is_matchday_locked as _global_deadline_passed
from matchday_facts import SERIE_A_TEAMS as _HISTORICAL_TEAMS
from pymongo import ReturnDocument

logger = logging.getLogger("scoreandlive")

# Serie A team names used to anchor the listone parser. We include both the
# current 2026-27 season teams and historical variants (last ~5 seasons)
# from ``matchday_facts.SERIE_A_TEAMS`` so promotions/relegations don't break
# the PDF import each new season. When a listone PDF uses "Hellas Verona"
# instead of "Verona", we normalize it below via ``TEAM_ALIASES``.
SERIE_A_TEAMS = set(_HISTORICAL_TEAMS)
_TEAM_ALIASES = {"Hellas Verona": "Verona"}
# Order: longest names first, so the regex prefers "Hellas Verona" over "Verona".
_SORTED_TEAMS = sorted(SERIE_A_TEAMS, key=len, reverse=True)


def _parse_calendar_pdf(pdf_bytes: bytes) -> List[dict]:
    """Extract Serie A fixtures from a season-calendar PDF.

    The Lega Serie A publishes the season fixtures in several layouts. This
    parser is intentionally tolerant:

      * Matchday headers are detected via patterns like
        ``1ª GIORNATA``, ``GIORNATA 1``, ``1a giornata``, ``1° giornata``.
      * Fixture lines can be one of:
            ``Home - Away``
            ``Home-Away``
            ``Home vs Away``
            ``Home  Away``  (only when both are recognised team names)
      * Optional kickoff time / date around the line is ignored.

    Returns ``[{ "matchday": int, "home_team": str, "away_team": str }, ...]``.
    Rows without a recognised team pair are silently skipped.
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"pdfplumber non installato: {e}") from e

    import io as _io
    md_re = re.compile(
        r"(?:^|\s)(\d{1,2})\s*[ªa°.\-)]?\s*giornata\b|\bgiornata\s+(\d{1,2})\b",
        re.IGNORECASE,
    )
    # Fixture separator: any of  " - " "-" " – " "–" " — " "—" " vs " " V "
    sep_re = re.compile(r"\s*(?:-|–|—|\s+vs\.?\s+|\s+V\s+)\s*")

    teams = sorted(SERIE_A_TEAMS | {"Hellas Verona", "Empoli", "Monza", "Frosinone", "Salernitana", "Venezia"}, key=len, reverse=True)
    team_pattern = "|".join(re.escape(t) for t in teams)
    fixture_re = re.compile(rf"\b({team_pattern})\b.*?\b({team_pattern})\b", re.IGNORECASE)

    def _canonical_team(name: str) -> str:
        low = name.strip().lower()
        for t in teams:
            if t.lower() == low:
                return t
        return name.strip()

    fixtures: List[dict] = []
    seen: set[tuple[int, str, str]] = set()
    current_md: Optional[int] = None

    with pdfplumber.open(_io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw in text.split("\n"):
                line = raw.strip()
                if not line:
                    continue

                # 1) Matchday marker
                m = md_re.search(line)
                if m:
                    n = m.group(1) or m.group(2)
                    if n:
                        try:
                            mdn = int(n)
                            if 1 <= mdn <= 38:
                                current_md = mdn
                                continue
                        except ValueError:
                            pass

                if current_md is None:
                    continue

                # 2) Try the strict "TeamA <sep> TeamB" form first
                strict_parts = sep_re.split(line)
                if len(strict_parts) >= 2:
                    a = strict_parts[0].strip()
                    b = strict_parts[1].strip()
                    # Strip time/date noise around teams
                    a_clean = re.sub(r"^\d{1,2}[:.]\d{2}\s*", "", a)
                    a_clean = re.sub(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*", "", a_clean)
                    b_clean = re.sub(r"\s*\d{1,2}[:.]\d{2}$", "", b)
                    b_clean = re.sub(r"\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?$", "", b_clean)
                    # Try to match against known teams (either direction).
                    for cand_home, cand_away in ((a_clean, b_clean),):
                        # Extract only the recognised team substrings.
                        home_match = re.search(rf"\b({team_pattern})\b", cand_home, re.IGNORECASE)
                        away_match = re.search(rf"\b({team_pattern})\b", cand_away, re.IGNORECASE)
                        if home_match and away_match:
                            ht = _canonical_team(home_match.group(0))
                            at = _canonical_team(away_match.group(0))
                            key = (current_md, ht.lower(), at.lower())
                            if ht and at and ht != at and key not in seen:
                                seen.add(key)
                                fixtures.append({
                                    "matchday": current_md,
                                    "home_team": ht,
                                    "away_team": at,
                                })
                            break
                    else:
                        # fall through to loose match below
                        pass
                    if fixtures and fixtures[-1]["matchday"] == current_md:
                        continue

                # 3) Loose fallback — find any two team names on the line
                m2 = fixture_re.search(line)
                if m2:
                    ht = _canonical_team(m2.group(1))
                    at = _canonical_team(m2.group(2))
                    if ht and at and ht.lower() != at.lower():
                        key = (current_md, ht.lower(), at.lower())
                        if key not in seen:
                            seen.add(key)
                            fixtures.append({
                                "matchday": current_md,
                                "home_team": ht,
                                "away_team": at,
                            })

    return fixtures


def _parse_listone_pdf(pdf_bytes: bytes) -> List[dict]:
    """Extract Serie A players from a "Listone Fantacalcio" PDF.

    Expected layout (one row per player, whitespace-separated):
        <Id> <R> <RM> <Cognome[ Inizialesuffisso.]> <Squadra> <QtA> <QtI> <Diff> <QtAM> <QtIM>

    Returns a list of dicts ready to be inserted in ``sal_players``. Rows that
    don't match are silently skipped (typical for headers, page numbers or the
    Mantra variant appended to the same PDF).
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"pdfplumber non installato: {e}") from e

    import io as _io
    line_re = re.compile(
        r"^(\d+)\s+([PDCA])\s+(\S+)\s+(.+?)\s+(" + "|".join(re.escape(t) for t in _SORTED_TEAMS) + r")\s+"
        r"(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s*$"
    )
    seen_ids: set[int] = set()
    players: List[dict] = []
    with pdfplumber.open(_io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                line = line.strip()
                if not line or line.startswith("Id") or "Quotazioni" in line:
                    continue
                m = line_re.match(line)
                if not m:
                    continue
                fid, role, rm, name_field, team, qa, qi, _diff, _qam, _qim = m.groups()
                team = _TEAM_ALIASES.get(team, team)  # normalize "Hellas Verona" -> "Verona"
                fid_int = int(fid)
                if fid_int in seen_ids:
                    continue  # de-dupe rows from the Mantra section
                seen_ids.add(fid_int)
                parts = name_field.split()
                if len(parts) >= 2 and re.fullmatch(r"[A-Z]\.", parts[-1]):
                    first = parts[-1]
                    last = " ".join(parts[:-1])
                else:
                    first = ""
                    last = name_field
                players.append({
                    "fanta_id": fid_int,
                    "first_name": first,
                    "last_name": last,
                    "team": team,
                    "role": role,
                    "role_mantra": rm,
                    "price_current": int(qa),
                    "price_initial": int(qi),
                })
    return players


# =========================================================================
# Pydantic models (shared)
# =========================================================================

class PlayerIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(min_length=1, max_length=60)
    team: str = Field(min_length=1, max_length=60)
    role: Optional[str] = None

    @field_validator("role")
    @classmethod
    def _norm_role(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return (v.strip().upper()[:2]) or None


class PlayerImport(BaseModel):
    replace_all: bool = False
    players: List[PlayerIn]


class TournamentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    initial_lives: int = Field(default=10, ge=1, le=50)
    start_matchday: int = Field(default=1, ge=1, le=38)
    season: str = Field(default="2026-27", max_length=10)


class MatchdayFixtureIn(BaseModel):
    home_team: str = Field(min_length=1, max_length=60)
    away_team: str = Field(min_length=1, max_length=60)
    postponed: bool = False


class MatchdayCreate(BaseModel):
    matchday_number: int = Field(ge=1, le=38)
    # If ``fixtures`` is omitted OR empty, the endpoint auto-loads the 10
    # fixtures from the season calendar (``sal_calendar``) for that matchday.
    fixtures: Optional[List[MatchdayFixtureIn]] = None


class CalendarFixtureIn(BaseModel):
    matchday: int = Field(ge=1, le=38)
    home_team: str = Field(min_length=1, max_length=60)
    away_team: str = Field(min_length=1, max_length=60)
    kickoff_iso: Optional[str] = None  # optional ISO datetime


class CalendarImportIn(BaseModel):
    season: str = Field(default="2025-26", max_length=10)
    fixtures: List[CalendarFixtureIn]
    replace: bool = True  # wipes previous rows for the season before insert


class PickItem(BaseModel):
    fixture_idx: int = Field(ge=0)
    player_id: str


class PicksSubmit(BaseModel):
    picks: List[PickItem]


class ScorerEntry(BaseModel):
    fixture_idx: int
    player_id: str


class ResultsConfirm(BaseModel):
    scorers: List[ScorerEntry] = []
    postponed_during: List[int] = []


class InviteRedeem(BaseModel):
    invite_code: str


# =========================================================================
# Factory: builds the router with proper auth dependencies
# =========================================================================

def build_router(
    db,
    current_user: Callable,
    require_admin: Callable,
    display_name: Callable,
) -> APIRouter:
    """Return an APIRouter for ScoreAndLive.

    The auth dependencies (``current_user``, ``require_admin``) are captured
    in a closure so FastAPI can resolve them per-request.
    """

    router = APIRouter(prefix="/sal")

    # --- utils -----------------------------------------------------------

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _gen_code(length: int = 6) -> str:
        alphabet = string.ascii_uppercase + string.digits
        return "".join(random.choices(alphabet, k=length))

    def _norm_team(name: str) -> str:
        return (name or "").strip().lower()

    async def _get_tournament(tournament_id: str) -> dict:
        t = await db.sal_tournaments.find_one({"id": tournament_id}, {"_id": 0})
        if not t:
            raise HTTPException(status_code=404, detail="Torneo non trovato")
        return t

    async def _get_matchday(matchday_id: str) -> dict:
        md = await db.sal_matchdays.find_one({"id": matchday_id}, {"_id": 0})
        if not md:
            raise HTTPException(status_code=404, detail="Giornata non trovata")
        return md

    async def _require_tournament_admin(tournament_id: str, user: dict) -> dict:
        t = await _get_tournament(tournament_id)
        if user["role"] != "admin" and user["id"] != t.get("admin_user_id"):
            raise HTTPException(status_code=403, detail="Solo l'admin del torneo può eseguire questa azione")
        return t

    async def _participant(tournament_id: str, user_id: str) -> Optional[dict]:
        return await db.sal_participants.find_one(
            {"tournament_id": tournament_id, "user_id": user_id}, {"_id": 0}
        )

    async def _tournament_dict(t: dict, viewer: Optional[dict] = None) -> dict:
        total = await db.sal_participants.count_documents({"tournament_id": t["id"]})
        alive = await db.sal_participants.count_documents(
            {"tournament_id": t["id"], "eliminated_at_matchday": None}
        )
        is_admin = bool(
            viewer and (viewer["role"] == "admin" or viewer["id"] == t.get("admin_user_id"))
        )
        # Single-use invite stats (mirrors TheBestTiket rooms behaviour).
        invites_total = await db.sal_invites.count_documents(
            {"tournament_id": t["id"], "revoked_at": None}
        )
        invites_available = await db.sal_invites.count_documents(
            {"tournament_id": t["id"], "revoked_at": None, "used_by_user_id": None}
        )
        return {
            **{k: t.get(k) for k in ("id", "name", "status", "current_matchday_number",
                                     "initial_lives", "created_at", "admin_user_id",
                                     "invite_code", "winner_user_id",
                                     "start_matchday", "season")},
            "participants_total": total,
            "participants_alive": alive,
            "invites_total": invites_total,
            "invites_available": invites_available,
            "is_admin": is_admin,
            "archived": bool(t.get("archived", False)),
        }

    async def _invite_dict(inv: dict) -> dict:
        used_nickname = None
        if inv.get("used_by_user_id"):
            u = await db.users.find_one({"id": inv["used_by_user_id"]}, {"_id": 0})
            if u:
                used_nickname = display_name(u)
        return {
            "id": inv["id"],
            "code": inv["code"],
            "tournament_id": inv["tournament_id"],
            "created_at": inv.get("created_at"),
            "created_by": inv.get("created_by"),
            "used_by_user_id": inv.get("used_by_user_id"),
            "used_by_nickname": used_nickname,
            "used_at": inv.get("used_at"),
            "revoked_at": inv.get("revoked_at"),
        }

    def _player_dict(p: dict) -> dict:
        return {
            "id": p["id"],
            "first_name": p.get("first_name"),
            "last_name": p.get("last_name"),
            "full_name": p.get("full_name") or (
                f"{p.get('first_name','').strip()} {p.get('last_name','').strip()}".strip()
            ),
            "team": p.get("team"),
            "role": p.get("role"),
        }

    # --- Players (listone) ---------------------------------------------

    @router.post("/players/import")
    async def import_players(data: PlayerImport, user: dict = Depends(require_admin)):
        if not data.players:
            raise HTTPException(status_code=400, detail="Nessun giocatore fornito")
        if data.replace_all:
            await db.sal_players.delete_many({})
        now = _now()
        docs = []
        for p in data.players:
            docs.append({
                "id": str(uuid.uuid4()),
                "first_name": p.first_name.strip(),
                "last_name": p.last_name.strip(),
                "full_name": f"{p.first_name.strip()} {p.last_name.strip()}",
                "team": p.team.strip(),
                "role": p.role,
                "active": True,
                "created_at": now,
            })
        if docs:
            await db.sal_players.insert_many(docs)
        return {"inserted": len(docs), "total": await db.sal_players.count_documents({})}

    @router.post("/players/import-pdf")
    async def import_players_pdf(
        file: UploadFile = File(...),
        dry_run: bool = True,
        replace_all: bool = False,
        user: dict = Depends(require_admin),
    ):
        """Upload a "Listone Fantacalcio" PDF and import players.

        - ``dry_run=true`` (default) → returns a preview without writing to DB
        - ``dry_run=false`` → actually imports (use ``replace_all=true`` to wipe
          the existing roster first)
        """
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Serve un file .pdf")
        raw = await file.read()
        if len(raw) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="PDF troppo grande (max 20MB)")
        try:
            extracted = _parse_listone_pdf(raw)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("PDF parse error")
            raise HTTPException(status_code=400, detail=f"Errore nell'analisi del PDF: {e}")

        if not extracted:
            raise HTTPException(status_code=400, detail="Nessun giocatore riconosciuto nel PDF. Verifica il formato.")

        # Team distribution helps the admin sanity-check the extraction
        by_team: Dict[str, int] = {}
        by_role: Dict[str, int] = {}
        for p in extracted:
            by_team[p["team"]] = by_team.get(p["team"], 0) + 1
            by_role[p["role"]] = by_role.get(p["role"], 0) + 1

        result: Dict[str, Any] = {
            "extracted": len(extracted),
            "by_team": dict(sorted(by_team.items())),
            "by_role": dict(sorted(by_role.items())),
            "sample": extracted[:15],
            "dry_run": dry_run,
        }

        if dry_run:
            return result

        # Actually import
        if replace_all:
            await db.sal_players.delete_many({})
        now = _now()
        docs = []
        for p in extracted:
            docs.append({
                "id": str(uuid.uuid4()),
                "fanta_id": p["fanta_id"],
                "first_name": p["first_name"],
                "last_name": p["last_name"],
                "full_name": (p["first_name"] + " " + p["last_name"]).strip(),
                "team": p["team"],
                "role": p["role"],
                "role_mantra": p.get("role_mantra"),
                "price_current": p.get("price_current"),
                "price_initial": p.get("price_initial"),
                "active": True,
                "created_at": now,
            })
        await db.sal_players.insert_many(docs)
        result["inserted"] = len(docs)
        result["total"] = await db.sal_players.count_documents({})
        return result

    @router.post("/players/import-xlsx")
    async def import_players_xlsx(
        file: UploadFile = File(...),
        dry_run: bool = True,
        replace_all: bool = False,
        user: dict = Depends(require_admin),
    ):
        """Upload a "Quotazioni Fantacalcio" **XLSX** and import players.

        Same semantics as ``import-pdf`` but reads the official
        fantacalcio.it Excel export (sheet ``Tutti``). Instant parse, zero
        OCR ambiguity.

        - ``dry_run=true`` (default) → preview without writing to DB
        - ``dry_run=false`` → actually imports (use ``replace_all=true`` to
          wipe the existing roster first — recommended for a new season)
        """
        if not file.filename or not file.filename.lower().endswith(".xlsx"):
            raise HTTPException(status_code=400, detail="Serve un file .xlsx")
        raw = await file.read()
        if len(raw) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="XLSX troppo grande (max 20MB)")

        from excel_parser import parse_listone_xlsx  # local import → light startup
        try:
            extracted = parse_listone_xlsx(raw)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("XLSX parse error")
            raise HTTPException(status_code=400, detail=f"Errore nell'analisi dell'XLSX: {e}")

        if not extracted:
            raise HTTPException(
                status_code=400,
                detail="Nessun giocatore riconosciuto nell'XLSX. Verifica il formato "
                       "(atteso: 'Quotazioni Fantacalcio', foglio 'Tutti').",
            )

        by_team: Dict[str, int] = {}
        by_role: Dict[str, int] = {}
        for p in extracted:
            by_team[p["team"]] = by_team.get(p["team"], 0) + 1
            by_role[p["role"]] = by_role.get(p["role"], 0) + 1

        result: Dict[str, Any] = {
            "extracted": len(extracted),
            "by_team": dict(sorted(by_team.items())),
            "by_role": dict(sorted(by_role.items())),
            "sample": extracted[:15],
            "dry_run": dry_run,
        }
        if dry_run:
            return result

        if replace_all:
            await db.sal_players.delete_many({})
        now = _now()
        docs = []
        for p in extracted:
            docs.append({
                "id": str(uuid.uuid4()),
                "fanta_id": p["fanta_id"],
                "first_name": p["first_name"],
                "last_name": p["last_name"],
                "full_name": (p["first_name"] + " " + p["last_name"]).strip(),
                "team": p["team"],
                "role": p["role"],
                "role_mantra": p.get("role_mantra"),
                "price_current": p.get("price_current"),
                "price_initial": p.get("price_initial"),
                "active": True,
                "created_at": now,
            })
        await db.sal_players.insert_many(docs)
        result["inserted"] = len(docs)
        result["total"] = await db.sal_players.count_documents({})
        return result

    @router.get("/players")
    async def list_players(
        q: Optional[str] = Query(default=None, min_length=1, max_length=40),
        team: Optional[str] = None,
        role: Optional[str] = Query(default=None, pattern=r"^(P|D|C|A)$"),
        limit: int = Query(default=50, ge=1, le=1000),
        user: dict = Depends(current_user),
    ):
        filt: Dict[str, Any] = {"active": True}
        if team:
            filt["team"] = {"$regex": f"^{team}$", "$options": "i"}
        if role:
            filt["role"] = role
        if q:
            filt["full_name"] = {"$regex": q, "$options": "i"}
        # Order: role (P=1, D=2, C=3, A=4), then full_name asc.
        pipeline = [
            {"$match": filt},
            {"$addFields": {
                "_role_order": {
                    "$switch": {
                        "branches": [
                            {"case": {"$eq": ["$role", "P"]}, "then": 1},
                            {"case": {"$eq": ["$role", "D"]}, "then": 2},
                            {"case": {"$eq": ["$role", "C"]}, "then": 3},
                            {"case": {"$eq": ["$role", "A"]}, "then": 4},
                        ],
                        "default": 5,
                    },
                },
            }},
            {"$sort": {"_role_order": 1, "full_name": 1}},
            {"$limit": limit},
            {"$project": {"_id": 0, "_role_order": 0}},
        ]
        return [_player_dict(p) async for p in db.sal_players.aggregate(pipeline)]

    @router.get("/players/teams")
    async def list_teams(user: dict = Depends(current_user)):
        teams = await db.sal_players.distinct("team", {"active": True})
        return sorted([t for t in teams if t])

    # --- Tournaments ----------------------------------------------------

    async def _create_tournament_doc(
        *, admin_user_id: str, name: str, initial_lives: int,
        start_matchday: int, season: str,
        previous_tournament_id: Optional[str] = None,
    ) -> dict:
        """Create a fresh tournament doc + unique invite code + auto matchdays.

        Shared helper used both by the manual POST endpoint and by the
        auto-progression flow (when a tournament ends with 0/1 alive, we
        immediately spawn the next round starting from the next unplayed
        matchday, with a brand-new invite code).
        """
        # Unique invite code
        for _ in range(50):
            code = _gen_code()
            if not await db.sal_tournaments.find_one({"invite_code": code}) \
               and not await db.sal_invites.find_one({"code": code}):
                break
        else:
            raise HTTPException(status_code=500, detail="Impossibile generare un codice univoco")

        now = _now()
        tid = str(uuid.uuid4())
        doc = {
            "id": tid,
            "name": name.strip(),
            "admin_user_id": admin_user_id,
            "game": "scoreandlive",
            "status": "open",
            "initial_lives": initial_lives,
            "current_matchday_number": None,
            "start_matchday": start_matchday,
            "season": season,
            "created_at": now,
            "finished_at": None,
            "invite_code": code,
            "winner_user_id": None,
            "blocked_players_by_user": {},
            "blocked_teams_by_user": {},  # legacy; kept for read compat, unused in v2
            "previous_tournament_id": previous_tournament_id,
            "next_tournament_id": None,
        }
        await db.sal_tournaments.insert_one(doc)
        await db.sal_invites.insert_one({
            "id": str(uuid.uuid4()),
            "tournament_id": tid,
            "code": code,
            "used_by_user_id": None,
            "used_at": None,
            "created_at": now,
            "created_by": admin_user_id,
            "revoked_at": None,
        })

        # Auto-create matchdays from ``start_matchday`` to 38.
        md_docs = []
        for md_num in range(start_matchday, 39):
            cal_rows = [r async for r in db.sal_calendar.find(
                {"season": season, "matchday": md_num}, {"_id": 0}
            ).sort("home_team", 1)]
            if not cal_rows:
                continue
            md_docs.append({
                "id": str(uuid.uuid4()),
                "tournament_id": tid,
                "matchday_number": md_num,
                "fixtures": [
                    {"idx": i, "home_team": r["home_team"].strip(),
                     "away_team": r["away_team"].strip(),
                     "kickoff_iso": r.get("kickoff_iso"),
                     "postponed_before": False, "postponed_during": False}
                    for i, r in enumerate(cal_rows)
                ],
                "scorers": [],
                "status": "open",
                "starts_at": None,   # set when admin locks the matchday
                "created_at": now,
            })
        if md_docs:
            await db.sal_matchdays.insert_many(md_docs)
        return doc

    async def _close_tournament_and_advance(t: dict, last_settled_md: int) -> Optional[str]:
        """Called after a matchday settles with <=1 survivors.

        - Marks the tournament as ``finished`` with winner + timestamp.
        - Attempts to spawn a follow-up tournament starting from the next
          unplayed matchday (if any calendar rows exist). The new tournament
          inherits ``initial_lives`` from its predecessor and gets a fresh
          unique invite code. Returns the new tournament's id (or None if
          the season is done).
        """
        # Determine the winner (0 or 1 alive)
        alive = [p async for p in db.sal_participants.find(
            {"tournament_id": t["id"], "eliminated_at_matchday": None}, {"_id": 0})]
        winner = alive[0]["user_id"] if len(alive) == 1 else None
        await db.sal_tournaments.update_one(
            {"id": t["id"]},
            {"$set": {"status": "finished", "winner_user_id": winner,
                      "finished_at": _now()}},
        )

        # Try to spawn the next round
        next_start = last_settled_md + 1
        if next_start > 38:
            return None
        remaining = await db.sal_calendar.count_documents(
            {"season": t.get("season"), "matchday": {"$gte": next_start},
             "excluded": {"$ne": True}}
        )
        if remaining == 0:
            return None

        # Naming: "Serie A 2026-27 · Round 1" → "· Round 2"
        base_name = t["name"]
        m = re.search(r"·\s*Round\s+(\d+)\s*$", base_name)
        if m:
            n = int(m.group(1)) + 1
            new_name = re.sub(r"·\s*Round\s+\d+\s*$", f"· Round {n}", base_name)
        else:
            new_name = f"{base_name} · Round 2"

        new_doc = await _create_tournament_doc(
            admin_user_id=t["admin_user_id"],
            name=new_name,
            initial_lives=t["initial_lives"],
            start_matchday=next_start,
            season=t.get("season") or "2026-27",
            previous_tournament_id=t["id"],
        )
        await db.sal_tournaments.update_one(
            {"id": t["id"]}, {"$set": {"next_tournament_id": new_doc["id"]}}
        )
        return new_doc["id"]

    @router.post("/tournaments")
    async def create_tournament(data: TournamentCreate, user: dict = Depends(require_admin)):
        doc = await _create_tournament_doc(
            admin_user_id=user["id"], name=data.name,
            initial_lives=data.initial_lives,
            start_matchday=data.start_matchday, season=data.season,
        )
        # Enrol ALL current admins as participants (every admin plays every
        # tournament automatically). Multi-admin support: each admin has full
        # control regardless of who created the tournament.
        admin_users = [u async for u in db.users.find(
            {"role": "admin"}, {"_id": 0, "id": 1, "username": 1, "email": 1},
        )]
        for adm in admin_users:
            existing = await db.sal_participants.find_one({
                "tournament_id": doc["id"], "user_id": adm["id"],
            })
            if existing:
                continue
            await db.sal_participants.insert_one({
                "tournament_id": doc["id"],
                "user_id": adm["id"],
                "nickname": display_name(adm),
                "lives_remaining": data.initial_lives,
                "eliminated_at_matchday": None,
                "joined_at": _now(),
            })
        # Auto-create the first_scorer bonus draft for this matchday
        try:
            from bonus import ensure_bonus_draft
            await ensure_bonus_draft(
                db, season=data.season, matchday=int(data.start_matchday),
                bonus_type="first_scorer", created_by=user["id"],
            )
        except Exception:
            logger.exception("Failed to ensure bonus draft for SAL %s", doc["id"])
        return await _tournament_dict(doc, user)

    @router.get("/tournaments")
    async def list_tournaments(user: dict = Depends(current_user)):
        if user["role"] == "admin":
            cursor = db.sal_tournaments.find({}, {"_id": 0}).sort("created_at", -1)
        else:
            joined = [p["tournament_id"] async for p in db.sal_participants.find(
                {"user_id": user["id"]}, {"tournament_id": 1, "_id": 0})]
            cursor = db.sal_tournaments.find({"id": {"$in": joined}}, {"_id": 0}).sort("created_at", -1)
        return [await _tournament_dict(t, user) async for t in cursor]

    @router.get("/tournaments/{tournament_id}")
    async def get_tournament(tournament_id: str, user: dict = Depends(current_user)):
        t = await _get_tournament(tournament_id)
        # Find the current open matchday (earliest still-open) so we can flag
        # which participants have already submitted their picks for it.
        current_md = await db.sal_matchdays.find_one(
            {"tournament_id": tournament_id, "status": "open"},
            {"_id": 0, "id": 1, "matchday_number": 1},
            sort=[("matchday_number", 1)],
        )
        submitted_user_ids: set[str] = set()
        if current_md:
            async for pk in db.sal_picks.find(
                {"tournament_id": tournament_id, "matchday_id": current_md["id"]},
                {"_id": 0, "user_id": 1},
            ):
                submitted_user_ids.add(pk["user_id"])
        participants: List[dict] = []
        async for p in db.sal_participants.find({"tournament_id": tournament_id}, {"_id": 0}):
            participants.append({
                "user_id": p["user_id"],
                "nickname": p["nickname"],
                "lives_remaining": p["lives_remaining"],
                "eliminated_at_matchday": p.get("eliminated_at_matchday"),
                "is_me": p["user_id"] == user["id"],
                # Visible to admin + all players: has this user submitted picks
                # for the current open matchday? (False if no open matchday.)
                "has_submitted_current": p["user_id"] in submitted_user_ids,
            })
        participants.sort(key=lambda x: (
            x["eliminated_at_matchday"] is not None,
            -(x["lives_remaining"] or 0),
            x["nickname"].lower(),
        ))
        matchdays: List[dict] = []
        # Only show past (locked/settled) matchdays PLUS the current active
        # one (earliest still-open). This keeps the tournament view focused
        # on "what to do now" instead of listing all 38 upfront.
        all_mds = [md async for md in db.sal_matchdays.find(
            {"tournament_id": tournament_id}, {"_id": 0}
        ).sort("matchday_number", 1)]
        first_open_seen = False
        for md in all_mds:
            if md["status"] == "open":
                if first_open_seen:
                    continue  # skip subsequent open matchdays
                first_open_seen = True
            matchdays.append({
                "id": md["id"],
                "matchday_number": md["matchday_number"],
                "status": md["status"],
                "fixtures_count": sum(1 for f in md.get("fixtures", []) if not f.get("postponed_before")),
            })
        # Resolve blocked player IDs → names for the UI
        blocked_ids = t.get("blocked_players_by_user", {}).get(user["id"], []) or []
        blocked_players_detail: List[dict] = []
        if blocked_ids:
            async for pl in db.sal_players.find(
                {"id": {"$in": blocked_ids}}, {"_id": 0, "id": 1, "full_name": 1, "team": 1},
            ):
                blocked_players_detail.append({
                    "player_id": pl["id"],
                    "full_name": pl.get("full_name"),
                    "team": pl.get("team"),
                })
        return {
            **await _tournament_dict(t, user),
            "participants": participants,
            "matchdays": matchdays,
            "my_blocked_players": blocked_players_detail,
            "my_blocked_teams": [],  # legacy field (empty in v2 rules)
        }

    @router.get("/tournaments/by-code/{invite_code}")
    async def preview_tournament(invite_code: str):
        """Public preview by invite code. Rejects used or revoked codes so the
        UI can distinguish "wrong code" from "already used" like TheBestTiket."""
        code = invite_code.upper().strip()
        inv = await db.sal_invites.find_one({"code": code})
        if not inv:
            raise HTTPException(status_code=404, detail="Codice invito non valido")
        if inv.get("revoked_at"):
            raise HTTPException(status_code=410, detail="Codice invito revocato")
        if inv.get("used_by_user_id"):
            raise HTTPException(status_code=410, detail="Codice invito già utilizzato")
        t = await db.sal_tournaments.find_one({"id": inv["tournament_id"]}, {"_id": 0})
        if not t:
            raise HTTPException(status_code=404, detail="Torneo non trovato")
        return {
            "id": t["id"], "name": t["name"], "status": t["status"],
            "invite_code": code, "game": "scoreandlive",
        }

    # -------- Single-use invite endpoints (admin only) --------

    @router.get("/tournaments/{tournament_id}/invites")
    async def list_invites(tournament_id: str, user: dict = Depends(current_user)):
        await _require_tournament_admin(tournament_id, user)
        invites = [inv async for inv in db.sal_invites.find(
            {"tournament_id": tournament_id}, {"_id": 0}
        ).sort("created_at", -1)]
        return [await _invite_dict(i) for i in invites]

    @router.post("/tournaments/{tournament_id}/invites")
    async def create_invite(tournament_id: str, user: dict = Depends(current_user)):
        await _require_tournament_admin(tournament_id, user)
        for _ in range(20):
            code = _gen_code()
            existing_t = await db.sal_tournaments.find_one({"invite_code": code})
            existing_inv = await db.sal_invites.find_one({"code": code})
            if not existing_t and not existing_inv:
                break
        else:
            raise HTTPException(status_code=500, detail="Impossibile generare un codice univoco, riprova")
        now = _now()
        doc = {
            "id": str(uuid.uuid4()),
            "tournament_id": tournament_id,
            "code": code,
            "used_by_user_id": None,
            "used_at": None,
            "created_at": now,
            "created_by": user["id"],
            "revoked_at": None,
        }
        await db.sal_invites.insert_one(doc)
        return await _invite_dict(doc)

    @router.delete("/tournaments/{tournament_id}/invites/{invite_id}")
    async def revoke_invite(tournament_id: str, invite_id: str, user: dict = Depends(current_user)):
        await _require_tournament_admin(tournament_id, user)
        inv = await db.sal_invites.find_one({"id": invite_id, "tournament_id": tournament_id}, {"_id": 0})
        if not inv:
            raise HTTPException(status_code=404, detail="Invito non trovato")
        if inv.get("used_by_user_id"):
            raise HTTPException(status_code=400, detail="Impossibile revocare: invito già utilizzato")
        if inv.get("revoked_at"):
            return await _invite_dict(inv)
        now = _now()
        await db.sal_invites.update_one({"id": invite_id}, {"$set": {"revoked_at": now}})
        inv["revoked_at"] = now
        return await _invite_dict(inv)

    @router.post("/tournaments/{tournament_id}/join")
    async def join_tournament(tournament_id: str, data: InviteRedeem, user: dict = Depends(current_user)):
        code = data.invite_code.upper().strip()
        # Guard: if the user is ALREADY a participant of this tournament,
        # refuse before consuming a fresh invite (otherwise a valid code
        # would be burned uselessly, "stealing" it from another player).
        existing = await _participant(tournament_id, user["id"])
        if existing:
            # Idempotence tolerated only if the code they're presenting is the
            # one they previously redeemed themselves.
            inv = await db.sal_invites.find_one({"code": code, "tournament_id": tournament_id})
            if inv and inv.get("used_by_user_id") == user["id"]:
                t = await _get_tournament(tournament_id)
                return await _tournament_dict(t, user)
            raise HTTPException(
                status_code=400,
                detail="Sei già iscritto a questo torneo. Ogni utente può usare un solo invito per torneo.",
            )

        # Atomically claim the invite — race-safe under concurrent joins.
        now = _now()
        claimed = await db.sal_invites.find_one_and_update(
            {"code": code, "tournament_id": tournament_id,
             "used_by_user_id": None, "revoked_at": None},
            {"$set": {"used_by_user_id": user["id"], "used_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            # Distinguish "wrong code / wrong tournament" from "already used".
            inv = await db.sal_invites.find_one({"code": code})
            if not inv or inv.get("tournament_id") != tournament_id:
                raise HTTPException(status_code=400, detail="Codice invito non valido per questo torneo")
            if inv.get("revoked_at"):
                raise HTTPException(status_code=410, detail="Codice invito revocato")
            if inv.get("used_by_user_id") == user["id"]:
                # Idempotence: same user retrying → allow entry.
                t = await _get_tournament(tournament_id)
                return await _tournament_dict(t, user)
            raise HTTPException(status_code=410, detail="Codice invito già utilizzato")

        t = await _get_tournament(tournament_id)
        if t["status"] not in ("open",):
            # Roll back the claim if the tournament is closed.
            await db.sal_invites.update_one(
                {"id": claimed["id"]},
                {"$set": {"used_by_user_id": None, "used_at": None}},
            )
            raise HTTPException(status_code=400, detail="Il torneo non accetta più iscrizioni")

        await db.sal_participants.insert_one({
            "tournament_id": tournament_id,
            "user_id": user["id"],
            "nickname": display_name(user),
            "lives_remaining": t["initial_lives"],
            "eliminated_at_matchday": None,
            "joined_at": now,
        })
        return await _tournament_dict(t, user)

    @router.post("/tournaments/{tournament_id}/archive")
    async def archive_tournament(
        tournament_id: str,
        archived: bool = True,
        user: dict = Depends(require_admin),
    ):
        """Archive/unarchive a FINISHED tournament (keeps history, hides from active list)."""
        await _require_tournament_admin(tournament_id, user)
        t = await _get_tournament(tournament_id)
        if archived and t.get("status") != "finished":
            raise HTTPException(status_code=400, detail="Solo i tornei conclusi possono essere archiviati")
        await db.sal_tournaments.update_one({"id": tournament_id}, {"$set": {"archived": bool(archived)}})
        t["archived"] = bool(archived)
        return await _tournament_dict(t, user)

    @router.delete("/tournaments/{tournament_id}")
    async def delete_tournament(
        tournament_id: str,
        force: bool = False,
        user: dict = Depends(require_admin),
    ):
        """Delete a tournament and all its cascading data.

        SAFETY: to protect historical integrity, tournaments that contain
        at least one submitted pick are NEVER deleted unless the caller
        explicitly passes ``force=true``. This makes deletion a two-step
        opt-in: safe by default, admins can still nuke if needed.
        """
        await _require_tournament_admin(tournament_id, user)
        picks_count = await db.sal_picks.count_documents({"tournament_id": tournament_id})
        if picks_count > 0 and not force:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Il torneo contiene {picks_count} giocate storiche. "
                    "Eliminarlo cancellerebbe lo storico. Riprova con force=true "
                    "solo se sei davvero sicuro."
                ),
            )
        await db.sal_tournaments.delete_one({"id": tournament_id})
        await db.sal_participants.delete_many({"tournament_id": tournament_id})
        await db.sal_matchdays.delete_many({"tournament_id": tournament_id})
        await db.sal_picks.delete_many({"tournament_id": tournament_id})
        await db.sal_invites.delete_many({"tournament_id": tournament_id})
        return {"ok": True, "deleted_picks": picks_count}

    @router.post("/tournaments/{tournament_id}/kick/{user_id}")
    async def kick_from_tournament(
        tournament_id: str,
        user_id: str,
        user: dict = Depends(require_admin),
    ):
        """Hard-remove a player from a ScoreAndLive tournament: removes
        participant record, all their picks across all matchdays. Irreversible."""
        await _require_tournament_admin(tournament_id, user)
        t = await db.sal_tournaments.find_one({"id": tournament_id}, {"_id": 0})
        if t and t.get("admin_user_id") == user_id:
            raise HTTPException(
                status_code=400,
                detail="Impossibile escludere l'admin del torneo",
            )
        target = await db.users.find_one(
            {"id": user_id}, {"_id": 0, "password_hash": 0}
        )
        if not target:
            raise HTTPException(status_code=404, detail="Utente non trovato")
        part = await db.sal_participants.find_one(
            {"tournament_id": tournament_id, "user_id": user_id}
        )
        if not part:
            raise HTTPException(
                status_code=404,
                detail="Il giocatore non è iscritto a questo torneo",
            )
        deleted_picks = await db.sal_picks.delete_many(
            {"tournament_id": tournament_id, "user_id": user_id}
        )
        await db.sal_participants.delete_many(
            {"tournament_id": tournament_id, "user_id": user_id}
        )
        return {
            "ok": True,
            "deleted_picks": deleted_picks.deleted_count,
            "kicked_user_id": user_id,
        }

    @router.get("/tournaments/archive/list")
    async def list_archived_tournaments(user: dict = Depends(current_user)):
        """List all finished tournaments (permanent history, visible to all)."""
        cursor = db.sal_tournaments.find(
            {"status": "finished"}, {"_id": 0}
        ).sort("finished_at", -1)
        out = []
        async for t in cursor:
            # Enrich with winner nickname
            winner_nick = None
            if t.get("winner_user_id"):
                p = await db.sal_participants.find_one({
                    "tournament_id": t["id"], "user_id": t["winner_user_id"]
                }, {"_id": 0})
                winner_nick = p.get("nickname") if p else None
            total = await db.sal_participants.count_documents({"tournament_id": t["id"]})
            settled_mds = await db.sal_matchdays.count_documents({
                "tournament_id": t["id"], "status": "settled"
            })
            out.append({
                "id": t["id"], "name": t["name"], "season": t.get("season"),
                "start_matchday": t.get("start_matchday"),
                "created_at": t.get("created_at"),
                "finished_at": t.get("finished_at"),
                "winner_user_id": t.get("winner_user_id"),
                "winner_nickname": winner_nick,
                "participants_total": total,
                "settled_matchdays": settled_mds,
            })
        return out

    @router.get("/tournaments/{tournament_id}/history")
    async def tournament_history(tournament_id: str, user: dict = Depends(current_user)):
        """Full public history of a tournament: all matchdays + all picks by everyone.

        For an OPEN tournament, only ``locked`` and ``settled`` matchdays
        expose picks (to avoid revealing others' picks before the deadline).
        For a FINISHED tournament, every matchday's picks are public.
        """
        t = await _get_tournament(tournament_id)
        is_finished = t.get("status") == "finished"
        _season_ = t.get("season") or "2026-27"
        matchdays = [m async for m in db.sal_matchdays.find(
            {"tournament_id": tournament_id}, {"_id": 0}
        ).sort("matchday_number", 1)]
        participants = {p["user_id"]: p async for p in db.sal_participants.find(
            {"tournament_id": tournament_id}, {"_id": 0}
        )}
        result_mds = []
        for md in matchdays:
            # Visibility gate: finished tournament, matchday locked/settled,
            # OR the global deadline for that matchday has elapsed.
            deadline_passed = await _global_deadline_passed(
                db, _season_, md["matchday_number"],
            )
            picks_visible = (
                is_finished
                or md.get("status") in ("locked", "settled")
                or deadline_passed
            )
            entry = {
                "id": md["id"],
                "matchday_number": md["matchday_number"],
                "status": md.get("status", "open"),
                "starts_at": md.get("starts_at"),
                "settled_at": md.get("settled_at"),
                "fixtures": md.get("fixtures", []),
                "scorers": md.get("scorers", []),
                "deadline_passed": deadline_passed,
                "picks_visible": picks_visible,
                "picks": [],
            }
            if picks_visible:
                cursor = db.sal_picks.find(
                    {"tournament_id": tournament_id, "matchday_id": md["id"]},
                    {"_id": 0},
                )
                async for p in cursor:
                    uid = p["user_id"]
                    part = participants.get(uid, {})
                    entry["picks"].append({
                        "user_id": uid,
                        "nickname": part.get("nickname", "?"),
                        "picks": p.get("picks", []),
                        "outcome": p.get("outcome"),
                    })
            result_mds.append(entry)
        return {
            "tournament": {
                "id": t["id"], "name": t["name"], "status": t.get("status"),
                "season": t.get("season"),
                "winner_user_id": t.get("winner_user_id"),
                "winner_nickname": (participants.get(t.get("winner_user_id") or "") or {}).get("nickname"),
                "finished_at": t.get("finished_at"),
                "previous_tournament_id": t.get("previous_tournament_id"),
                "next_tournament_id": t.get("next_tournament_id"),
            },
            "matchdays": result_mds,
        }

    @router.post("/tournaments/{tournament_id}/matchdays/{matchday_id}/lock")
    async def lock_matchday(tournament_id: str, matchday_id: str,
                            user: dict = Depends(require_admin)):
        """Admin locks a matchday: no more picks accepted, picks become public."""
        await _require_tournament_admin(tournament_id, user)
        md = await db.sal_matchdays.find_one({"id": matchday_id, "tournament_id": tournament_id})
        if not md:
            raise HTTPException(status_code=404, detail="Giornata non trovata")
        if md.get("status") == "settled":
            raise HTTPException(status_code=400, detail="La giornata è già chiusa")
        if md.get("status") == "locked":
            return {"ok": True, "already_locked": True}
        await db.sal_matchdays.update_one(
            {"id": matchday_id},
            {"$set": {"status": "locked", "starts_at": _now()}},
        )
        return {"ok": True, "locked_at": _now()}

    @router.post("/tournaments/{tournament_id}/matchdays/{matchday_id}/unlock")
    async def unlock_matchday(tournament_id: str, matchday_id: str,
                              user: dict = Depends(require_admin)):
        """Admin re-opens a mistakenly locked matchday. Not allowed if settled."""
        await _require_tournament_admin(tournament_id, user)
        md = await db.sal_matchdays.find_one({"id": matchday_id, "tournament_id": tournament_id})
        if not md:
            raise HTTPException(status_code=404, detail="Giornata non trovata")
        if md.get("status") == "settled":
            raise HTTPException(status_code=400, detail="Impossibile riaprire una giornata già risolta")
        await db.sal_matchdays.update_one(
            {"id": matchday_id},
            {"$set": {"status": "open"}, "$unset": {"starts_at": ""}},
        )
        return {"ok": True}

    # --- Matchdays ------------------------------------------------------

    @router.post("/tournaments/{tournament_id}/matchdays")
    async def create_matchday(tournament_id: str, data: MatchdayCreate, user: dict = Depends(require_admin)):
        await _require_tournament_admin(tournament_id, user)
        # Fetch the tournament to enforce start_matchday and use its season.
        t = await db.sal_tournaments.find_one({"id": tournament_id}, {"_id": 0})
        if not t:
            raise HTTPException(status_code=404, detail="Torneo non trovato")
        start_md = int(t.get("start_matchday") or 1)
        if data.matchday_number < start_md:
            raise HTTPException(
                status_code=400,
                detail=f"Questo torneo parte dalla giornata {start_md}. Non puoi creare la giornata {data.matchday_number}.",
            )
        season = t.get("season") or "2026-27"
        existing = await db.sal_matchdays.find_one({
            "tournament_id": tournament_id,
            "matchday_number": data.matchday_number,
        })
        if existing:
            raise HTTPException(status_code=400, detail="Giornata già creata")

        # If no fixtures were provided, load them from the season calendar.
        # This lets the admin upload the full 380-fixture season once and then
        # have each matchday auto-populated with its 10 fixtures.
        provided = list(data.fixtures or [])
        if not provided:
            cal_rows = [r async for r in db.sal_calendar.find(
                {"season": season, "matchday": data.matchday_number}, {"_id": 0}
            ).sort("home_team", 1)]
            if not cal_rows:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Nessuna fixture nel calendario {season} per la giornata "
                        f"{data.matchday_number}. Carica il calendario stagionale "
                        f"da /sal/calendar/import o passa 'fixtures' esplicite."
                    ),
                )
            provided = [
                MatchdayFixtureIn(home_team=r["home_team"], away_team=r["away_team"])
                for r in cal_rows
            ]

        md_id = str(uuid.uuid4())
        # Optional: look up kickoff_iso for each fixture from the season calendar
        # so /summary can decide the pre/post kickoff privacy window.
        kickoff_map: Dict[tuple, Optional[str]] = {}
        async for row in db.sal_calendar.find(
            {"season": season, "matchday": data.matchday_number},
            {"_id": 0, "home_team": 1, "away_team": 1, "kickoff_iso": 1},
        ):
            kickoff_map[(row["home_team"].strip(), row["away_team"].strip())] = row.get("kickoff_iso")

        fixtures = []
        for i, fx in enumerate(provided):
            home = fx.home_team.strip()
            away = fx.away_team.strip()
            fixtures.append({
                "idx": i,
                "home_team": home,
                "away_team": away,
                "kickoff_iso": kickoff_map.get((home, away)),
                "postponed_before": bool(getattr(fx, "postponed", False)),
                "postponed_during": False,
            })
        doc = {
            "id": md_id,
            "tournament_id": tournament_id,
            "matchday_number": data.matchday_number,
            "fixtures": fixtures,
            "scorers": [],
            "status": "open",
            "created_at": _now(),
            "settled_at": None,
        }
        await db.sal_matchdays.insert_one(doc)
        await db.sal_tournaments.update_one(
            {"id": tournament_id},
            {"$set": {"current_matchday_number": data.matchday_number, "status": "running"}},
        )
        return {"id": md_id, **{k: doc[k] for k in ("matchday_number", "fixtures", "status")}}

    @router.patch("/tournaments/{tournament_id}/matchdays/{matchday_id}/fixtures/{idx}")
    async def update_fixture(
        tournament_id: str, matchday_id: str, idx: int,
        data: dict, user: dict = Depends(require_admin),
    ):
        """Edit a single fixture (admin only). Useful for postponements: set
        ``postponed_before=True`` or replace home/away team names.

        Body: ``{"home_team"?: str, "away_team"?: str, "postponed_before"?: bool}``
        """
        await _require_tournament_admin(tournament_id, user)
        md = await _get_matchday(matchday_id)
        if md["tournament_id"] != tournament_id:
            raise HTTPException(status_code=404, detail="Giornata non trovata")
        if md["status"] != "open":
            raise HTTPException(status_code=400, detail="Giornata già chiusa, non modificabile")

        patch: Dict[str, Any] = {}
        if "home_team" in data and isinstance(data["home_team"], str):
            patch[f"fixtures.{idx}.home_team"] = data["home_team"].strip()
        if "away_team" in data and isinstance(data["away_team"], str):
            patch[f"fixtures.{idx}.away_team"] = data["away_team"].strip()
        if "postponed_before" in data:
            patch[f"fixtures.{idx}.postponed_before"] = bool(data["postponed_before"])
        if not patch:
            raise HTTPException(status_code=400, detail="Nessun campo da modificare")
        # ensure idx exists
        if idx < 0 or idx >= len(md["fixtures"]):
            raise HTTPException(status_code=400, detail="Indice fixture non valido")
        await db.sal_matchdays.update_one({"id": matchday_id}, {"$set": patch})
        return await _get_matchday(matchday_id)

    @router.delete("/tournaments/{tournament_id}/matchdays/{matchday_id}/fixtures/{idx}")
    async def delete_fixture(
        tournament_id: str, matchday_id: str, idx: int,
        user: dict = Depends(require_admin),
    ):
        """Remove a single fixture (admin only). Renumbers remaining indices."""
        await _require_tournament_admin(tournament_id, user)
        md = await _get_matchday(matchday_id)
        if md["tournament_id"] != tournament_id:
            raise HTTPException(status_code=404, detail="Giornata non trovata")
        if md["status"] != "open":
            raise HTTPException(status_code=400, detail="Giornata già chiusa, non modificabile")
        if idx < 0 or idx >= len(md["fixtures"]):
            raise HTTPException(status_code=400, detail="Indice fixture non valido")
        new_fixtures = [f for i, f in enumerate(md["fixtures"]) if i != idx]
        for i, f in enumerate(new_fixtures):
            f["idx"] = i
        await db.sal_matchdays.update_one(
            {"id": matchday_id}, {"$set": {"fixtures": new_fixtures}}
        )
        return await _get_matchday(matchday_id)

    # --- Season calendar (admin only) -----------------------------------

    @router.post("/calendar/import")
    async def import_calendar(data: CalendarImportIn, user: dict = Depends(require_admin)):
        """Bulk-import the entire Serie A calendar for a season.

        Typical payload: 380 fixtures (38 matchdays × 10 games each).
        If ``replace=True`` (default), any previous rows for the same season
        are removed before the insert. Idempotent per (season, matchday,
        home_team) triplet.
        """
        if not data.fixtures:
            raise HTTPException(status_code=400, detail="Elenco fixtures vuoto")
        if data.replace:
            await db.sal_calendar.delete_many({"season": data.season})
        now = _now()
        # Basic dedupe within the payload
        seen = set()
        docs = []
        for fx in data.fixtures:
            key = (data.season, fx.matchday, fx.home_team.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            docs.append({
                "id": str(uuid.uuid4()),
                "season": data.season,
                "matchday": fx.matchday,
                "home_team": fx.home_team.strip(),
                "away_team": fx.away_team.strip(),
                "kickoff_iso": fx.kickoff_iso,
                "imported_at": now,
            })
        if docs:
            await db.sal_calendar.insert_many(docs)
        by_md: Dict[int, int] = {}
        for d in docs:
            by_md[d["matchday"]] = by_md.get(d["matchday"], 0) + 1
        return {
            "season": data.season,
            "inserted": len(docs),
            "matchdays": sorted(by_md.keys()),
            "counts_by_matchday": by_md,
        }

    @router.post("/calendar/import-pdf")
    async def import_calendar_pdf(
        file: UploadFile = File(...),
        season: str = "2025-26",
        dry_run: bool = True,
        replace: bool = True,
        user: dict = Depends(require_admin),
    ):
        """Upload the season calendar as a PDF and populate ``sal_calendar``.

        - ``dry_run=true`` (default) → parses the PDF and returns a preview
          (fixtures grouped by matchday) without touching the DB.
        - ``dry_run=false`` → actually writes the fixtures. Use ``replace=true``
          (default) to wipe the previous rows for the same ``season``.
        """
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Serve un file .pdf")
        raw = await file.read()
        if len(raw) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="PDF troppo grande (max 20MB)")
        try:
            fixtures = _parse_calendar_pdf(raw)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Calendar PDF parse error")
            raise HTTPException(status_code=400, detail=f"Errore nell'analisi del PDF: {e}")

        if not fixtures:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Nessuna partita riconosciuta nel PDF. "
                    "Verifica che sia il calendario Serie A e che i nomi delle "
                    "squadre siano nella lista attesa."
                ),
            )

        by_md: Dict[int, int] = {}
        for f in fixtures:
            by_md[f["matchday"]] = by_md.get(f["matchday"], 0) + 1

        preview_sample = fixtures[:20]

        result: Dict[str, Any] = {
            "season": season,
            "extracted": len(fixtures),
            "matchdays": sorted(by_md.keys()),
            "counts_by_matchday": dict(sorted(by_md.items())),
            "sample": preview_sample,
            "dry_run": dry_run,
        }

        if dry_run:
            return result

        # Persist
        if replace:
            await db.sal_calendar.delete_many({"season": season})
        now = _now()
        docs = []
        seen = set()
        for fx in fixtures:
            key = (season, fx["matchday"], fx["home_team"].lower())
            if key in seen:
                continue
            seen.add(key)
            docs.append({
                "id": str(uuid.uuid4()),
                "season": season,
                "matchday": fx["matchday"],
                "home_team": fx["home_team"],
                "away_team": fx["away_team"],
                "kickoff_iso": None,
                "imported_at": now,
            })
        if docs:
            await db.sal_calendar.insert_many(docs)
        result["inserted"] = len(docs)
        result["stored_total"] = await db.sal_calendar.count_documents({"season": season})
        return result

    @router.post("/calendar/import-xlsx")
    async def import_calendar_xlsx(
        file: UploadFile = File(...),
        season: str = "2026-27",
        dry_run: bool = True,
        replace: bool = True,
        user: dict = Depends(require_admin),
    ):
        """Upload the season calendar as an **XLSX** and populate ``sal_calendar``.

        Flexible header detection (columns: Giornata / Casa / Trasferta,
        optional Data). Same dry-run/commit contract as ``import-pdf``.
        """
        if not file.filename or not file.filename.lower().endswith(".xlsx"):
            raise HTTPException(status_code=400, detail="Serve un file .xlsx")
        raw = await file.read()
        if len(raw) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="XLSX troppo grande (max 20MB)")

        from excel_parser import parse_calendar_xlsx  # local import → light startup
        try:
            fixtures, diag = parse_calendar_xlsx(raw)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Calendar XLSX parse error")
            raise HTTPException(status_code=400, detail=f"Errore nell'analisi dell'XLSX: {e}")

        if not fixtures:
            raise HTTPException(
                status_code=400,
                detail="Nessuna partita riconosciuta nell'XLSX. Verifica le colonne (Giornata, Casa, Trasferta).",
            )

        by_md: Dict[int, int] = {}
        for f in fixtures:
            by_md[f["matchday"]] = by_md.get(f["matchday"], 0) + 1
        result: Dict[str, Any] = {
            "season": season,
            "extracted": len(fixtures),
            "matchdays": sorted(by_md.keys()),
            "counts_by_matchday": dict(sorted(by_md.items())),
            "sample": fixtures[:20],
            "dry_run": dry_run,
        }
        if dry_run:
            return result

        if replace:
            await db.sal_calendar.delete_many({"season": season})
        now = _now()
        docs = []
        seen = set()
        for fx in fixtures:
            key = (season, fx["matchday"], fx["home_team"].lower())
            if key in seen:
                continue
            seen.add(key)
            docs.append({
                "id": str(uuid.uuid4()),
                "season": season,
                "matchday": fx["matchday"],
                "home_team": fx["home_team"],
                "away_team": fx["away_team"],
                "kickoff_iso": fx.get("kickoff_iso"),
                "imported_at": now,
            })
        if docs:
            await db.sal_calendar.insert_many(docs)
        result["inserted"] = len(docs)
        result["stored_total"] = await db.sal_calendar.count_documents({"season": season})
        return result

    @router.get("/calendar")
    async def list_calendar(
        season: str = "2025-26",
        matchday: Optional[int] = None,
        user: dict = Depends(current_user),
    ):
        q: Dict[str, Any] = {"season": season}
        if matchday is not None:
            q["matchday"] = matchday
        rows = [r async for r in db.sal_calendar.find(q, {"_id": 0})
                .sort([("matchday", 1), ("home_team", 1)])]
        return {"season": season, "count": len(rows), "fixtures": rows}

    @router.delete("/calendar")
    async def clear_calendar(season: str = "2025-26", user: dict = Depends(require_admin)):
        r = await db.sal_calendar.delete_many({"season": season})
        return {"season": season, "deleted": r.deleted_count}

    async def _propagate_fixture_exclusion(home, away, matchday, excluded: bool) -> dict:
        """Mark/unmark a fixture as excluded across every NON-settled snapshot
        of ``matchday`` in all games, so a removed/excluded match can never be
        played again. Matching is whitespace/case-insensitive.

        * Survival (``sv_matchdays``) & ScoreAndLive (``sal_matchdays``):
          set ``excluded`` + ``postponed_before`` on the fixture → hidden from
          the playable list and rejected on submit.
        * TheBestTiket rooms: inject a neutral ``postponed=True`` result
          (quota 1.00) into ``db.fixtures`` for every open room of the
          matchday, so the excluded match never gains/loses points on the
          schedina — even after the calendar row is deleted.
        """
        h = (home or "").strip().lower()
        a = (away or "").strip().lower()
        counts = {"sv": 0, "sal": 0, "tiket": 0}

        def _same(f):
            return (f.get("home_team") or "").strip().lower() == h and \
                   (f.get("away_team") or "").strip().lower() == a

        async for md in db.sv_matchdays.find(
            {"matchday": matchday, "status": {"$ne": "settled"}},
        ):
            changed = False
            for f in md.get("fixtures", []):
                if _same(f):
                    f["excluded"] = excluded
                    f["postponed_before"] = excluded
                    changed = True
            if changed:
                await db.sv_matchdays.update_one(
                    {"_id": md["_id"]}, {"$set": {"fixtures": md["fixtures"]}},
                )
                counts["sv"] += 1

        async for md in db.sal_matchdays.find(
            {"matchday_number": matchday, "status": {"$ne": "settled"}},
        ):
            changed = False
            for f in md.get("fixtures", []):
                if _same(f):
                    f["excluded"] = excluded
                    f["postponed_before"] = excluded
                    changed = True
            if changed:
                await db.sal_matchdays.update_one(
                    {"_id": md["_id"]}, {"$set": {"fixtures": md["fixtures"]}},
                )
                counts["sal"] += 1

        async for room in db.rooms.find(
            {"matchday": matchday, "game": "thebesttiket",
             "status": {"$ne": "settled"}},
            {"_id": 0, "id": 1},
        ):
            if excluded:
                await db.fixtures.update_one(
                    {"room_id": room["id"], "home_team": home, "away_team": away},
                    {"$set": {
                        "room_id": room["id"],
                        "home_team": home,
                        "away_team": away,
                        "home_score": 0,
                        "away_score": 0,
                        "both_scored": False,
                        "postponed": True,
                        "excluded": True,
                    }},
                    upsert=True,
                )
            else:
                await db.fixtures.delete_one({
                    "room_id": room["id"], "home_team": home,
                    "away_team": away, "excluded": True,
                })
            counts["tiket"] += 1
        return counts

    @router.delete("/calendar/fixture/{fixture_id}")
    async def delete_calendar_fixture(fixture_id: str, user: dict = Depends(require_admin)):
        """Delete a single fixture from the season calendar.

        The removal is PROPAGATED to every NON-settled snapshot of that
        matchday in all games (Survival, ScoreAndLive, TheBestTiket) so the
        deleted match instantly disappears from the playable fixtures and is
        neutralised (quota 1.00) on Tiket schedine.

        SOFT delete: the master calendar row is KEPT (only marked ``excluded``)
        so it is NEVER lost — a future NEW tournament/room will still seed the
        full original calendar. The removal only affects the tournaments/rooms
        that already existed (via snapshot propagation).
        """
        fx = await db.sal_calendar.find_one({"id": fixture_id}, {"_id": 0})
        if not fx:
            raise HTTPException(status_code=404, detail="Partita non trovata")
        propagated = await _propagate_fixture_exclusion(
            fx.get("home_team"), fx.get("away_team"), fx.get("matchday"), True,
        )
        await db.sal_calendar.update_one(
            {"id": fixture_id}, {"$set": {"excluded": True}},
        )
        return {"deleted": True, "soft": True, "propagated": propagated}

    @router.put("/calendar/fixture/{fixture_id}")
    async def update_calendar_fixture(
        fixture_id: str,
        payload: CalendarFixtureIn,
        user: dict = Depends(require_admin),
    ):
        """Edit a fixture (rename teams, move to a different matchday)."""
        r = await db.sal_calendar.find_one_and_update(
            {"id": fixture_id},
            {"$set": {
                "matchday": payload.matchday,
                "home_team": payload.home_team.strip(),
                "away_team": payload.away_team.strip(),
                "kickoff_iso": payload.kickoff_iso,
            }},
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )
        if not r:
            raise HTTPException(status_code=404, detail="Partita non trovata")
        return r

    @router.patch("/calendar/fixture/{fixture_id}/exclude")
    async def toggle_exclude_fixture(
        fixture_id: str,
        payload: Dict[str, Any],
        user: dict = Depends(require_admin),
    ):
        """Toggle the ``excluded`` flag on a season fixture.

        When a match is excluded (pre-round admin action), it becomes
        non-selectable by users in every game (Survival, Score, Fanta,
        Tiket) and hidden from bonus Big Match dropdowns.

        The flag is also propagated to any *open* (non-settled) matchday /
        room snapshots that contain the fixture so already-created games
        pick up the change immediately.

        Body: ``{"excluded": true}`` or ``{"excluded": false}``.
        """
        excluded = bool(payload.get("excluded", True))
        fx = await db.sal_calendar.find_one_and_update(
            {"id": fixture_id},
            {"$set": {"excluded": excluded}},
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )
        if not fx:
            raise HTTPException(status_code=404, detail="Partita non trovata")
        propagated = await _propagate_fixture_exclusion(
            fx["home_team"], fx["away_team"], fx.get("matchday"), excluded,
        )
        return {"fixture": fx, "excluded": excluded, "propagated": propagated}


    @router.get("/tournaments/{tournament_id}/matchdays/{matchday_id}")
    async def get_matchday(tournament_id: str, matchday_id: str, user: dict = Depends(current_user)):
        md = await _get_matchday(matchday_id)
        if md["tournament_id"] != tournament_id:
            raise HTTPException(status_code=404, detail="Giornata non trovata")
        my_picks = await db.sal_picks.find_one(
            {"tournament_id": tournament_id, "matchday_id": matchday_id, "user_id": user["id"]},
            {"_id": 0},
        )
        # v2.1 dynamic-picks: expose how many picks the user must send this
        # matchday (= min(lives, playable_fixtures)). Frontend uses this to
        # gate the submit button.
        part = await _participant(tournament_id, user["id"])
        my_lives = int(part.get("lives_remaining", 0) or 0) if part else 0
        playable_count = sum(
            1 for f in md.get("fixtures", []) or [] if not f.get("postponed_before")
        )
        expected_picks_count = min(my_lives, playable_count) if part else 0
        return {
            **md,
            "my_picks": my_picks,
            "my_lives_remaining": my_lives,
            "expected_picks_count": expected_picks_count,
            "max_lives": 15,
            "playable_fixtures_count": playable_count,
        }

    # --- Picks ----------------------------------------------------------

    @router.post("/tournaments/{tournament_id}/matchdays/{matchday_id}/picks")
    async def submit_picks(
        tournament_id: str, matchday_id: str, data: PicksSubmit,
        user: dict = Depends(current_user),
    ):
        part = await _participant(tournament_id, user["id"])
        if not part:
            raise HTTPException(status_code=403, detail="Non sei iscritto a questo torneo")
        if part.get("eliminated_at_matchday") is not None:
            raise HTTPException(status_code=400, detail="Sei stato eliminato dal torneo")
        md = await _get_matchday(matchday_id)
        if md["tournament_id"] != tournament_id:
            raise HTTPException(status_code=404, detail="Giornata non trovata")
        if md["status"] != "open":
            raise HTTPException(status_code=400, detail="I pick per questa giornata sono chiusi")

        # Global deadline gate (shared timer across all games).
        t_for_gate = await _get_tournament(tournament_id)
        _season_for_gate = t_for_gate.get("season") or "2026-27"
        if await _global_deadline_passed(db, _season_for_gate, md["matchday_number"]):
            raise HTTPException(
                status_code=403,
                detail="Il timer di invio pronostici è scaduto per questa giornata.",
            )

        playable = [f for f in md["fixtures"] if not f.get("postponed_before")]
        playable_ids = {f["idx"] for f in playable}

        # v2.1 dynamic-picks rule: number of required picks = min(lives_left, playable).
        # Player chooses freely which N matches to play. All others are simply
        # skipped (no life lost for un-picked matches).
        MAX_LIVES = 15  # cap enforced also in settle_matchday
        lives = int(part.get("lives_remaining", 0) or 0)
        expected_picks = min(lives, len(playable_ids))

        seen_ids: set[int] = set()
        for p in data.picks:
            if p.fixture_idx not in playable_ids:
                raise HTTPException(status_code=400, detail=f"Partita {p.fixture_idx} non giocabile")
            if p.fixture_idx in seen_ids:
                raise HTTPException(status_code=400, detail="Pick duplicato per la stessa partita")
            seen_ids.add(p.fixture_idx)
        if len(seen_ids) != expected_picks:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Devi inviare esattamente {expected_picks} pronostici "
                    f"(hai {lives} vite): ne hai inviati {len(seen_ids)}."
                ),
            )

        t = await _get_tournament(tournament_id)
        # v2 rule: block ONLY the specific players a user has previously hit
        # (not the whole team). We still read the legacy team-block field for
        # backwards compat on old tournaments but new blocks go into
        # ``blocked_players_by_user``.
        blocked_players = set(
            t.get("blocked_players_by_user", {}).get(user["id"], []),
        )

        pick_docs = []
        for p in data.picks:
            fx = next(f for f in md["fixtures"] if f["idx"] == p.fixture_idx)
            player = await db.sal_players.find_one({"id": p.player_id}, {"_id": 0})
            if not player:
                raise HTTPException(status_code=400, detail=f"Giocatore {p.player_id} non trovato")
            home = _norm_team(fx["home_team"])
            away = _norm_team(fx["away_team"])
            p_team = _norm_team(player["team"])
            if p_team not in (home, away):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{player.get('full_name')} gioca nel {player.get('team')}, "
                        f"che non fa parte di {fx['home_team']} - {fx['away_team']}"
                    ),
                )
            # v2 rule: reject the pick if THIS specific player is already
            # blocked for the user (i.e. they already scored in a previous
            # matchday). Team-level blocks no longer exist.
            if p.player_id in blocked_players:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Hai già usato {player.get('full_name')} in una "
                        "giornata precedente: scegli un altro marcatore."
                    ),
                )
            pick_docs.append({
                "fixture_idx": p.fixture_idx,
                "player_id": p.player_id,
                "player_name": player.get("full_name"),
                "team": player.get("team"),
                # legacy field, no longer used (kept for downstream tools)
                "deadlock_override": False,
            })

        await db.sal_picks.update_one(
            {"tournament_id": tournament_id, "matchday_id": matchday_id, "user_id": user["id"]},
            {"$set": {
                "tournament_id": tournament_id,
                "matchday_id": matchday_id,
                "user_id": user["id"],
                "nickname": display_name(user),
                "picks": pick_docs,
                "submitted_at": _now(),
            }},
            upsert=True,
        )
        return {"ok": True, "picks": pick_docs}

    # --- Settlement -----------------------------------------------------

    @router.post("/tournaments/{tournament_id}/matchdays/{matchday_id}/settle")
    async def settle_matchday(
        tournament_id: str, matchday_id: str, data: ResultsConfirm,
        user: dict = Depends(require_admin),
    ):
        t = await _require_tournament_admin(tournament_id, user)
        md = await _get_matchday(matchday_id)
        if md["tournament_id"] != tournament_id:
            raise HTTPException(status_code=404, detail="Giornata non trovata")
        if md["status"] == "settled":
            raise HTTPException(status_code=400, detail="Giornata già chiusa")

        playable = {f["idx"] for f in md["fixtures"] if not f.get("postponed_before")}
        postponed_during = {i for i in data.postponed_during if i in playable}

        scorers_by_fixture: Dict[int, List[str]] = {}
        for s in data.scorers:
            if s.fixture_idx not in playable:
                raise HTTPException(status_code=400, detail=f"Partita {s.fixture_idx} non giocabile")
            if s.fixture_idx in postponed_during:
                raise HTTPException(
                    status_code=400,
                    detail=f"La partita {s.fixture_idx} è stata rinviata durante la giornata",
                )
            player = await db.sal_players.find_one({"id": s.player_id}, {"_id": 0})
            if not player:
                raise HTTPException(status_code=400, detail=f"Giocatore {s.player_id} non trovato")
            scorers_by_fixture.setdefault(s.fixture_idx, []).append(s.player_id)

        # v2 rule: block only the individual scorer that was hit, not the
        # whole team. Postponed matches during the matchday just skip the
        # pick (no life lost, no block gained).
        blocked_by_user = dict(t.get("blocked_players_by_user", {}))

        picks_cursor = db.sal_picks.find(
            {"tournament_id": tournament_id, "matchday_id": matchday_id}, {"_id": 0}
        )
        async for pk in picks_cursor:
            user_id = pk["user_id"]
            part = await _participant(tournament_id, user_id)
            if not part or part.get("eliminated_at_matchday") is not None:
                continue
            lives_lost = 0
            hits: List[dict] = []
            misses: List[dict] = []
            for p in pk["picks"]:
                fidx = p["fixture_idx"]
                if fidx in postponed_during:
                    continue  # Life saved
                scorer_ids = scorers_by_fixture.get(fidx, [])
                if p["player_id"] in scorer_ids:
                    hits.append(p)
                    blocked_set = set(blocked_by_user.get(user_id, []))
                    blocked_set.add(p["player_id"])
                    blocked_by_user[user_id] = sorted(blocked_set)
                else:
                    misses.append(p)
                    lives_lost += 1
            new_lives_raw = part["lives_remaining"] - lives_lost + len(hits)
            # v2.1: +1 vita per hit, cap max 15 vite (MAX_LIVES).
            new_lives = max(0, min(15, new_lives_raw))
            set_fields = {"lives_remaining": new_lives}
            if new_lives == 0 and part.get("eliminated_at_matchday") is None:
                set_fields["eliminated_at_matchday"] = md["matchday_number"]
            await db.sal_participants.update_one(
                {"tournament_id": tournament_id, "user_id": user_id},
                {"$set": set_fields},
            )
            await db.sal_picks.update_one(
                {"tournament_id": tournament_id, "matchday_id": matchday_id, "user_id": user_id},
                {"$set": {
                    "hits": hits, "misses": misses,
                    "lives_lost": lives_lost, "lives_gained": len(hits),
                }},
            )

        await db.sal_tournaments.update_one(
            {"id": tournament_id},
            {"$set": {"blocked_players_by_user": blocked_by_user}},
        )
        scorers_list = [
            {"fixture_idx": fidx, "player_id": pid}
            for fidx, pids in scorers_by_fixture.items()
            for pid in pids
        ]
        fixtures_updated = []
        for fx in md["fixtures"]:
            f = dict(fx)
            if fx["idx"] in postponed_during:
                f["postponed_during"] = True
            fixtures_updated.append(f)
        await db.sal_matchdays.update_one(
            {"id": matchday_id},
            {"$set": {
                "scorers": scorers_list,
                "fixtures": fixtures_updated,
                "status": "settled",
                "settled_at": _now(),
            }},
        )
        alive = [p async for p in db.sal_participants.find(
            {"tournament_id": tournament_id, "eliminated_at_matchday": None}, {"_id": 0}
        )]
        next_tid: Optional[str] = None
        if len(alive) <= 1:
            # Reload tournament (previous state might be stale) then close+advance.
            fresh = await db.sal_tournaments.find_one({"id": tournament_id}, {"_id": 0})
            if fresh and fresh.get("status") != "finished":
                next_tid = await _close_tournament_and_advance(fresh, md["matchday_number"])
        return {"ok": True, "settled": True, "alive_count": len(alive),
                "next_tournament_id": next_tid}

    # ------------------------------------------------------------------
    # Riassunto Giornata (privacy-aware aggregation)
    # ------------------------------------------------------------------

    def _md_first_kickoff(md: dict) -> Optional[str]:
        first: Optional[str] = None
        for fx in md.get("fixtures", []) or []:
            k = fx.get("kickoff_iso")
            if k and (first is None or k < first):
                first = k
        return first

    def _md_summary_locked(md: dict) -> bool:
        """Return True once the FIRST kickoff of the matchday has passed
        (or the matchday is already settled)."""
        if md.get("status") == "settled":
            return True
        k = _md_first_kickoff(md)
        if not k:
            return False
        try:
            dt = datetime.fromisoformat(k.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= dt
        except Exception:
            return False

    @router.get("/tournaments/{tournament_id}/matchdays/{matchday_id}/summary")
    async def matchday_summary(
        tournament_id: str, matchday_id: str, user: dict = Depends(current_user),
    ):
        """Riassunto Giornata for ScoreAndLive.

        - Pre-kickoff of the first fixture: shows only aggregated counts per
          fixture (which players/teams have been picked, how many times) —
          without revealing which user picked which player.
        - Post-kickoff: reveals every pick with the nickname of who made it,
          so all participants can review the group's choices.
        """
        # Any user in the tournament can read it (participants + admins).
        part = await _participant(tournament_id, user["id"])
        t = await db.sal_tournaments.find_one({"id": tournament_id}, {"admin_user_id": 1, "_id": 0})
        is_admin = t and (user["role"] == "admin" or user["id"] == t.get("admin_user_id"))
        if not part and not is_admin:
            raise HTTPException(status_code=403, detail="Non sei iscritto a questo torneo")

        md = await _get_matchday(matchday_id)
        if md["tournament_id"] != tournament_id:
            raise HTTPException(status_code=404, detail="Giornata non trovata")

        locked = _md_summary_locked(md)
        first_kick = _md_first_kickoff(md)

        # Privacy boost: when the pool of ALIVE participants shrinks, the
        # aggregate pick counts become de-facto revealing (e.g. with only 3
        # players left, "Lautaro: 2 picks" tells everyone what those 2
        # players picked). We therefore hide candidates entirely while the
        # matchday is still un-locked (before first kickoff). Once matches
        # start, aggregate counts are irrelevant because individual picks
        # are anyway visible.
        active_count = await db.sal_participants.count_documents({
            "tournament_id": tournament_id,
            "eliminated_at_matchday": None,
        })
        PRIVACY_MIN_ACTIVE = 4  # hide summary when 4 or fewer alive players
        privacy_boost = (
            active_count <= PRIVACY_MIN_ACTIVE
            and not locked
            and md.get("status") != "settled"
        )

        # Load ALL picks for this matchday in a single roundtrip
        picks_cur = db.sal_picks.find(
            {"tournament_id": tournament_id, "matchday_id": matchday_id},
        )
        # Bucket picks by fixture_idx → player_id → list of pick metadata
        by_fx_player: Dict[int, Dict[str, Dict[str, Any]]] = {}
        async for doc in picks_cur:
            nickname = doc.get("nickname", "?")
            uid = doc["user_id"]
            for pk in doc.get("picks", []) or []:
                fi = int(pk.get("fixture_idx", -1))
                pid = pk.get("player_id") or "?"
                slot = by_fx_player.setdefault(fi, {}).setdefault(pid, {
                    "player_id": pid,
                    "player_name": pk.get("player_name"),
                    "team": pk.get("team"),
                    "count": 0,
                    "pickers": [],  # populated only when the summary is unlocked
                })
                slot["count"] += 1
                if locked:
                    slot["pickers"].append({
                        "user_id": uid,
                        "nickname": nickname,
                        "deadlock_override": bool(pk.get("deadlock_override")),
                    })

        # Build the per-fixture output preserving the order of md.fixtures
        fixtures_out: List[dict] = []
        for i, fx in enumerate(md.get("fixtures", []) or []):
            candidates = list(by_fx_player.get(i, {}).values())
            candidates.sort(key=lambda c: (-c["count"], (c.get("player_name") or "").lower()))
            # Strip pickers when locked=False (defense in depth against
            # tampering the client — the loop above already skips them, but
            # this makes the guarantee explicit).
            if not locked:
                for c in candidates:
                    c["pickers"] = None
            # Privacy boost: hide candidate details AND total when pool is small
            if privacy_boost:
                candidates = []
            fixtures_out.append({
                "fixture_idx": i,
                "home_team": fx.get("home_team"),
                "away_team": fx.get("away_team"),
                "kickoff_iso": fx.get("kickoff_iso"),
                "total_picks": 0 if privacy_boost else sum(c["count"] for c in candidates),
                "candidates": candidates,
            })

        return {
            "matchday": md.get("matchday_number"),
            "kickoff_first": first_kick,
            "locked": locked,
            "settled": md.get("status") == "settled",
            "privacy_boost": privacy_boost,
            "active_participants": active_count,
            "fixtures": fixtures_out,
        }

    return router


async def ensure_indexes(db) -> None:
    """Create MongoDB indexes for the ScoreAndLive collections."""
    await db.sal_players.create_index([("full_name", 1)])
    await db.sal_players.create_index("team")
    await db.sal_tournaments.create_index("admin_user_id")
    await db.sal_tournaments.create_index("invite_code", unique=True, sparse=True)
    await db.sal_matchdays.create_index([("tournament_id", 1), ("matchday_number", 1)], unique=True)
    await db.sal_picks.create_index(
        [("tournament_id", 1), ("matchday_id", 1), ("user_id", 1)], unique=True
    )
    await db.sal_participants.create_index(
        [("tournament_id", 1), ("user_id", 1)], unique=True
    )
    # Single-use invites (mirrors the TheBestTiket rooms model).
    await db.sal_invites.create_index("code", unique=True)
    await db.sal_invites.create_index(
        [("tournament_id", 1), ("used_by_user_id", 1)]
    )
    # Season calendar
    await db.sal_calendar.create_index([("season", 1), ("matchday", 1)])
    await db.sal_calendar.create_index(
        [("season", 1), ("matchday", 1), ("home_team", 1)], unique=True
    )

    # Backfill: for legacy tournaments that carry `invite_code` on the document
    # but have no matching invite record, create the corresponding single-use
    # invite so existing invite links keep working.
    now = datetime.now(timezone.utc).isoformat()
    async for t in db.sal_tournaments.find(
        {"invite_code": {"$exists": True}},
        {"id": 1, "invite_code": 1, "admin_user_id": 1, "created_at": 1, "_id": 0},
    ):
        existing = await db.sal_invites.find_one({"code": t["invite_code"]})
        if not existing:
            # If the tournament already has participants beyond the admin, we
            # assume the initial code was already redeemed (legacy multi-use
            # behaviour). Mark it as used-by-admin so it can't be re-consumed.
            used_by = None
            used_at = None
            n_participants = await db.sal_participants.count_documents({"tournament_id": t["id"]})
            if n_participants > 1:
                used_by = t.get("admin_user_id")
                used_at = t.get("created_at") or now
            await db.sal_invites.insert_one({
                "id": str(uuid.uuid4()),
                "tournament_id": t["id"],
                "code": t["invite_code"],
                "used_by_user_id": used_by,
                "used_at": used_at,
                "created_at": t.get("created_at") or now,
                "created_by": t.get("admin_user_id"),
                "revoked_at": None,
            })
