"""Surviva 2.0 — the fourth mini-game inside the RinoMagic umbrella.

Elimination tournament based on 1X2 predictions. Each matchday a player
picks **one** fixture and its outcome (1 / X / 2). A wrong prediction costs
one life. When a user has zero lives left they are eliminated. Once a
player has correctly guessed a (team, outcome) pair (e.g. "Inter → Vittoria"),
that combination becomes permanently unavailable for future matchdays — no
matter whether Inter plays at home or away.

Data model (all collections prefixed with ``sv_``):

* ``sv_tournaments``    — one running elimination tournament
* ``sv_invites``        — one-shot invite codes to join
* ``sv_participants``   — per-tournament state (lives, blocked signs, ...)
* ``sv_matchdays``      — a matchday inside a tournament
* ``sv_picks``          — the pick a player submits for a matchday (one per user)
* ``sal_calendar``      — shared with ScoreAndLive (season fixtures)

Cross-game features (also consumed by ScoreAndLive):
* Riassunto Giornata: pre-kickoff aggregated view, post-kickoff detailed view.
"""
from __future__ import annotations

import re
import uuid
import string
import random
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Callable, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from bonus import ensure_bonus_draft
from deadlines import is_matchday_locked as _global_deadline_passed, _parse_stored

logger = logging.getLogger("surviva")

DEFAULT_LIVES = 3

# Maximum number of picks a player can be asked to submit in a single
# matchday. Corresponds to ``initial_lives`` upper bound — a player can
# never have more picks required than lives they hold.
MAX_PICKS_PER_MATCHDAY = 10

# =========================================================================
# Team-lock engine (Surviva 2.0 — new rules)
# =========================================================================
# Rules (agreed with product):
#   • Each matchday the player submits **3 picks** on **3 different matches**.
#   • A CORRECT pick with sign "1"  → the *home* team gets locked.
#   • A CORRECT pick with sign "2"  → the *away* team gets locked.
#   • A CORRECT pick with sign "X"  → NO team gets locked (exception).
#   • A WRONG pick   → the team is NOT locked (only lives are consumed).
#   • Concession    → a fixture where BOTH teams are already locked is
#                     playable with any sign; the outcome of that pick does
#                     NOT introduce new locks.
# Lives: -1 for every wrong pick.
#
# Surviva 2.1 (June 2026): picks required per matchday is now DYNAMIC —
# equal to the player's remaining lives at the start of the matchday.
# A player with 3 lives submits 3 picks; a player with 1 life submits 1.

# Legacy constant — kept for the DB index setup and settlement
# reference; NOT used anymore to enforce per-matchday pick count.
REQUIRED_PICKS_PER_MATCHDAY = 3

# Legacy per-outcome map kept for retro-compat helpers (used by the
# short-lived summary endpoints).
_OUTCOME_HOME = {"1": "W", "X": "D", "2": "L"}
_OUTCOME_AWAY = {"1": "L", "X": "D", "2": "W"}


def _team_outcomes_for_pick(pick: str) -> Tuple[str, str]:
    """Legacy helper — returns (home_outcome, away_outcome) for a 1/X/2 pick."""
    return _OUTCOME_HOME[pick], _OUTCOME_AWAY[pick]


def _team_locked_by_correct_pick(pick: str, home_team: str, away_team: str) -> Optional[str]:
    """When a pick is correct, return the *team* that must be locked.

    Returns ``None`` for pick "X" (draws never lock any team — exception
    granted per product rules).
    """
    if pick == "1":
        return home_team
    if pick == "2":
        return away_team
    return None  # "X" → no lock


def _pick_uses_locked_team(
    pick: str, home_team: str, away_team: str, locked_teams: set,
) -> Optional[str]:
    """Return the offending team name if the pick would re-use a locked
    team, otherwise ``None``.

    Rules:
      • pick "1" → home_team must be free
      • pick "2" → away_team must be free
      • pick "X" → always free (draws don't consume teams)
    """
    if pick == "1" and home_team in locked_teams:
        return home_team
    if pick == "2" and away_team in locked_teams:
        return away_team
    return None


def _fixture_fully_locked(home_team: str, away_team: str, locked_teams: set) -> bool:
    """Concession trigger: both teams of the fixture are already locked."""
    return home_team in locked_teams and away_team in locked_teams


def _pick_correct(pick: str, home_score: int, away_score: int) -> bool:
    """Return True if *pick* matches the final score of the fixture."""
    if pick == "1":
        return home_score > away_score
    if pick == "2":
        return home_score < away_score
    if pick == "X":
        return home_score == away_score
    return False


# =========================================================================
# Pydantic models
# =========================================================================

class TournamentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    season: str = Field(default="2026-27", max_length=10)
    initial_lives: int = Field(default=DEFAULT_LIVES, ge=1, le=10)
    # Matchday from which the tournament starts. All previous matchdays
    # are ignored (useful when a season is already in progress or when a
    # new Round starts after a previous tournament has ended).
    start_matchday: int = Field(default=1, ge=1, le=38)


class JoinIn(BaseModel):
    invite_code: str

    @field_validator("invite_code")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class PickItem(BaseModel):
    """A single pick inside a 3-picks matchday submission."""
    home_team: str
    away_team: str
    pick: str = Field(pattern=r"^[1X2]$")


class PicksSubmit(BaseModel):
    """Bulk submit of the caller's Surviva-v2 picks for a matchday.

    Rules (v2.1 — dynamic pick count):
      • Number of picks = participant's ``lives_left`` at submit time
      • each pick on a DIFFERENT fixture of the matchday
      • pick "1" / "2" cannot target an already-locked team, UNLESS the
        fixture has BOTH teams locked (concession)
      • correct picks with sign "1"/"2" add the winning team to the
        player's locked_teams set; correct picks with sign "X" do NOT lock
    """
    picks: List[PickItem] = Field(min_length=1,
                                  max_length=MAX_PICKS_PER_MATCHDAY)


# Legacy single-pick model kept for retro-compat helpers (unused as of v2).
class PickSubmit(BaseModel):
    home_team: str
    away_team: str
    pick: str = Field(pattern=r"^[1X2]$")


class MatchdaySettle(BaseModel):
    """Settle a matchday by providing per-fixture results.

    Postponed matches can be marked with ``postponed=True`` — they will neither
    cost lives nor grant blocked signs, and the pick remains pending until the
    admin re-settles the matchday with the updated results.
    """
    results: List[dict] = Field(default_factory=list)


class FixturePatch(BaseModel):
    """Body for editing a single fixture inside an existing matchday.

    Used by admins to handle scheduled postponements (``postponed_before=True``)
    or minor calendar fixes (rename teams).
    """
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    postponed_before: Optional[bool] = None


# =========================================================================
# Indexes + router factory
# =========================================================================

async def ensure_indexes(db) -> None:
    try:
        await db.sv_tournaments.create_index("id", unique=True)
        await db.sv_tournaments.create_index("invite_code", unique=True)
        await db.sv_invites.create_index("code", unique=True)
        await db.sv_invites.create_index([("tournament_id", 1), ("used_by_user_id", 1)])
        await db.sv_participants.create_index(
            [("tournament_id", 1), ("user_id", 1)], unique=True,
        )
        await db.sv_matchdays.create_index(
            [("tournament_id", 1), ("matchday", 1)], unique=True,
        )
        # Drop v1 unique index (t, md, user) if present — v2 allows 3 picks
        # per (t, md, user), one per fixture.
        try:
            existing = await db.sv_picks.index_information()
            for name, spec in existing.items():
                if name == "_id_":
                    continue
                keys = tuple(k for k, _ in spec.get("key", []))
                if keys == (
                    "tournament_id", "matchday_id", "user_id",
                ) and spec.get("unique"):
                    await db.sv_picks.drop_index(name)
        except Exception:
            logger.exception("Failed to inspect/drop legacy sv_picks index")
        # Surviva 2.0 v2: a player submits UP TO REQUIRED_PICKS_PER_MATCHDAY
        # picks per matchday, each on a distinct fixture. Uniqueness is
        # therefore per fixture-key inside the matchday.
        await db.sv_picks.create_index(
            [("tournament_id", 1), ("matchday_id", 1), ("user_id", 1),
             ("fixture_key", 1)],
            unique=True,
        )
        await db.sv_picks.create_index([("tournament_id", 1), ("user_id", 1)])
    except Exception:
        logger.exception("Failed to create Surviva indexes")


def build_router(
    db,
    current_user: Callable,
    require_admin: Callable,
    display_name: Callable,
) -> APIRouter:
    router = APIRouter(prefix="/sv")

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _gen_code(length: int = 6) -> str:
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

    async def _get_tournament(tid: str) -> dict:
        t = await db.sv_tournaments.find_one({"id": tid}, {"_id": 0})
        if not t:
            raise HTTPException(status_code=404, detail="Torneo non trovato")
        return t

    async def _get_participant(tid: str, uid: str) -> Optional[dict]:
        return await db.sv_participants.find_one(
            {"tournament_id": tid, "user_id": uid}, {"_id": 0},
        )

    async def _require_participant(tid: str, uid: str) -> dict:
        p = await _get_participant(tid, uid)
        if not p:
            raise HTTPException(status_code=403, detail="Non sei iscritto a questo torneo")
        return p

    async def _require_tournament_admin(tid: str, user: dict) -> dict:
        t = await _get_tournament(tid)
        if user["role"] != "admin" and user["id"] != t.get("admin_user_id"):
            raise HTTPException(status_code=403, detail="Solo l'admin del torneo")
        return t

    async def _fixtures_for_matchday(season: str, matchday: int) -> List[dict]:
        """Read the fixtures list from the shared ``sal_calendar`` collection.

        Fixtures with ``excluded=True`` (admin excluded pre-round) are
        skipped so users can never select them.
        """
        cursor = db.sal_calendar.find(
            {"season": season, "matchday": matchday, "excluded": {"$ne": True}},
            {"_id": 0, "home_team": 1, "away_team": 1, "kickoff_iso": 1},
        )
        return [f async for f in cursor]

    async def _auto_populate_matchdays(
        tid: str, season: str, start_matchday: int = 1,
    ) -> int:
        """Create ``sv_matchdays`` docs for every matchday available in the
        season calendar. Idempotent — existing matchdays are skipped.

        ``start_matchday`` limits creation to matchdays >= ``start_matchday``,
        which is essential for tournament rollover (Round 2 starts right after
        the matchday that closed Round 1).

        Returns the number of matchdays created.
        """
        # Distinct matchdays available for this season
        mds = await db.sal_calendar.distinct("matchday", {"season": season})
        created = 0
        for md in sorted(int(x) for x in mds):
            if md < int(start_matchday):
                continue
            existing = await db.sv_matchdays.find_one(
                {"tournament_id": tid, "matchday": md}, {"id": 1, "_id": 0},
            )
            if existing:
                continue
            fixtures = await _fixtures_for_matchday(season, md)
            # Add per-fixture postponement flag (``postponed_before``) so
            # admins can hide/remove specific matches (e.g. scheduled
            # postponements) without breaking picks referencing the fixture.
            fixtures_norm: List[dict] = []
            for f in fixtures:
                fixtures_norm.append({
                    "home_team": f.get("home_team"),
                    "away_team": f.get("away_team"),
                    "kickoff_iso": f.get("kickoff_iso"),
                    "postponed_before": False,
                })
            first_kick = None
            for f in fixtures_norm:
                k = f.get("kickoff_iso")
                if k and (first_kick is None or k < first_kick):
                    first_kick = k
            await db.sv_matchdays.insert_one({
                "id": str(uuid.uuid4()),
                "tournament_id": tid,
                "matchday": md,
                "season": season,
                "status": "open",  # open → locked (after first kickoff) → settled
                "kickoff_first": first_kick,
                "fixtures": fixtures_norm,
                "created_at": _now(),
                "settled_at": None,
            })
            created += 1
        return created

    def _locked_teams(p: Optional[dict]) -> List[str]:
        if not p:
            return []
        return list(p.get("locked_teams") or [])

    def _blocked_dict(p: Optional[dict]) -> List[dict]:
        """Legacy alias: returns [] under v2 rules."""
        return []

    async def _tournament_dict(t: dict, viewer: Optional[dict] = None) -> dict:
        players = await db.sv_participants.count_documents({"tournament_id": t["id"]})
        alive = await db.sv_participants.count_documents(
            {"tournament_id": t["id"], "eliminated_at": None},
        )
        is_admin = bool(
            viewer and (viewer["role"] == "admin" or viewer["id"] == t.get("admin_user_id"))
        )
        joined = False
        if viewer:
            joined = bool(await _get_participant(t["id"], viewer["id"]))
        return {
            "id": t["id"],
            "name": t["name"],
            "season": t.get("season", "2026-27"),
            "status": t.get("status", "open"),
            "admin_user_id": t.get("admin_user_id"),
            "initial_lives": t.get("initial_lives", DEFAULT_LIVES),
            "start_matchday": int(t.get("start_matchday") or 1),
            "current_matchday": t.get("current_matchday", 1),
            "invite_code": t.get("invite_code"),
            "created_at": t.get("created_at"),
            "finished_at": t.get("finished_at"),
            "players_total": players,
            "players_alive": alive,
            "is_admin": is_admin,
            "joined": joined,
            "archived": bool(t.get("archived", False)),
            "previous_tournament_id": t.get("previous_tournament_id"),
            "next_tournament_id": t.get("next_tournament_id"),
        }

    # ------------------------------------------------------------------
    # Tournaments — CRUD + join
    # ------------------------------------------------------------------

    async def _gen_unique_code() -> str:
        for _ in range(50):
            code = _gen_code()
            if not await db.sv_tournaments.find_one({"invite_code": code}) \
                    and not await db.sv_invites.find_one({"code": code}):
                return code
        raise HTTPException(status_code=500, detail="Impossibile generare un codice univoco")

    async def _spawn_tournament(
        *,
        admin_user_id: str,
        name: str,
        season: str,
        initial_lives: int,
        start_matchday: int,
        previous_tournament_id: Optional[str] = None,
    ) -> dict:
        """Create a fresh tournament doc + unique invite + auto-populate the
        matchdays from ``start_matchday`` onwards. Shared between the manual
        ``POST /tournaments`` and the auto-rollover triggered on settlement.

        Also auto-enrols every admin as a participant (multi-admin support)
        so that every subsequent Round always has the admin(s) available
        as players without requiring the invite dance.
        """
        code = await _gen_unique_code()
        tid = str(uuid.uuid4())
        now = _now()
        doc = {
            "id": tid,
            "name": name,
            "season": season,
            "status": "open",
            "admin_user_id": admin_user_id,
            "initial_lives": initial_lives,
            "start_matchday": int(start_matchday),
            "current_matchday": int(start_matchday),
            "invite_code": code,
            "created_at": now,
            "finished_at": None,
            "previous_tournament_id": previous_tournament_id,
            "next_tournament_id": None,
        }
        await db.sv_tournaments.insert_one(doc)
        # Initial single-use invite
        await db.sv_invites.insert_one({
            "id": str(uuid.uuid4()),
            "tournament_id": tid,
            "code": code,
            "used_by_user_id": None,
            "used_at": None,
            "created_at": now,
            "created_by": admin_user_id,
            "revoked_at": None,
        })
        # Auto-populate matchdays from calendar (>= start_matchday)
        created = await _auto_populate_matchdays(tid, season, start_matchday)
        logger.info(
            "Surviva tournament %s created — %d matchdays populated (start=%s)",
            tid, created, start_matchday,
        )
        # Auto-enrol EVERY admin as a participant so they can play any Round
        # (including auto-rollover Rounds) without needing the invite code.
        admin_users = [u async for u in db.users.find(
            {"role": "admin"}, {"_id": 0, "id": 1, "username": 1, "email": 1},
        )]
        for adm in admin_users:
            existing = await db.sv_participants.find_one({
                "tournament_id": tid, "user_id": adm["id"],
            })
            if existing:
                continue
            await db.sv_participants.insert_one({
                "tournament_id": tid,
                "user_id": adm["id"],
                "nickname": display_name(adm),
                "lives_left": int(initial_lives),
                "locked_teams": [],
                "blocked_signs": [],
                "eliminated_at": None,
                "joined_at": now,
            })
        # Auto-create a draft bonus config for the Survival bonus type
        # (exact_score). Admin must complete the Big Match later, but the
        # slot is now visible to players from day one.
        try:
            draft = await ensure_bonus_draft(
                db, season=season, matchday=int(start_matchday),
                bonus_type="exact_score", created_by=admin_user_id,
            )
            if draft:
                logger.info(
                    "Surviva %s → bonus draft ensured (md=%s, id=%s)",
                    tid, draft.get("matchday"), draft.get("id"),
                )
        except Exception:
            logger.exception("Failed to ensure bonus draft for surviva %s", tid)
        return doc

    @router.post("/tournaments")
    async def create_tournament(
        data: TournamentCreate, user: dict = Depends(require_admin),
    ):
        doc = await _spawn_tournament(
            admin_user_id=user["id"],
            name=data.name,
            season=data.season,
            initial_lives=data.initial_lives,
            start_matchday=data.start_matchday,
        )
        # Auto-enrol ALL current admins as participants (multi-admin support).
        admin_users = [u async for u in db.users.find(
            {"role": "admin"}, {"_id": 0, "id": 1, "username": 1, "email": 1},
        )]
        for adm in admin_users:
            existing = await db.sv_participants.find_one({
                "tournament_id": doc["id"], "user_id": adm["id"],
            })
            if existing:
                continue
            await db.sv_participants.insert_one({
                "tournament_id": doc["id"],
                "user_id": adm["id"],
                "nickname": display_name(adm),
                "lives_left": data.initial_lives,
                "locked_teams": [],
                "blocked_signs": [],  # legacy field (v1), always empty in v2
                "eliminated_at": None,
                "joined_at": _now(),
            })
        return await _tournament_dict(doc, user)

    @router.get("/tournaments")
    async def list_tournaments(
        user: dict = Depends(current_user),
        include_finished: bool = False,
    ):
        q: dict = {}
        if not include_finished:
            q["status"] = {"$ne": "finished"}
        cursor = db.sv_tournaments.find(q, {"_id": 0}).sort("created_at", -1)
        out = []
        async for t in cursor:
            out.append(await _tournament_dict(t, user))
        return out

    @router.get("/tournaments/history")
    async def tournaments_history(user: dict = Depends(current_user)):
        """Finished tournaments — always visible to everyone (public archive)."""
        cursor = db.sv_tournaments.find(
            {"status": "finished"}, {"_id": 0},
        ).sort("finished_at", -1)
        out = []
        async for t in cursor:
            out.append(await _tournament_dict(t, user))
        return out

    @router.get("/tournaments/{tid}")
    async def get_tournament(tid: str, user: dict = Depends(current_user)):
        t = await _get_tournament(tid)
        return await _tournament_dict(t, user)

    # ------------------------------------------------------------------
    # Single-use invites (admin only) — mirrors ScoreAndLive
    # ------------------------------------------------------------------

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

    @router.get("/tournaments/{tid}/invites")
    async def list_invites(tid: str, user: dict = Depends(current_user)):
        await _require_tournament_admin(tid, user)
        invites = [
            inv async for inv in db.sv_invites.find(
                {"tournament_id": tid}, {"_id": 0},
            ).sort("created_at", -1)
        ]
        return [await _invite_dict(i) for i in invites]

    @router.post("/tournaments/{tid}/invites")
    async def create_invite(tid: str, user: dict = Depends(current_user)):
        """Generate a new single-use invite code for this tournament.

        Each code is unique (across all tournaments) and can be redeemed by
        exactly one player. Admins can create as many codes as needed.
        """
        await _require_tournament_admin(tid, user)
        code = await _gen_unique_code()
        now = _now()
        doc = {
            "id": str(uuid.uuid4()),
            "tournament_id": tid,
            "code": code,
            "used_by_user_id": None,
            "used_at": None,
            "created_at": now,
            "created_by": user["id"],
            "revoked_at": None,
        }
        await db.sv_invites.insert_one(doc)
        return await _invite_dict(doc)

    @router.delete("/tournaments/{tid}/invites/{invite_id}")
    async def revoke_invite(tid: str, invite_id: str, user: dict = Depends(current_user)):
        """Revoke an unused invite. Already-used invites cannot be revoked."""
        await _require_tournament_admin(tid, user)
        inv = await db.sv_invites.find_one(
            {"id": invite_id, "tournament_id": tid}, {"_id": 0},
        )
        if not inv:
            raise HTTPException(status_code=404, detail="Invito non trovato")
        if inv.get("used_by_user_id"):
            raise HTTPException(
                status_code=400,
                detail="Impossibile revocare: invito già utilizzato",
            )
        if inv.get("revoked_at"):
            return await _invite_dict(inv)
        await db.sv_invites.update_one(
            {"id": invite_id}, {"$set": {"revoked_at": _now()}},
        )
        inv["revoked_at"] = _now()
        return await _invite_dict(inv)

    @router.post("/tournaments/{tid}/archive")
    async def archive_tournament(tid: str, archived: bool = True, user: dict = Depends(require_admin)):
        """Archive/unarchive a FINISHED tournament (keeps history, hides from active list)."""
        t = await _get_tournament(tid)
        if archived and t.get("status") != "finished":
            raise HTTPException(status_code=400, detail="Solo i tornei conclusi possono essere archiviati")
        await db.sv_tournaments.update_one({"id": tid}, {"$set": {"archived": bool(archived)}})
        t["archived"] = bool(archived)
        return await _tournament_dict(t, user)

    @router.delete("/tournaments/{tid}")
    async def delete_tournament(tid: str, user: dict = Depends(require_admin)):
        t = await _get_tournament(tid)
        _ = t
        await db.sv_tournaments.delete_one({"id": tid})
        await db.sv_invites.delete_many({"tournament_id": tid})
        await db.sv_participants.delete_many({"tournament_id": tid})
        await db.sv_matchdays.delete_many({"tournament_id": tid})
        await db.sv_picks.delete_many({"tournament_id": tid})
        return {"ok": True}

    @router.post("/tournaments/{tid}/kick/{user_id}")
    async def kick_from_tournament(
        tid: str, user_id: str, user: dict = Depends(require_admin),
    ):
        """Hard-remove a player from a Survival tournament: removes participant
        record, all their picks across all matchdays. Irreversible."""
        t = await _get_tournament(tid)
        if t.get("admin_user_id") == user_id:
            raise HTTPException(
                status_code=400,
                detail="Impossibile escludere l'admin del torneo",
            )
        target = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
        if not target:
            raise HTTPException(status_code=404, detail="Utente non trovato")
        part = await db.sv_participants.find_one(
            {"tournament_id": tid, "user_id": user_id}
        )
        if not part:
            raise HTTPException(
                status_code=404,
                detail="Il giocatore non è iscritto a questo torneo",
            )
        deleted_picks = await db.sv_picks.delete_many(
            {"tournament_id": tid, "user_id": user_id}
        )
        await db.sv_participants.delete_many(
            {"tournament_id": tid, "user_id": user_id}
        )
        return {
            "ok": True,
            "deleted_picks": deleted_picks.deleted_count,
            "kicked_user_id": user_id,
        }

    @router.post("/tournaments/join")
    async def join_tournament(data: JoinIn, user: dict = Depends(current_user)):
        code = data.invite_code
        invite = await db.sv_invites.find_one({"code": code})
        if not invite:
            raise HTTPException(status_code=404, detail="Codice invito non valido")
        if invite.get("revoked_at"):
            raise HTTPException(status_code=410, detail="Codice invito revocato")
        if invite.get("used_by_user_id") and invite["used_by_user_id"] != user["id"]:
            raise HTTPException(status_code=410, detail="Codice invito già utilizzato")
        tid = invite["tournament_id"]
        t = await _get_tournament(tid)
        if t.get("status") == "finished":
            raise HTTPException(status_code=400, detail="Torneo già concluso")

        # Idempotent: allow re-entry by the same user
        existing = await _get_participant(tid, user["id"])
        if existing:
            return await _tournament_dict(t, user)

        # Refuse joining if the tournament has advanced past its first
        # matchday to prevent late-joiners from having an unfair advantage.
        start_md = int(t.get("start_matchday") or 1)
        if int(t.get("current_matchday") or start_md) > start_md:
            raise HTTPException(
                status_code=400,
                detail="Torneo già iniziato: iscrizioni chiuse.",
            )

        # Claim invite + create participant
        await db.sv_invites.update_one(
            {"id": invite["id"]},
            {"$set": {"used_by_user_id": user["id"], "used_at": _now()}},
        )
        await db.sv_participants.insert_one({
            "tournament_id": tid,
            "user_id": user["id"],
            "nickname": display_name(user),
            "lives_left": t.get("initial_lives", DEFAULT_LIVES),
            "locked_teams": [],
            "blocked_signs": [],  # legacy (v1)
            "eliminated_at": None,
            "joined_at": _now(),
        })
        return await _tournament_dict(t, user)

    @router.get("/tournaments/{tid}/participants")
    async def list_participants(tid: str, user: dict = Depends(current_user)):
        await _get_tournament(tid)
        cursor = db.sv_participants.find({"tournament_id": tid}, {"_id": 0})
        rows = []
        async for p in cursor:
            rows.append({
                "user_id": p["user_id"],
                "nickname": p["nickname"],
                "lives_left": p.get("lives_left", 0),
                "eliminated_at": p.get("eliminated_at"),
                "locked_teams": list(p.get("locked_teams") or []),
                "blocked_signs": [],  # legacy compat
            })
        # Sort: alive first (by lives desc), then eliminated by date desc
        rows.sort(key=lambda r: (
            r["eliminated_at"] is not None,
            -r["lives_left"],
            r["nickname"].lower(),
        ))
        return rows

    # ------------------------------------------------------------------
    # Matchdays + picks
    # ------------------------------------------------------------------

    def _md_is_locked(md: dict) -> bool:
        """A matchday is locked for picks once the first kickoff is past."""
        k = md.get("kickoff_first")
        if not k:
            return md.get("status") in {"locked", "settled"}
        try:
            dt = datetime.fromisoformat(k.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= dt
        except Exception:
            return md.get("status") in {"locked", "settled"}

    async def _valid_calendar_keys(season, matchday) -> set:
        """Set of (home,away) fixtures that CURRENTLY exist in the season
        calendar and are NOT excluded. Used to filter playable fixtures live,
        so a match deleted/excluded from the calendar disappears from the
        pick screen even if an old matchday snapshot still references it."""
        keys: set = set()
        if not season or matchday is None:
            return keys
        async for cf in db.sal_calendar.find(
            {"season": season, "matchday": matchday, "excluded": {"$ne": True}},
            {"_id": 0, "home_team": 1, "away_team": 1},
        ):
            keys.add(((cf.get("home_team") or "").strip().lower(),
                      (cf.get("away_team") or "").strip().lower()))
        return keys

    def _key(f: dict):
        return ((f.get("home_team") or "").strip().lower(),
                (f.get("away_team") or "").strip().lower())

    async def _effective_fixtures(md: dict) -> List[dict]:
        """Playable fixtures of a matchday: snapshot minus excluded/postponed,
        AND (for non-settled matchdays) reconciled live against the season
        calendar so deleted/excluded matches never remain playable."""
        out = [f for f in md.get("fixtures", [])
               if not f.get("excluded") and not f.get("postponed_before")]
        if md.get("status") != "settled":
            valid = await _valid_calendar_keys(md.get("season"), md.get("matchday"))
            out = [f for f in out if _key(f) in valid]
        return out

    async def _matchday_dict(
        md: dict, viewer_id: Optional[str] = None, valid_keys: Optional[set] = None,
    ) -> dict:
        locked = _md_is_locked(md)
        settled = md.get("status") == "settled"
        # Base filter: drop fixtures flagged excluded/postponed in the snapshot.
        fixtures_out = [
            f for f in md.get("fixtures", [])
            if not f.get("excluded") and not f.get("postponed_before")
        ]
        # LIVE reconciliation for non-settled matchdays: only keep fixtures
        # that still exist (and are not excluded) in the season calendar.
        if not settled:
            if valid_keys is None:
                valid_keys = await _valid_calendar_keys(
                    md.get("season"), md.get("matchday"),
                )
            fixtures_out = [
                f for f in fixtures_out
                if ((f.get("home_team") or "").strip().lower(),
                    (f.get("away_team") or "").strip().lower()) in valid_keys
            ]
        my_picks_count = 0
        picks_required = REQUIRED_PICKS_PER_MATCHDAY  # fallback
        my_big_match_bonus_won = False
        my_big_match_pick: Optional[dict] = None
        if viewer_id:
            my_picks_count = await db.sv_picks.count_documents({
                "tournament_id": md["tournament_id"],
                "matchday_id": md["id"],
                "user_id": viewer_id,
            })
            # v2.1 — picks required = viewer's current lives_left in this
            # tournament (0 if not participating / already eliminated).
            part = await db.sv_participants.find_one(
                {"tournament_id": md["tournament_id"], "user_id": viewer_id},
                {"_id": 0, "lives_left": 1, "eliminated_at": 1},
            )
            if part and not part.get("eliminated_at"):
                picks_required = max(0, int(part.get("lives_left") or 0))
            else:
                picks_required = 0

            # Did this viewer earn the Big Match bonus for this matchday?
            bonus_users = md.get("big_match_bonus_users") or []
            if viewer_id in bonus_users:
                my_big_match_bonus_won = True

            # Their submitted exact_score pick for the current Big Match
            # (surfaced in the pick screen so they know if they already
            # picked or need to go to the Bonus section).
            bp = await db.bonus_picks.find_one(
                {"game": "survival",
                 "bonus_type": "exact_score",
                 "season": md.get("season"),
                 "matchday": md["matchday"],
                 "user_id": viewer_id},
                {"_id": 0, "pick": 1, "subscription_id": 1},
            )
            if bp:
                my_big_match_pick = bp.get("pick")

        # Big Match info (from the active exact_score Bonus config for
        # this season+matchday). Surfaced so the pick screen can show a
        # "🎯 Bonus Big Match" CTA and the summary/history can show which
        # game is the Big Match of the giornata.
        big_match_info: Optional[dict] = None
        if md.get("season") and md.get("matchday"):
            bm_cfg = await db.bonus_configs.find_one(
                {"season": md["season"], "matchday": md["matchday"],
                 "bonus_type": "exact_score"},
                {"_id": 0, "id": 1, "big_match": 1, "settled_at": 1},
            )
            if bm_cfg and bm_cfg.get("big_match"):
                bm = bm_cfg["big_match"]
                big_match_info = {
                    "config_id": bm_cfg.get("id"),
                    "home_team": bm.get("home_team"),
                    "away_team": bm.get("away_team"),
                    "kickoff_iso": bm.get("kickoff_iso"),
                    "settled": bool(bm_cfg.get("settled_at")),
                }

        return {
            "id": md["id"],
            "tournament_id": md["tournament_id"],
            "matchday": md["matchday"],
            "season": md.get("season"),
            "status": md.get("status", "open"),
            "kickoff_first": md.get("kickoff_first"),
            "fixtures": fixtures_out,
            "locked": locked,
            "settled": md.get("status") == "settled",
            "tie_break": bool(md.get("tie_break")),
            "my_picks_count": my_picks_count,
            "picks_required": picks_required,
            "big_match": big_match_info,
            "my_big_match_pick": my_big_match_pick,
            "my_big_match_bonus_won": my_big_match_bonus_won,
            "big_match_bonus_count": len(md.get("big_match_bonus_users") or []),
        }

    @router.get("/tournaments/{tid}/matchdays")
    async def list_matchdays(tid: str, user: dict = Depends(current_user)):
        t = await _get_tournament(tid)
        season = t.get("season")
        # Batch-load calendar keys once, grouped by matchday, to avoid one
        # sal_calendar query per matchday inside _matchday_dict.
        cal_by_md: Dict[int, set] = {}
        async for cf in db.sal_calendar.find(
            {"season": season, "excluded": {"$ne": True}},
            {"_id": 0, "matchday": 1, "home_team": 1, "away_team": 1},
        ):
            cal_by_md.setdefault(int(cf["matchday"]), set()).add(
                ((cf.get("home_team") or "").strip().lower(),
                 (cf.get("away_team") or "").strip().lower())
            )
        cursor = db.sv_matchdays.find({"tournament_id": tid}, {"_id": 0}).sort("matchday", 1)
        rows = []
        async for md in cursor:
            rows.append(await _matchday_dict(
                md, user["id"],
                valid_keys=cal_by_md.get(int(md.get("matchday") or 0), set()),
            ))
        return rows

    @router.get("/tournaments/{tid}/matchdays/current")
    async def current_matchday(tid: str, user: dict = Depends(current_user)):
        t = await _get_tournament(tid)
        md = await db.sv_matchdays.find_one({
            "tournament_id": tid,
            "matchday": t.get("current_matchday", 1),
        }, {"_id": 0})
        if not md:
            raise HTTPException(status_code=404, detail="Nessuna giornata in corso")
        return await _matchday_dict(md, user["id"])

    @router.get("/tournaments/{tid}/matchdays/{md_id}/my-picks")
    async def my_picks(tid: str, md_id: str, user: dict = Depends(current_user)):
        """Return the picks the caller has submitted for a matchday.

        The ``required`` value is dynamic: equal to the caller's remaining
        lives at read time (0 if not participating / eliminated).
        """
        p = await _require_participant(tid, user["id"])
        picks = [pk async for pk in db.sv_picks.find(
            {"tournament_id": tid, "matchday_id": md_id, "user_id": user["id"]},
            {"_id": 0},
        )]
        required = 0
        if not p.get("eliminated_at"):
            required = max(0, int(p.get("lives_left") or 0))
        return {"picks": picks, "required": required}

    @router.get("/tournaments/{tid}/participants/{user_id}/picks")
    async def participant_picks(
        tid: str, user_id: str, user: dict = Depends(current_user),
    ):
        """Return the target participant's picks per matchday.

        Visibility rule (3-TUTTI, cross-game): each matchday's picks are
        included ONLY when the global deadline for that matchday has passed
        (or when the matchday has been settled). Otherwise the entry is
        returned with ``hidden: True`` and no pick data.

        The caller sees own picks always (no gate for self).

        Anyone logged in can view — eliminated participants and past
        players must still be able to consult the classifica and pick
        history of any tournament they took part in.
        """
        # No participant check: eliminated / past participants must be
        # able to see the classifica and everyone's history.
        target = await db.sv_participants.find_one(
            {"tournament_id": tid, "user_id": user_id}, {"_id": 0},
        )
        if not target:
            raise HTTPException(status_code=404, detail="Partecipante non trovato")
        t = await _get_tournament(tid)
        season = t.get("season") or "2026-27"
        is_self = user_id == user["id"]

        matchdays = [
            md async for md in db.sv_matchdays.find(
                {"tournament_id": tid},
                {"_id": 0, "id": 1, "matchday": 1, "status": 1, "fixtures": 1, "big_match_bonus_users": 1},
            ).sort("matchday", 1)
        ]
        # Group picks by matchday_id for one query
        picks_by_md: Dict[str, List[dict]] = {}
        async for pk in db.sv_picks.find(
            {"tournament_id": tid, "user_id": user_id}, {"_id": 0},
        ):
            picks_by_md.setdefault(pk["matchday_id"], []).append(pk)

        # Batch-load ALL deadlines for the season once (avoids 38 sequential
        # DB round-trips → the picks modal now opens instantly).
        dl_map: Dict[int, Any] = {}
        async for d in db.matchday_deadlines.find(
            {"season": season}, {"_id": 0, "matchday": 1, "deadline_at": 1},
        ):
            parsed = _parse_stored(d.get("deadline_at"))
            if parsed is not None:
                dl_map[int(d["matchday"])] = parsed
        now_dt = datetime.now(timezone.utc)

        target_lives = int(target.get("lives_left") or 0)
        target_locks = set(target.get("locked_teams") or [])

        # Batch-load valid calendar keys per matchday (deleted/excluded
        # matches are filtered out of preview defaults too).
        cal_by_md: Dict[int, set] = {}
        async for cf in db.sal_calendar.find(
            {"season": season, "excluded": {"$ne": True}},
            {"_id": 0, "matchday": 1, "home_team": 1, "away_team": 1},
        ):
            cal_by_md.setdefault(int(cf["matchday"]), set()).add(
                ((cf.get("home_team") or "").strip().lower(),
                 (cf.get("away_team") or "").strip().lower())
            )

        def _preview_defaults(md: dict, existing: List[dict], valid_keys: set) -> List[dict]:
            """Compute (non-persisted) default picks for display AFTER the
            deadline but BEFORE settlement — same rule as the settle-time
            auto-fill: first fixture first, sign respecting team blocks."""
            required = max(0, target_lives)
            needed = required - len(existing)
            if needed <= 0:
                return []
            picked_keys = {(pk.get("home_team"), pk.get("away_team")) for pk in existing}
            out_p: List[dict] = []
            for fx in md.get("fixtures", []):
                if needed <= 0:
                    break
                key = (fx.get("home_team"), fx.get("away_team"))
                nk = ((fx.get("home_team") or "").strip().lower(),
                      (fx.get("away_team") or "").strip().lower())
                if key in picked_keys or fx.get("excluded") or fx.get("postponed_before"):
                    continue
                if nk not in valid_keys:
                    continue
                home, away = fx.get("home_team"), fx.get("away_team")
                if home not in target_locks:
                    sign, conc = "1", False
                elif away not in target_locks:
                    sign, conc = "2", False
                else:
                    sign, conc = "X", True
                out_p.append({
                    "home_team": home, "away_team": away, "pick": sign,
                    "concession": conc, "correct": None,
                    "auto_generated": True, "preview": True,
                })
                needed -= 1
            return out_p

        out: List[dict] = []
        for md in matchdays:
            md_num = md["matchday"]
            settled = md.get("status") == "settled"
            dl = dl_map.get(md_num)
            deadline_passed = dl is not None and now_dt >= dl
            visible = is_self or settled or deadline_passed
            entry: Dict[str, Any] = {
                "matchday": md_num,
                "matchday_id": md["id"],
                "status": md.get("status", "open"),
                "settled": settled,
                "deadline_passed": deadline_passed,
                "hidden": not visible,
                "big_match_bonus_won": bool(
                    settled and user_id in (md.get("big_match_bonus_users") or [])
                ),
            }
            if visible:
                existing = picks_by_md.get(md["id"], [])
                # Vite a inizio giornata (cuoricini nel riepilogo): per una
                # giornata calcolata equivale al numero di giocate persistite
                # (le picks vengono auto-completate = vite a inizio giornata);
                # per la giornata corrente sono le vite attuali del giocatore.
                if settled:
                    entry["lives_at_start"] = len(existing)
                else:
                    entry["lives_at_start"] = max(0, target_lives)
                # After the deadline and before settlement, surface the
                # default picks the player WILL receive at calcolo, so the
                # giocata is visible like everyone else's (marked preview).
                if not settled and deadline_passed and target.get("eliminated_at") is None:
                    existing = existing + _preview_defaults(
                        md, existing, cal_by_md.get(int(md_num), set()),
                    )
                entry["picks"] = existing
            out.append(entry)

        # Snapshot participant state (lives, locked teams, elimination)
        participant_view = {
            "user_id": target["user_id"],
            "display_name": target.get("display_name"),
            "lives_left": target.get("lives_left", 0),
            "eliminated_at": target.get("eliminated_at"),
            "locked_teams": target.get("locked_teams") or [],
        }
        return {
            "participant": participant_view,
            "matchdays": out,
        }

    @router.get("/tournaments/{tid}/matchdays/{md_id}/my-pick")
    async def my_pick(tid: str, md_id: str, user: dict = Depends(current_user)):
        """Legacy single-pick endpoint. Kept for compat: returns the first pick."""
        await _require_participant(tid, user["id"])
        p = await db.sv_picks.find_one(
            {"tournament_id": tid, "matchday_id": md_id, "user_id": user["id"]},
            {"_id": 0},
        )
        return p or {"empty": True}

    @router.get("/tournaments/{tid}/locked-teams")
    async def my_locked_teams(tid: str, user: dict = Depends(current_user)):
        """Return the caller's locked teams for the tournament + their lives."""
        p = await _require_participant(tid, user["id"])
        return {
            "locked_teams": _locked_teams(p),
            "lives_left": p.get("lives_left", 0),
        }

    @router.get("/tournaments/{tid}/blocked-signs")
    async def my_blocked_signs(tid: str, user: dict = Depends(current_user)):
        """Legacy endpoint (v1). In v2 rules this always returns an empty list."""
        p = await _require_participant(tid, user["id"])
        return {"blocked_signs": [], "lives_left": p.get("lives_left", 0)}

    @router.post("/tournaments/{tid}/matchdays/{md_id}/picks")
    async def submit_picks(
        tid: str, md_id: str, data: PicksSubmit, user: dict = Depends(current_user),
    ):
        """Submit the caller's picks for a matchday (Surviva 2.1 dynamic).

        The number of picks required equals the player's ``lives_left`` at
        submit time. Picks REPLACE any existing picks for the matchday
        (idempotent upsert). Full validation happens up-front — the write
        only occurs if ALL picks are legal.
        """
        p = await _require_participant(tid, user["id"])
        if p.get("eliminated_at"):
            raise HTTPException(status_code=403, detail="Sei stato eliminato dal torneo")
        # v2.1 — required picks == current lives_left
        required = max(0, int(p.get("lives_left") or 0))
        if required == 0:
            raise HTTPException(
                status_code=403,
                detail="Non hai vite disponibili per giocare questa giornata.",
            )
        if len(data.picks) != required:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Devi inviare esattamente {required} "
                    f"pronostic{'o' if required == 1 else 'i'} "
                    f"(uno per ogni vita rimasta), ne hai inviati {len(data.picks)}."
                ),
            )
        md = await db.sv_matchdays.find_one({"id": md_id, "tournament_id": tid}, {"_id": 0})
        if not md:
            raise HTTPException(status_code=404, detail="Giornata non trovata")
        if _md_is_locked(md):
            raise HTTPException(status_code=403, detail="Giornata chiusa: pronostici bloccati")

        # Global deadline gate (shared across all games).
        t = await _get_tournament(tid)
        season = t.get("season") or "2026-27"
        if await _global_deadline_passed(db, season, md["matchday"]):
            raise HTTPException(
                status_code=403,
                detail="Il timer di invio pronostici è scaduto per questa giornata.",
            )

        fixtures_by_key = {
            (f["home_team"], f["away_team"]): f for f in await _effective_fixtures(md)
        }
        locked_teams: set = set(p.get("locked_teams") or [])

        seen_keys: set = set()
        for i, pk in enumerate(data.picks, start=1):
            key = (pk.home_team, pk.away_team)
            if key not in fixtures_by_key:
                raise HTTPException(
                    status_code=400,
                    detail=f"Pronostico {i}: partita non in calendario "
                           f"({pk.home_team} vs {pk.away_team})",
                )
            if fixtures_by_key[key].get("postponed_before"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Pronostico {i}: partita rinviata, scegli un'altra partita.",
                )
            if key in seen_keys:
                raise HTTPException(
                    status_code=400,
                    detail=f"Pronostico {i}: hai già scelto questo match, "
                           f"gli {required} pronostici devono essere su match diversi.",
                )
            seen_keys.add(key)

            # Team-lock check with concession
            if not _fixture_fully_locked(pk.home_team, pk.away_team, locked_teams):
                offender = _pick_uses_locked_team(
                    pk.pick, pk.home_team, pk.away_team, locked_teams,
                )
                if offender:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Pronostico {i}: {offender} è già stata usata "
                            "correttamente in una giornata precedente. "
                            "Scegli un'altra squadra o cambia segno."
                        ),
                    )

        # All picks are legal — replace existing picks atomically.
        now = _now()
        await db.sv_picks.delete_many(
            {"tournament_id": tid, "matchday_id": md_id, "user_id": user["id"]},
        )
        docs = []
        for pk in data.picks:
            fkey = f"{pk.home_team}||{pk.away_team}"
            concession = _fixture_fully_locked(pk.home_team, pk.away_team, locked_teams)
            docs.append({
                "tournament_id": tid,
                "matchday_id": md_id,
                "matchday": md["matchday"],
                "user_id": user["id"],
                "nickname": p["nickname"],
                "home_team": pk.home_team,
                "away_team": pk.away_team,
                "fixture_key": fkey,
                "pick": pk.pick,
                "concession": concession,
                "correct": None,
                "lost_life": None,
                "created_at": now,
            })
        if docs:
            await db.sv_picks.insert_many(docs)
        return {"ok": True, "picks": len(docs)}

    # ---- Legacy 1-pick endpoint (deprecated by v2 rules) -----------------
    @router.post("/tournaments/{tid}/matchdays/{md_id}/pick")
    async def submit_pick(  # noqa: ARG001
        tid: str, md_id: str, data: PickSubmit, user: dict = Depends(current_user),
    ):
        """Deprecated: Surviva 2.0 v2 requires 3 picks. Use ``/picks``."""
        raise HTTPException(
            status_code=410,
            detail="Endpoint deprecato: Surviva 2.0 richiede 3 pronostici. "
                   "Usa POST /matchdays/{md_id}/picks",
        )

    # ------------------------------------------------------------------
    # Settlement + auto-progression
    # ------------------------------------------------------------------

    async def _auto_fill_default_picks(tid: str, md: dict) -> Dict[str, int]:
        """Auto-generate default picks for participants who submitted fewer
        than ``REQUIRED_PICKS_PER_MATCHDAY`` picks for this matchday.

        Rules for the default pick:
          • Iterate fixtures of the matchday in their natural order.
          • Skip fixtures already picked by the user, and skip
            excluded / postponed_before fixtures.
          • Choose the sign that respects team blocks:
              - "1" (home team) if home team is NOT locked
              - "2" (away team) if home is locked but away is NOT
              - "X" (draw) if BOTH teams are locked (concession)
          • Fill picks until the user reaches their ``lives_left``
            (Surviva 2.1 dynamic rule: 1 pick per remaining life).

        Returns a dict ``{"users_filled": int, "picks_created": int}``.

        Called at the start of settle so that inactive users are still
        penalised (losing a life if the default pick loses) instead of
        silently keeping all their lives.
        """
        picks_created = 0
        users_filled = 0
        fixtures = await _effective_fixtures(md)
        if not fixtures:
            return {"users_filled": 0, "picks_created": 0}
        participants = [p async for p in db.sv_participants.find(
            {"tournament_id": tid, "eliminated_at": None},
            {"_id": 0},
        )]
        for p in participants:
            uid = p["user_id"]
            existing = [pk async for pk in db.sv_picks.find(
                {"tournament_id": tid, "matchday_id": md["id"], "user_id": uid},
                {"_id": 0, "home_team": 1, "away_team": 1},
            )]
            # v2.1 — number of picks required is the player's current lives
            required = max(0, int(p.get("lives_left") or 0))
            needed = required - len(existing)
            if needed <= 0:
                continue
            picked_keys = {(pk["home_team"], pk["away_team"]) for pk in existing}
            locked_teams = set(p.get("locked_teams") or [])
            nickname = p.get("nickname") or (
                (await db.users.find_one({"id": uid}, {"_id": 0, "nickname": 1}))
                or {}
            ).get("nickname", "")
            new_docs = []
            for fx in fixtures:
                if needed <= 0:
                    break
                key = (fx.get("home_team"), fx.get("away_team"))
                if key in picked_keys:
                    continue
                if fx.get("excluded") or fx.get("postponed_before"):
                    continue
                home = fx["home_team"]
                away = fx["away_team"]
                # Choose sign respecting team locks
                if home not in locked_teams:
                    pick_sign = "1"
                    concession = False
                elif away not in locked_teams:
                    pick_sign = "2"
                    concession = False
                else:
                    pick_sign = "X"
                    concession = True
                new_docs.append({
                    "tournament_id": tid,
                    "matchday_id": md["id"],
                    "matchday": md["matchday"],
                    "user_id": uid,
                    "nickname": nickname,
                    "home_team": home,
                    "away_team": away,
                    "fixture_key": f"{home}||{away}",
                    "pick": pick_sign,
                    "concession": concession,
                    "correct": None,
                    "lost_life": None,
                    "created_at": _now(),
                    "auto_generated": True,
                })
                needed -= 1
            if new_docs:
                await db.sv_picks.insert_many(new_docs)
                picks_created += len(new_docs)
                users_filled += 1
        return {"users_filled": users_filled, "picks_created": picks_created}

    @router.post("/tournaments/{tid}/matchdays/{md_id}/settle")
    async def settle_matchday(
        tid: str, md_id: str, data: MatchdaySettle,
        user: dict = Depends(current_user),
    ):
        t = await _require_tournament_admin(tid, user)
        md = await db.sv_matchdays.find_one({"id": md_id, "tournament_id": tid}, {"_id": 0})
        if not md:
            raise HTTPException(status_code=404, detail="Giornata non trovata")

        # IDEMPOTENCY GUARD — running settle_matchday twice on the same
        # matchday would re-apply life deductions AND re-award the Big Match
        # bonus, silently inflating (or wrongly deflating) lives_left. We
        # refuse the second call with a clear message. To re-run legitimately,
        # an admin must first invoke the dedicated reset endpoint (roadmap).
        if md.get("status") == "settled":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Giornata {md['matchday']} già liquidata (settle_at={md.get('settled_at')}). "
                    "Ri-eseguire il calcolo ora falserebbe le vite e i bonus. "
                    "Per rifare il settle serve prima resettare la giornata (funzione da abilitare)."
                ),
            )

        # STEP 0 — Auto-generate default picks for inactive players.
        # Anyone who didn't submit the required number of picks receives
        # default picks (first-fixture-first, sign chosen respecting their
        # team blocks) so they can lose lives instead of freeriding.
        auto_stats = await _auto_fill_default_picks(tid, md)

        # Build a lookup: (home,away) → {home_score, away_score, postponed}
        results_by_key: Dict[Tuple[str, str], dict] = {}
        for r in data.results:
            k = (r.get("home_team"), r.get("away_team"))
            if not k[0] or not k[1]:
                continue
            results_by_key[k] = r

        # Iterate through every submitted pick — mark correct/wrong,
        # deduct lives, and add blocked signs on correct picks.
        picks_cur = db.sv_picks.find({"tournament_id": tid, "matchday_id": md_id})
        stats = {"settled": 0, "correct": 0, "wrong": 0, "postponed": 0}
        # We batch participant updates in-memory to avoid concurrent races.
        pending_participant_updates: Dict[str, dict] = {}
        eliminated_now: List[str] = []

        async for pk in picks_cur:
            key = (pk["home_team"], pk["away_team"])
            res = results_by_key.get(key)
            if not res or res.get("postponed"):
                # Postponed / no data: leave pick pending
                stats["postponed"] += 1
                continue
            hs = int(res.get("home_score") or 0)
            as_ = int(res.get("away_score") or 0)
            correct = _pick_correct(pk["pick"], hs, as_)
            await db.sv_picks.update_one(
                {"_id": pk["_id"]},
                {"$set": {
                    "correct": correct,
                    "lost_life": (not correct),
                    "home_score": hs,
                    "away_score": as_,
                    "settled_at": _now(),
                }},
            )
            stats["settled"] += 1
            uid = pk["user_id"]
            state = pending_participant_updates.setdefault(
                uid, {"life_delta": 0, "new_locks": []},
            )
            if correct:
                stats["correct"] += 1
                # A correct pick on a "concession" fixture (both teams
                # already locked) does NOT introduce new locks — otherwise
                # we would over-punish. Team locks apply only when the
                # player was actually forced to choose a fresh team.
                if not pk.get("concession"):
                    team_to_lock = _team_locked_by_correct_pick(
                        pk["pick"], pk["home_team"], pk["away_team"],
                    )
                    if team_to_lock:
                        state["new_locks"].append(team_to_lock)
            else:
                stats["wrong"] += 1
                state["life_delta"] -= 1

        # Snapshot the state of ALL currently-alive participants BEFORE we
        # apply life deductions — needed for the "pareggio → resurrezione"
        # rule: if EVERY remaining alive player dies in the same matchday,
        # we restore them to this pre-settle state and force them to
        # replay the next matchday (repeat until only one survives).
        pre_alive_snapshot: List[dict] = [
            {
                "user_id": p["user_id"],
                "lives_left": int(p.get("lives_left") or 0),
                "locked_teams": list(p.get("locked_teams") or []),
            }
            async for p in db.sv_participants.find(
                {"tournament_id": tid, "eliminated_at": None},
                {"_id": 0, "user_id": 1, "lives_left": 1, "locked_teams": 1},
            )
        ]

        # ----- Big Match Bonus (+1 life) ---------------------------------
        # Rule (Aug 2026 v2): if a participant nailed the EXACT score of the
        # giornata's Big Match (via the "exact_score" Bonus pick on the
        # Survival subscription), they earn +1 life at settlement.
        # Constraints:
        #   * NO CAP: lives can grow unbounded (4, 5, 44, …). Survival does
        #     NOT clamp to initial_lives — accumulating lives is a valid
        #     strategic reward across a season.
        #   * RESCUE ALWAYS: the bonus is applied AFTER the wrong-picks
        #     deduction, so it fully rescues a player who would otherwise
        #     end at 0. Example: 3 lives → 3 wrong picks → 0 → +1 bonus = 1.
        #     Any player who ENTERED the matchday with ≥1 life can be
        #     saved by the Big Match. Only pre-existing eliminated players
        #     stay out (they don't submit picks in the first place).
        #   * Reads bonus_picks (game="survival", bonus_type="exact_score")
        #     for the matchday; no coupling with the Bonus module's own
        #     settle_at flag (we just need the prediction + actual score).
        big_match_bonus_users: set = set()
        bm_cfg = await db.bonus_configs.find_one(
            {"season": md.get("season"), "matchday": md["matchday"],
             "bonus_type": "exact_score"},
            {"_id": 0, "big_match": 1},
        )
        if bm_cfg and bm_cfg.get("big_match"):
            bm = bm_cfg["big_match"]
            bm_res = results_by_key.get((bm["home_team"], bm["away_team"]))
            if bm_res and not bm_res.get("postponed"):
                try:
                    actual_hs = int(bm_res.get("home_score") or 0)
                    actual_as = int(bm_res.get("away_score") or 0)
                    async for bp in db.bonus_picks.find(
                        {"game": "survival",
                         "bonus_type": "exact_score",
                         "season": md.get("season"),
                         "matchday": md["matchday"]},
                        {"_id": 0, "user_id": 1, "pick": 1},
                    ):
                        pred = bp.get("pick") or {}
                        try:
                            ph = int(pred.get("home_score"))
                            pa = int(pred.get("away_score"))
                        except (TypeError, ValueError):
                            continue
                        if ph == actual_hs and pa == actual_as:
                            big_match_bonus_users.add(bp["user_id"])
                except Exception:  # pragma: no cover
                    logger.exception("Big Match bonus scan failed")

        initial_lives_cap = int(t.get("initial_lives", DEFAULT_LIVES))  # kept for reference only — NOT applied to Big Match bonus

        # Apply participant updates. We iterate over the UNION of participants
        # with pending life deltas AND those who earned the Big Match bonus,
        # so a participant who made 0 wrong picks still gets the +1 applied.
        all_uids_to_update: set = (
            set(pending_participant_updates.keys()) | big_match_bonus_users
        )
        for uid in all_uids_to_update:
            p = await _get_participant(tid, uid)
            if not p:
                continue
            state = pending_participant_updates.get(
                uid, {"life_delta": 0, "new_locks": []},
            )
            new_lives = max(0, int(p.get("lives_left", 0)) + state["life_delta"])
            # Big Match bonus: ALWAYS +1 life, applied AFTER the wrong-picks
            # deduction. This is intentional so it doubles as a rescue net —
            # a player going 3→0 from wrong picks ends the giornata at 1
            # life if they nailed the Big Match exact score.
            if uid in big_match_bonus_users:
                new_lives = new_lives + 1
            existing = list(p.get("locked_teams") or [])
            for t_name in state["new_locks"]:
                if t_name and t_name not in existing:
                    existing.append(t_name)
            update_set: dict = {"lives_left": new_lives, "locked_teams": existing}
            if new_lives <= 0 and not p.get("eliminated_at"):
                update_set["eliminated_at"] = _now()
                # Store the matchday of elimination so the leaderboard can
                # show "Eliminato al MD X" and group ties correctly.
                update_set["eliminated_matchday"] = int(md["matchday"])
                eliminated_now.append(uid)
            await db.sv_participants.update_one(
                {"tournament_id": tid, "user_id": uid},
                {"$set": update_set},
            )

        # ----- Pareggio → Resurrezione ------------------------------------
        # If ALL previously-alive players got eliminated in this same
        # matchday (and at least 2 were alive going in), it's a tie: no
        # single winner emerged. Restore them all to their pre-matchday
        # state and let the tournament continue to the next matchday.
        # Repeats naturally: any tied matchday triggers this branch again.
        alive_after_updates = await db.sv_participants.count_documents(
            {"tournament_id": tid, "eliminated_at": None},
        )
        tie_break_triggered = False
        if (
            alive_after_updates == 0
            and len(pre_alive_snapshot) >= 2
        ):
            tie_break_triggered = True
            for snap in pre_alive_snapshot:
                await db.sv_participants.update_one(
                    {"tournament_id": tid, "user_id": snap["user_id"]},
                    {"$set": {
                        "lives_left": snap["lives_left"],
                        "locked_teams": snap["locked_teams"],
                        "eliminated_at": None,
                    }},
                )
            eliminated_now = []
            await db.sv_matchdays.update_one(
                {"id": md_id, "tournament_id": tid},
                {"$set": {"tie_break": True}},
            )
            logger.info(
                "Surviva tie-break on tournament=%s matchday=%s: %d players resurrected",
                tid, md["matchday"], len(pre_alive_snapshot),
            )

        # Mark matchday as settled and advance the tournament
        await db.sv_matchdays.update_one(
            {"id": md_id, "tournament_id": tid},
            {"$set": {
                "status": "settled",
                "settled_at": _now(),
                "big_match_bonus_users": list(big_match_bonus_users),
            }},
        )
        # Next matchday: the smallest one with matchday > current that exists.
        next_md = await db.sv_matchdays.find_one(
            {"tournament_id": tid, "matchday": {"$gt": md["matchday"]}},
            {"matchday": 1, "_id": 0},
            sort=[("matchday", 1)],
        )
        # A tournament finishes when there are no more matchdays OR when 0/1
        # players remain alive.
        alive = await db.sv_participants.count_documents(
            {"tournament_id": tid, "eliminated_at": None},
        )
        finished = next_md is None or alive <= 1

        tour_patch: dict = {}
        new_tournament_id: Optional[str] = None
        if finished:
            tour_patch["status"] = "finished"
            tour_patch["finished_at"] = _now()
        elif next_md:
            tour_patch["current_matchday"] = int(next_md["matchday"])
        if tour_patch:
            await db.sv_tournaments.update_one({"id": tid}, {"$set": tour_patch})

        # ----- Auto-rollover: spawn next Round starting from md+1 -----
        # Only when the tournament finished BUT the season still has matchdays
        # to play. New Round inherits initial_lives from the previous one and
        # gets a fresh unique invite code.
        if finished:
            next_start = int(md["matchday"]) + 1
            if next_start <= 38:
                remaining = await db.sal_calendar.count_documents({
                    "season": t.get("season"),
                    "matchday": {"$gte": next_start},
                    "excluded": {"$ne": True},
                })
                if remaining > 0:
                    base = t.get("name") or "Torneo"
                    m = re.search(r"·\s*Round\s+(\d+)\s*$", base)
                    if m:
                        n = int(m.group(1)) + 1
                        new_name = re.sub(r"·\s*Round\s+\d+\s*$", f"· Round {n}", base)
                    else:
                        new_name = f"{base} · Round 2"
                    try:
                        new_doc = await _spawn_tournament(
                            admin_user_id=t["admin_user_id"],
                            name=new_name,
                            season=t.get("season") or "2026-27",
                            initial_lives=int(t.get("initial_lives") or DEFAULT_LIVES),
                            start_matchday=next_start,
                            previous_tournament_id=t["id"],
                        )
                        new_tournament_id = new_doc["id"]
                        await db.sv_tournaments.update_one(
                            {"id": tid},
                            {"$set": {"next_tournament_id": new_tournament_id}},
                        )
                    except Exception:
                        logger.exception("Failed to spawn Surviva next Round")

        return {
            "ok": True,
            "matchday": md["matchday"],
            "stats": stats,
            "auto_filled": auto_stats,
            "eliminated_now": eliminated_now,
            "next_matchday": None if finished else int(next_md["matchday"]),
            "tournament_finished": finished,
            "alive_players": alive,
            "next_tournament_id": new_tournament_id,
            "tie_break": tie_break_triggered,
            "tie_break_players": (
                [s["user_id"] for s in pre_alive_snapshot]
                if tie_break_triggered else []
            ),
            "big_match_bonus_users": list(big_match_bonus_users),
            "big_match_bonus_count": len(big_match_bonus_users),
        }

    # ------------------------------------------------------------------
    # Fixture management inside a matchday (admin only)
    # ------------------------------------------------------------------

    def _fixture_slot(md: dict, idx: int) -> dict:
        fixtures = md.get("fixtures", [])
        if idx < 0 or idx >= len(fixtures):
            raise HTTPException(status_code=400, detail="Indice partita non valido")
        return fixtures[idx]

    @router.patch("/tournaments/{tid}/matchdays/{md_id}/fixtures/{idx}")
    async def update_fixture(
        tid: str, md_id: str, idx: int, patch: FixturePatch,
        user: dict = Depends(current_user),
    ):
        """Admin edits a single fixture inside an open matchday.

        Use ``postponed_before=True`` to hide a scheduled postponement from
        the pick UI (players can no longer choose that specific game).
        Rename teams by passing ``home_team`` / ``away_team``.

        Only allowed while the matchday is still ``open`` (no picks locked).
        """
        await _require_tournament_admin(tid, user)
        md = await db.sv_matchdays.find_one({"id": md_id, "tournament_id": tid}, {"_id": 0})
        if not md:
            raise HTTPException(status_code=404, detail="Giornata non trovata")
        if _md_is_locked(md):
            raise HTTPException(status_code=400, detail="Giornata già iniziata, non modificabile")

        _ = _fixture_slot(md, idx)  # validates idx
        set_ops: Dict[str, Any] = {}
        if patch.home_team is not None and patch.home_team.strip():
            set_ops[f"fixtures.{idx}.home_team"] = patch.home_team.strip()
        if patch.away_team is not None and patch.away_team.strip():
            set_ops[f"fixtures.{idx}.away_team"] = patch.away_team.strip()
        if patch.postponed_before is not None:
            set_ops[f"fixtures.{idx}.postponed_before"] = bool(patch.postponed_before)
        if not set_ops:
            raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")
        await db.sv_matchdays.update_one({"id": md_id}, {"$set": set_ops})

        # If the fixture is now postponed OR its teams changed, clear any
        # pending pick that referenced the old (home,away) tuple. Users
        # will need to re-pick after the admin's edit.
        if patch.postponed_before or patch.home_team is not None or patch.away_team is not None:
            old_fx = _fixture_slot(md, idx)
            await db.sv_picks.delete_many({
                "tournament_id": tid,
                "matchday_id": md_id,
                "home_team": old_fx["home_team"],
                "away_team": old_fx["away_team"],
            })

        updated = await db.sv_matchdays.find_one({"id": md_id}, {"_id": 0})
        return await _matchday_dict(updated, user["id"])

    @router.delete("/tournaments/{tid}/matchdays/{md_id}/fixtures/{idx}")
    async def delete_fixture(
        tid: str, md_id: str, idx: int, user: dict = Depends(current_user),
    ):
        """Admin removes a fixture (typical use: scheduled postponement).

        Any pending pick on the removed fixture is discarded so players can
        choose a different fixture for this matchday.
        """
        await _require_tournament_admin(tid, user)
        md = await db.sv_matchdays.find_one({"id": md_id, "tournament_id": tid}, {"_id": 0})
        if not md:
            raise HTTPException(status_code=404, detail="Giornata non trovata")
        if _md_is_locked(md):
            raise HTTPException(status_code=400, detail="Giornata già iniziata, non modificabile")

        old_fx = _fixture_slot(md, idx)
        new_fixtures = [
            f for i, f in enumerate(md.get("fixtures", [])) if i != idx
        ]
        await db.sv_matchdays.update_one(
            {"id": md_id}, {"$set": {"fixtures": new_fixtures}},
        )
        # Drop any pick that pointed to the removed fixture.
        await db.sv_picks.delete_many({
            "tournament_id": tid,
            "matchday_id": md_id,
            "home_team": old_fx["home_team"],
            "away_team": old_fx["away_team"],
        })
        updated = await db.sv_matchdays.find_one({"id": md_id}, {"_id": 0})
        return await _matchday_dict(updated, user["id"])


    # ------------------------------------------------------------------
    # Leaderboard + Riassunto Giornata
    # ------------------------------------------------------------------

    @router.get("/tournaments/{tid}/leaderboard")
    async def leaderboard(tid: str, user: dict = Depends(current_user)):
        t = await _get_tournament(tid)
        _ = t
        # Find current open matchday (earliest still-open) to flag which
        # participants have already submitted picks for it.
        current_md = await db.sv_matchdays.find_one(
            {"tournament_id": tid, "status": {"$ne": "settled"}},
            {"_id": 0, "id": 1, "matchday": 1},
            sort=[("matchday", 1)],
        )
        submitted_user_ids: set[str] = set()
        if current_md:
            async for pk in db.sv_picks.find(
                {"tournament_id": tid, "matchday_id": current_md["id"]},
                {"_id": 0, "user_id": 1, "picks": 1},
            ):
                # Only count as submitted if at least one pick present (v2
                # allows partial submissions but empty pick set = not started)
                if pk.get("picks"):
                    submitted_user_ids.add(pk["user_id"])
        # Bonus wins per player (across all bonus games attached to THIS
        # Survival tournament). Used in the leaderboard to reveal how many
        # of a player's lives came from a bonus win.
        bonus_wins: dict[str, int] = {}
        async for row in db.bonus_picks.aggregate([
            {"$match": {
                "game": "survival", "subscription_id": tid, "is_correct": True,
            }},
            {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
        ]):
            bonus_wins[row["_id"]] = int(row["n"])
        # Wrong picks per player — needed to compute "pick lives" which is
        # ``initial_lives - wrong_picks``. May be negative when a player
        # was already eliminated before the bonus could top them up.
        wrong_picks: dict[str, int] = {}
        async for row in db.sv_picks.aggregate([
            {"$match": {"tournament_id": tid, "correct": False}},
            {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
        ]):
            wrong_picks[row["_id"]] = int(row["n"])
        initial_lives = int(t.get("initial_lives") or 0)
        cursor = db.sv_participants.find({"tournament_id": tid}, {"_id": 0})
        rows = []
        async for p in cursor:
            uid = p["user_id"]
            rows.append({
                "user_id": uid,
                "nickname": p["nickname"],
                "lives_left": p.get("lives_left", 0),
                "locked_teams_count": len(p.get("locked_teams") or []),
                "blocked_signs_count": 0,  # legacy field (always 0 in v2)
                "eliminated": p.get("eliminated_at") is not None,
                "eliminated_at": p.get("eliminated_at"),
                "eliminated_matchday": p.get("eliminated_matchday"),
                "has_submitted_current": uid in submitted_user_ids,
                "bonus_wins": bonus_wins.get(uid, 0),
                "pick_lives": initial_lives - wrong_picks.get(uid, 0),
            })
        # Sort: alive first (by lives desc, most locked teams desc = most
        # experienced player), then eliminated in REVERSE-ELIMINATION order —
        # i.e. the player eliminated in the last matchday is ranked highest
        # among the eliminated, then those eliminated in the penultimate MD,
        # and so on down to the ones eliminated in G1.
        #
        # This produces a "how long they lasted" ranking, so an admin scanning
        # the classifica can immediately tell who was still in play near the
        # tournament's end vs. who dropped out early.
        alive = [r for r in rows if not r["eliminated"]]
        elim = [r for r in rows if r["eliminated"]]
        alive.sort(key=lambda r: (
            -r["lives_left"],
            -r["locked_teams_count"],
            r["nickname"].lower(),
        ))
        # 2-step stable sort: primary key = elimination matchday DESC
        # (later matchday = eliminated later = ranked higher), secondary =
        # eliminated_at DESC as a fallback for legacy rows without the
        # matchday field, tertiary = nickname ASC (deterministic tie-break).
        elim.sort(key=lambda r: r["nickname"].lower())
        elim.sort(key=lambda r: (r.get("eliminated_at") or ""), reverse=True)
        elim.sort(key=lambda r: int(r.get("eliminated_matchday") or 0), reverse=True)
        rows = alive + elim
        for i, r in enumerate(rows):
            r["rank"] = i + 1
        return rows

    @router.get("/tournaments/{tid}/matchdays/{md_id}/summary")
    async def matchday_summary(
        tid: str, md_id: str, user: dict = Depends(current_user),
    ):
        """Riassunto Giornata.

        - Prima del calcio d'inizio della prima partita: mostra SOLO gli
          aggregati (numero di scelte 1/X/2 per ogni partita) e nasconde
          l'identità dei giocatori.
        - Dopo il calcio d'inizio: sblocca anche la lista delle singole
          scelte per ogni utente.
        - **Privacy**: quando restano pochissimi giocatori vivi
          (≤ ``PRIVACY_THRESHOLD``), gli aggregati vengono nascosti prima
          del calcio d'inizio, altrimenti sarebbe banale dedurre chi ha
          scelto cosa.

        Anyone logged in can view — eliminated participants and past
        players must still be able to consult past matchdays.
        """
        PRIVACY_THRESHOLD = 4
        # No participant check: eliminated users must be able to see the
        # per-matchday summary of tournaments they took part in.
        md = await db.sv_matchdays.find_one({"id": md_id, "tournament_id": tid}, {"_id": 0})
        if not md:
            raise HTTPException(status_code=404, detail="Giornata non trovata")

        locked = _md_is_locked(md)
        alive_count = await db.sv_participants.count_documents(
            {"tournament_id": tid, "eliminated_at": None},
        )
        # Only mask counts BEFORE kick-off — after kick-off the round is
        # already decided so identity leakage is irrelevant.
        counts_hidden = (not locked) and alive_count > 0 and alive_count <= PRIVACY_THRESHOLD
        picks_cur = db.sv_picks.find({"tournament_id": tid, "matchday_id": md_id})

        # Aggregate counts per fixture (deleted/excluded matches filtered out)
        agg: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for f in await _effective_fixtures(md):
            agg[(f["home_team"], f["away_team"])] = {
                "home_team": f["home_team"],
                "away_team": f["away_team"],
                "counts": {"1": 0, "X": 0, "2": 0},
                "picks": [] if locked else None,
            }

        async for pk in picks_cur:
            key = (pk["home_team"], pk["away_team"])
            slot = agg.get(key)
            if not slot:
                continue
            p = pk["pick"]
            if p in slot["counts"] and not counts_hidden:
                slot["counts"][p] += 1
            if locked and slot["picks"] is not None:
                slot["picks"].append({
                    "nickname": pk.get("nickname", "?"),
                    "user_id": pk["user_id"],
                    "pick": p,
                    "correct": pk.get("correct"),
                })

        return {
            "matchday": md["matchday"],
            "kickoff_first": md.get("kickoff_first"),
            "locked": locked,
            "counts_hidden": counts_hidden,
            "alive_count": alive_count,
            "privacy_threshold": PRIVACY_THRESHOLD,
            "fixtures": list(agg.values()),
        }

    return router


__all__ = ["build_router", "ensure_indexes", "DEFAULT_LIVES"]
