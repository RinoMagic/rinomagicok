"""FantaGiornata — the third mini-game inside the RinoMagic umbrella.

Weekly fantasy football: each user submits an 11-player starting XI + an
8-player bench (2P+2D+2C+2A). After the Voti PDF is imported (matchday_facts),
each player's fantavoto is derived, unavailable ("senza voto") starters are
auto-substituted with a same-role bench, and the sum becomes the user's
matchday score. A rolling leaderboard aggregates points across matchdays.

Shared assets with the rest of the app:
* Roster source: ``sal_players`` collection (imported from the Listone PDF).
* Vote source:  ``matchday_facts`` collection (imported from the Voti PDF).
* Auth stack:   same JWT dependencies as the other routers.

MongoDB collections (all prefixed ``fg_``):
* ``fg_leagues``            — league metadata (id, name, admin, status, ...)
* ``fg_invites``            — single-use invites (mirror of ``sal_invites``)
* ``fg_memberships``        — {league_id, user_id, nickname, joined_at, ...}
* ``fg_lineups``            — {league_id, user_id, matchday, starters[], bench[]}
* ``fg_matchday_results``   — computed fantavoto snapshots per user
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
from pymongo import ReturnDocument

from deadlines import is_matchday_locked as _global_deadline_passed

logger = logging.getLogger("fantagiornata")

ROLE_ORDER = ("P", "D", "C", "A")
STARTERS_COUNT = 11
BENCH_COMPOSITION = {"P": 2, "D": 2, "C": 2, "A": 2}
BENCH_COUNT = sum(BENCH_COMPOSITION.values())  # 8

# Only 1 goalkeeper allowed in the starting XI (Serie A convention).
STARTER_MIN = {"P": 1, "D": 0, "C": 0, "A": 0}
STARTER_MAX = {"P": 1, "D": 5, "C": 5, "A": 5}


# =========================================================================
# Fantavoto pure calculation
# =========================================================================

def fantavoto_from_fact(fact: dict, role: str) -> Optional[float]:
    """Compute the fantavoto for a single player from a ``matchday_facts``
    document.

    Returns ``None`` if the player received no rating (``sv=True``) — the
    caller must then trigger the auto-substitution logic.

    Rules (aligned with the legacy FantaGiornata module — see the
    ``_archive/fantagiornata`` tests for the reference cases):

        base voto
        + 3 * gol aperti  (Gf)
        + 3 * rigori segnati (Rf)
        - 3 * rigori sbagliati (Rs)
        + 1 * assist
        - 2 * autogol (Au)
        - 0.5 se ammonito (Amm > 0)
        - 1 se espulso (Esp > 0)
        # portiere-only
        - 1 * gol subiti (Gs)
        + 3 * rigori parati (Rp)

    NB: The ``gol_vittoria`` (+1) and ``gol_pareggio`` (+0.5) bonuses from
    the legacy rules require goal-by-goal timeline data that is not present
    in the fantacalcio.it Voti PDF. They are intentionally omitted until we
    have an authoritative source for match timelines.
    """
    if fact.get("sv"):
        return None
    voto = fact.get("voto")
    if voto is None:
        return None
    fv = float(voto)
    fv += 3 * int(fact.get("gf", 0))
    fv += 3 * int(fact.get("rf", 0))
    fv -= 3 * int(fact.get("rs", 0))
    fv += 1 * int(fact.get("ass", 0))
    fv -= 2 * int(fact.get("au", 0))
    if int(fact.get("amm", 0)) > 0:
        fv -= 0.5
    if int(fact.get("esp", 0)) > 0:
        fv -= 1
    if role == "P":
        fv -= 1 * int(fact.get("gs", 0))
        fv += 3 * int(fact.get("rp", 0))
    return round(fv, 2)


def validate_starters(starters_by_role: Dict[str, List[str]]) -> None:
    """Raise 400 if the starting XI is not a legal formation.

    Enforces: exactly 1 P, at most 5 D/C/A, total 11 starters.
    """
    p = len(starters_by_role.get("P", []))
    d = len(starters_by_role.get("D", []))
    c = len(starters_by_role.get("C", []))
    a = len(starters_by_role.get("A", []))
    total = p + d + c + a
    if total != STARTERS_COUNT:
        raise HTTPException(
            status_code=400,
            detail=f"La formazione deve avere {STARTERS_COUNT} titolari (fornito: {total})",
        )
    if p != 1:
        raise HTTPException(status_code=400, detail="Serve esattamente 1 portiere titolare")
    for r, n in (("D", d), ("C", c), ("A", a)):
        if not (STARTER_MIN[r] <= n <= STARTER_MAX[r]):
            raise HTTPException(
                status_code=400,
                detail=f"Numero di {r} non valido: {n} (max {STARTER_MAX[r]})",
            )


def validate_bench(bench_by_role: Dict[str, List[str]]) -> None:
    """Raise 400 if the bench isn't exactly 2P+2D+2C+2A."""
    for r, need in BENCH_COMPOSITION.items():
        got = len(bench_by_role.get(r, []))
        if got != need:
            raise HTTPException(
                status_code=400,
                detail=f"La panchina richiede {need} {r} (fornito: {got})",
            )


def compute_lineup_score(
    starters: List[dict],       # list of {player: sal_players_doc, fact: matchday_facts_doc or None}
    bench: List[dict],          # same shape
) -> dict:
    """Compute the total fantavoto for a lineup, applying auto-substitutions.

    Each entry has:
      * ``player``: the roster doc (must have ``role`` and ``id``)
      * ``fact``:   the matchday_facts doc or None if the player didn't play

    Returns a dict with:
      * ``total``:        sum of the 11 fantavoti actually counted
      * ``breakdown``:    per-starter details (with sub info if any)
      * ``bench_used``:   ids of bench players that substituted a starter
      * ``bench_left``:   ids of bench players still on the bench

    Rule: a starter with fantavoto=None (SV or absent) is replaced by the
    first bench player of the SAME role (order of bench list preserved).
    """
    breakdown: List[dict] = []
    bench_used: set = set()
    total = 0.0

    # Index bench by role, preserving user-provided order.
    bench_by_role: Dict[str, List[dict]] = {r: [] for r in ROLE_ORDER}
    for b in bench:
        r = b["player"]["role"]
        bench_by_role.setdefault(r, []).append(b)

    for s in starters:
        role = s["player"]["role"]
        fv = None if s["fact"] is None else fantavoto_from_fact(s["fact"], role)
        entry = {
            "player_id": s["player"]["id"],
            "player_name": s["player"].get("full_name")
                or f"{s['player'].get('first_name','')} {s['player'].get('last_name','')}".strip(),
            "role": role,
            "team": s["player"].get("team"),
            "starter_fantavoto": fv,
            "substituted_by": None,
            "final_fantavoto": fv,
        }
        if fv is None:
            # find first available bench of same role
            for cand in bench_by_role.get(role, []):
                if cand["player"]["id"] in bench_used:
                    continue
                cand_fv = None if cand["fact"] is None else fantavoto_from_fact(cand["fact"], role)
                if cand_fv is None:
                    continue
                bench_used.add(cand["player"]["id"])
                entry["substituted_by"] = {
                    "player_id": cand["player"]["id"],
                    "player_name": cand["player"].get("full_name")
                        or f"{cand['player'].get('first_name','')} {cand['player'].get('last_name','')}".strip(),
                    "fantavoto": cand_fv,
                }
                entry["final_fantavoto"] = cand_fv
                break

        if entry["final_fantavoto"] is not None:
            total += entry["final_fantavoto"]
        breakdown.append(entry)

    bench_left = [b["player"]["id"] for b in bench if b["player"]["id"] not in bench_used]
    return {
        "total": round(total, 2),
        "breakdown": breakdown,
        "bench_used": sorted(bench_used),
        "bench_left": bench_left,
    }


# =========================================================================
# Pydantic models
# =========================================================================

class LeagueCreate(BaseModel):
    name: str = Field(min_length=2, max_length=60)


class InviteRedeem(BaseModel):
    invite_code: str


MODULE_ROLE_COUNTS: Dict[str, Dict[str, int]] = {
    # module -> required non-GK role counts (P is always 1)
    "3-4-3": {"D": 3, "C": 4, "A": 3},
    "3-5-2": {"D": 3, "C": 5, "A": 2},
    "4-3-3": {"D": 4, "C": 3, "A": 3},
    "4-4-2": {"D": 4, "C": 4, "A": 2},
    "4-5-1": {"D": 4, "C": 5, "A": 1},
    "5-3-2": {"D": 5, "C": 3, "A": 2},
    "5-4-1": {"D": 5, "C": 4, "A": 1},
}
ALLOWED_MODULES = set(MODULE_ROLE_COUNTS.keys())


class LineupIn(BaseModel):
    matchday: int = Field(ge=1, le=38)
    starters: List[str] = Field(min_length=STARTERS_COUNT, max_length=STARTERS_COUNT)
    bench: List[str] = Field(min_length=BENCH_COUNT, max_length=BENCH_COUNT)
    module: Optional[str] = None  # e.g. "4-3-3" — cosmetic/UI hint, not used in scoring

    @field_validator("starters", "bench")
    @classmethod
    def _unique(cls, v: List[str]) -> List[str]:
        if len(set(v)) != len(v):
            raise ValueError("Giocatori duplicati non ammessi")
        return v

    @field_validator("module")
    @classmethod
    def _module_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if v == "":
            return None
        if v not in ALLOWED_MODULES:
            raise ValueError(f"Modulo non supportato: {v}")
        return v


class SettleIn(BaseModel):
    matchday: int = Field(ge=1, le=38)


# =========================================================================
# Router factory
# =========================================================================

def build_router(
    db,
    current_user: Callable,
    require_admin: Callable,
    display_name: Callable,
) -> APIRouter:
    router = APIRouter(prefix="/fg")

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _gen_code(length: int = 6) -> str:
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

    async def _get_league(league_id: str) -> dict:
        lg = await db.fg_leagues.find_one({"id": league_id}, {"_id": 0})
        if not lg:
            raise HTTPException(status_code=404, detail="Lega non trovata")
        return lg

    async def _require_league_admin(league_id: str, user: dict) -> dict:
        lg = await _get_league(league_id)
        if user["role"] != "admin" and user["id"] != lg.get("admin_user_id"):
            raise HTTPException(status_code=403, detail="Solo l'admin della lega può eseguire questa azione")
        return lg

    async def _ensure_member(league_id: str, user_id: str) -> None:
        m = await db.fg_memberships.find_one({"league_id": league_id, "user_id": user_id})
        if not m:
            raise HTTPException(status_code=403, detail="Non sei iscritto a questa lega")

    async def _league_dict(lg: dict, viewer: Optional[dict] = None) -> dict:
        members = await db.fg_memberships.count_documents({"league_id": lg["id"]})
        invites_total = await db.fg_invites.count_documents({"league_id": lg["id"], "revoked_at": None})
        invites_available = await db.fg_invites.count_documents(
            {"league_id": lg["id"], "revoked_at": None, "used_by_user_id": None}
        )
        is_admin = bool(
            viewer and (viewer["role"] == "admin" or viewer["id"] == lg.get("admin_user_id"))
        )
        return {
            **{k: lg.get(k) for k in ("id", "name", "status", "admin_user_id",
                                       "current_matchday", "invite_code", "created_at")},
            "members_count": members,
            "invites_total": invites_total,
            "invites_available": invites_available,
            "is_admin": is_admin,
        }

    async def _invite_dict(inv: dict) -> dict:
        used_nickname = None
        if inv.get("used_by_user_id"):
            u = await db.users.find_one({"id": inv["used_by_user_id"]}, {"_id": 0})
            if u:
                used_nickname = display_name(u)
        return {
            "id": inv["id"], "code": inv["code"],
            "league_id": inv["league_id"],
            "created_at": inv.get("created_at"),
            "used_by_user_id": inv.get("used_by_user_id"),
            "used_by_nickname": used_nickname,
            "used_at": inv.get("used_at"),
            "revoked_at": inv.get("revoked_at"),
        }

    # ==================================================================
    # Leagues
    # ==================================================================

    @router.post("/leagues")
    async def create_league(data: LeagueCreate, user: dict = Depends(require_admin)):
        # Generate a unique invite code (checked against BOTH leagues & invites collections).
        for _ in range(20):
            code = _gen_code()
            if (not await db.fg_leagues.find_one({"invite_code": code})
                    and not await db.fg_invites.find_one({"code": code})):
                break
        else:
            raise HTTPException(status_code=500, detail="Impossibile generare un codice univoco")
        now = _now()
        lg_id = str(uuid.uuid4())
        doc = {
            "id": lg_id, "name": data.name.strip(),
            "admin_user_id": user["id"], "game": "fantagiornata",
            "status": "open", "current_matchday": None,
            "created_at": now, "invite_code": code,
        }
        await db.fg_leagues.insert_one(doc)
        # Auto-enrol ALL current admins as league members (multi-admin support).
        admin_users = [u async for u in db.users.find(
            {"role": "admin"}, {"_id": 0, "id": 1, "username": 1, "email": 1},
        )]
        for adm in admin_users:
            existing = await db.fg_memberships.find_one({
                "league_id": lg_id, "user_id": adm["id"],
            })
            if existing:
                continue
            await db.fg_memberships.insert_one({
                "league_id": lg_id, "user_id": adm["id"],
                "nickname": display_name(adm), "joined_at": now,
            })
        # Create the first single-use invite
        await db.fg_invites.insert_one({
            "id": str(uuid.uuid4()), "league_id": lg_id, "code": code,
            "used_by_user_id": None, "used_at": None,
            "created_at": now, "created_by": user["id"], "revoked_at": None,
        })
        return await _league_dict(doc, user)

    @router.get("/leagues")
    async def list_leagues(user: dict = Depends(current_user)):
        if user["role"] == "admin":
            cursor = db.fg_leagues.find({}, {"_id": 0}).sort("created_at", -1)
        else:
            joined = [m["league_id"] async for m in db.fg_memberships.find(
                {"user_id": user["id"]}, {"league_id": 1, "_id": 0})]
            cursor = db.fg_leagues.find({"id": {"$in": joined}}, {"_id": 0}).sort("created_at", -1)
        return [await _league_dict(lg, user) async for lg in cursor]

    @router.get("/leagues/{league_id}")
    async def get_league(league_id: str, user: dict = Depends(current_user)):
        lg = await _get_league(league_id)
        season = lg.get("season") or "2026-27"
        # Determine the "current" matchday. Order of preference:
        # 1) Earliest deadline in ``matchday_deadlines`` that hasn't passed
        # 2) Largest deadline that HAS passed (settle window open)
        # 3) Largest matchday number found in fg_lineups for this league
        # 4) Fallback to 1 (beginning of season)
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        current_md: Optional[int] = None
        future_dl = await db.matchday_deadlines.find_one(
            {"season": season, "deadline_at": {"$gt": now_iso}},
            {"_id": 0, "matchday": 1},
            sort=[("matchday", 1)],
        )
        if future_dl:
            current_md = future_dl["matchday"]
        else:
            past_dl = await db.matchday_deadlines.find_one(
                {"season": season, "deadline_at": {"$lte": now_iso}},
                {"_id": 0, "matchday": 1},
                sort=[("matchday", -1)],
            )
            if past_dl:
                current_md = past_dl["matchday"]
        if current_md is None:
            # No deadlines configured yet: derive from actual lineup activity
            latest_lineup = await db.fg_lineups.find_one(
                {"league_id": league_id},
                {"_id": 0, "matchday": 1},
                sort=[("matchday", -1)],
            )
            current_md = latest_lineup["matchday"] if latest_lineup else 1

        submitted_user_ids: set[str] = set()
        async for ln in db.fg_lineups.find(
            {"league_id": league_id, "matchday": current_md},
            {"_id": 0, "user_id": 1, "starters": 1},
        ):
            # Consider "submitted" only if the user has a complete lineup
            # (11 starters). Partial drafts don't count.
            if len(ln.get("starters") or []) == 11:
                submitted_user_ids.add(ln["user_id"])

        # Members info
        members: List[dict] = []
        async for m in db.fg_memberships.find({"league_id": league_id}, {"_id": 0}):
            members.append({
                "user_id": m["user_id"], "nickname": m["nickname"],
                "joined_at": m.get("joined_at"),
                "has_submitted_current": m["user_id"] in submitted_user_ids,
            })
        base = await _league_dict(lg, user)
        base["members"] = members
        base["current_matchday_number"] = current_md
        return base

    @router.delete("/leagues/{league_id}")
    async def delete_league(league_id: str, user: dict = Depends(current_user)):
        await _require_league_admin(league_id, user)
        await db.fg_leagues.delete_one({"id": league_id})
        await db.fg_memberships.delete_many({"league_id": league_id})
        await db.fg_invites.delete_many({"league_id": league_id})
        await db.fg_lineups.delete_many({"league_id": league_id})
        await db.fg_matchday_results.delete_many({"league_id": league_id})
        return {"ok": True}

    @router.post("/leagues/{league_id}/kick/{user_id}")
    async def kick_from_league(
        league_id: str,
        user_id: str,
        user: dict = Depends(current_user),
    ):
        """Hard-remove a player from a FantaGiornata league: removes
        membership + all lineups + all matchday results for that user in
        that league. Irreversible."""
        await _require_league_admin(league_id, user)
        lg = await db.fg_leagues.find_one({"id": league_id}, {"_id": 0})
        if lg and lg.get("admin_user_id") == user_id:
            raise HTTPException(
                status_code=400,
                detail="Impossibile escludere l'admin della lega",
            )
        target = await db.users.find_one(
            {"id": user_id}, {"_id": 0, "password_hash": 0}
        )
        if not target:
            raise HTTPException(status_code=404, detail="Utente non trovato")
        m = await db.fg_memberships.find_one(
            {"league_id": league_id, "user_id": user_id}
        )
        if not m:
            raise HTTPException(
                status_code=404,
                detail="Il giocatore non è iscritto a questa lega",
            )
        deleted_lineups = await db.fg_lineups.delete_many(
            {"league_id": league_id, "user_id": user_id}
        )
        deleted_results = await db.fg_matchday_results.delete_many(
            {"league_id": league_id, "user_id": user_id}
        )
        await db.fg_memberships.delete_many(
            {"league_id": league_id, "user_id": user_id}
        )
        return {
            "ok": True,
            "deleted_lineups": deleted_lineups.deleted_count,
            "deleted_results": deleted_results.deleted_count,
            "kicked_user_id": user_id,
        }

    # ==================================================================
    # Invites (single-use, mirror of TheBestTiket and SAL)
    # ==================================================================

    @router.get("/leagues/by-code/{invite_code}")
    async def preview_league(invite_code: str):
        code = invite_code.upper().strip()
        inv = await db.fg_invites.find_one({"code": code})
        if not inv:
            raise HTTPException(status_code=404, detail="Codice invito non valido")
        if inv.get("revoked_at"):
            raise HTTPException(status_code=410, detail="Codice invito revocato")
        if inv.get("used_by_user_id"):
            raise HTTPException(status_code=410, detail="Codice invito già utilizzato")
        lg = await db.fg_leagues.find_one({"id": inv["league_id"]}, {"_id": 0})
        if not lg:
            raise HTTPException(status_code=404, detail="Lega non trovata")
        return {
            "id": lg["id"], "name": lg["name"], "status": lg["status"],
            "invite_code": code, "game": "fantagiornata",
        }

    @router.get("/leagues/{league_id}/invites")
    async def list_invites(league_id: str, user: dict = Depends(current_user)):
        await _require_league_admin(league_id, user)
        invs = [i async for i in db.fg_invites.find({"league_id": league_id}, {"_id": 0}).sort("created_at", -1)]
        return [await _invite_dict(i) for i in invs]

    @router.post("/leagues/{league_id}/invites")
    async def create_invite(league_id: str, user: dict = Depends(current_user)):
        await _require_league_admin(league_id, user)
        for _ in range(20):
            code = _gen_code()
            if (not await db.fg_leagues.find_one({"invite_code": code})
                    and not await db.fg_invites.find_one({"code": code})):
                break
        else:
            raise HTTPException(status_code=500, detail="Impossibile generare un codice univoco")
        now = _now()
        doc = {
            "id": str(uuid.uuid4()), "league_id": league_id, "code": code,
            "used_by_user_id": None, "used_at": None,
            "created_at": now, "created_by": user["id"], "revoked_at": None,
        }
        await db.fg_invites.insert_one(doc)
        return await _invite_dict(doc)

    @router.delete("/leagues/{league_id}/invites/{invite_id}")
    async def revoke_invite(league_id: str, invite_id: str, user: dict = Depends(current_user)):
        await _require_league_admin(league_id, user)
        inv = await db.fg_invites.find_one({"id": invite_id, "league_id": league_id}, {"_id": 0})
        if not inv:
            raise HTTPException(status_code=404, detail="Invito non trovato")
        if inv.get("used_by_user_id"):
            raise HTTPException(status_code=400, detail="Impossibile revocare: invito già utilizzato")
        if inv.get("revoked_at"):
            return await _invite_dict(inv)
        now = _now()
        await db.fg_invites.update_one({"id": invite_id}, {"$set": {"revoked_at": now}})
        inv["revoked_at"] = now
        return await _invite_dict(inv)

    @router.post("/leagues/{league_id}/join")
    async def join_league(league_id: str, data: InviteRedeem, user: dict = Depends(current_user)):
        code = data.invite_code.upper().strip()
        now = _now()
        claimed = await db.fg_invites.find_one_and_update(
            {"code": code, "league_id": league_id, "used_by_user_id": None, "revoked_at": None},
            {"$set": {"used_by_user_id": user["id"], "used_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            inv = await db.fg_invites.find_one({"code": code})
            if not inv or inv.get("league_id") != league_id:
                raise HTTPException(status_code=400, detail="Codice invito non valido per questa lega")
            if inv.get("revoked_at"):
                raise HTTPException(status_code=410, detail="Codice invito revocato")
            if inv.get("used_by_user_id") == user["id"]:
                lg = await _get_league(league_id)
                return await _league_dict(lg, user)
            raise HTTPException(status_code=410, detail="Codice invito già utilizzato")

        lg = await _get_league(league_id)
        existing = await db.fg_memberships.find_one({"league_id": league_id, "user_id": user["id"]})
        if not existing:
            await db.fg_memberships.insert_one({
                "league_id": league_id, "user_id": user["id"],
                "nickname": display_name(user), "joined_at": now,
            })
        return await _league_dict(lg, user)

    # ==================================================================
    # Lineups
    # ==================================================================

    async def _resolve_players(player_ids: List[str]) -> Dict[str, dict]:
        docs = [d async for d in db.sal_players.find(
            {"id": {"$in": player_ids}}, {"_id": 0}
        )]
        by_id = {d["id"]: d for d in docs}
        missing = [pid for pid in player_ids if pid not in by_id]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Giocatori non trovati nel listone: {missing[:3]}"
                       + ("..." if len(missing) > 3 else ""),
            )
        return by_id

    def _split_by_role(players: List[dict]) -> Dict[str, List[dict]]:
        buckets: Dict[str, List[dict]] = {r: [] for r in ROLE_ORDER}
        for p in players:
            role = p.get("role")
            if role not in buckets:
                raise HTTPException(status_code=400, detail=f"Ruolo giocatore non valido: {role}")
            buckets[role].append(p)
        return buckets

    @router.post("/leagues/{league_id}/lineup")
    async def save_lineup(league_id: str, data: LineupIn, user: dict = Depends(current_user)):
        await _ensure_member(league_id, user["id"])

        # Global deadline gate (shared timer across all games).
        _lg_for_gate = await db.fg_leagues.find_one({"id": league_id}, {"_id": 0, "season": 1})
        _season_for_gate = (_lg_for_gate or {}).get("season") or "2026-27"
        if await _global_deadline_passed(db, _season_for_gate, data.matchday):
            raise HTTPException(
                status_code=403,
                detail="Il timer di invio pronostici è scaduto per questa giornata.",
            )
        # Check nothing is on both lists
        overlap = set(data.starters) & set(data.bench)
        if overlap:
            raise HTTPException(status_code=400, detail="Un giocatore non può essere titolare e in panchina")

        players_by_id = await _resolve_players(list(data.starters) + list(data.bench))
        starters_p = _split_by_role([players_by_id[i] for i in data.starters])
        bench_p = _split_by_role([players_by_id[i] for i in data.bench])
        validate_starters({r: [p["id"] for p in starters_p[r]] for r in ROLE_ORDER})
        validate_bench({r: [p["id"] for p in bench_p[r]] for r in ROLE_ORDER})

        # If a module was declared, enforce its D/C/A distribution exactly.
        if data.module is not None:
            expected = MODULE_ROLE_COUNTS[data.module]
            got_d = len(starters_p["D"])
            got_c = len(starters_p["C"])
            got_a = len(starters_p["A"])
            if (got_d, got_c, got_a) != (expected["D"], expected["C"], expected["A"]):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"La formazione non rispetta il modulo {data.module}: "
                        f"attesi {expected['D']}D/{expected['C']}C/{expected['A']}A, "
                        f"ricevuti {got_d}D/{got_c}C/{got_a}A"
                    ),
                )

        now = _now()
        lineup_doc = {
            "league_id": league_id,
            "user_id": user["id"],
            "matchday": data.matchday,
            "starters": list(data.starters),
            "bench": list(data.bench),
            "module": data.module,
            "updated_at": now,
        }
        await db.fg_lineups.update_one(
            {"league_id": league_id, "user_id": user["id"], "matchday": data.matchday},
            {"$set": lineup_doc, "$setOnInsert": {"created_at": now, "id": str(uuid.uuid4())}},
            upsert=True,
        )
        return {"ok": True, **lineup_doc}

    @router.get("/leagues/{league_id}/lineup/{matchday}")
    async def get_lineup(league_id: str, matchday: int, user: dict = Depends(current_user)):
        await _ensure_member(league_id, user["id"])
        doc = await db.fg_lineups.find_one(
            {"league_id": league_id, "user_id": user["id"], "matchday": matchday},
            {"_id": 0},
        )
        if not doc:
            return {"league_id": league_id, "matchday": matchday, "starters": [], "bench": [], "module": None}
        return doc

    # ==================================================================
    # Settle a matchday
    # ==================================================================

    async def _load_facts_for_matchday(matchday: int) -> Dict[Tuple[int, str], dict]:
        """Load matchday_facts and index by (player_code, team_lower).

        Facts are also indexed by full name-lowered / team-lower as a fallback
        when the roster doesn't carry the fanta_id (older imports).
        """
        idx_by_code: Dict[int, dict] = {}
        idx_by_name: Dict[Tuple[str, str], dict] = {}
        async for f in db.matchday_facts.find({"matchday": matchday}, {"_id": 0}):
            code = f.get("player_code")
            if code is not None:
                idx_by_code[int(code)] = f
            name = (f.get("player_name") or "").strip().lower()
            team = (f.get("team") or "").strip().lower()
            idx_by_name[(name, team)] = f
        return {"by_code": idx_by_code, "by_name": idx_by_name}

    def _lookup_fact(player: dict, index: dict) -> Optional[dict]:
        code = player.get("fanta_id")
        if code is not None and int(code) in index["by_code"]:
            return index["by_code"][int(code)]
        # Fallback: last name (lowered) + team (lowered)
        last_lower = (player.get("last_name") or "").strip().lower()
        team_lower = (player.get("team") or "").strip().lower()
        return index["by_name"].get((last_lower, team_lower))

    @router.post("/leagues/{league_id}/settle")
    async def settle_matchday(league_id: str, data: SettleIn, user: dict = Depends(current_user)):
        """Compute fantavoto for every user in the league using ``matchday_facts``.

        Called after the admin has imported the Voti PDF for the target matchday.
        The results are stored in ``fg_matchday_results`` and re-runnable at will.
        """
        await _require_league_admin(league_id, user)
        # Ensure we have the facts
        n_facts = await db.matchday_facts.count_documents({"matchday": data.matchday})
        if not n_facts:
            raise HTTPException(
                status_code=400,
                detail=f"Nessun voto per la giornata {data.matchday}. Carica prima il PDF Voti.",
            )
        facts_idx = await _load_facts_for_matchday(data.matchday)

        # For each user with a lineup for this matchday, compute score
        results: List[dict] = []
        async for lineup in db.fg_lineups.find(
            {"league_id": league_id, "matchday": data.matchday}, {"_id": 0}
        ):
            players_by_id = await _resolve_players(lineup["starters"] + lineup["bench"])
            starters = [
                {"player": players_by_id[pid], "fact": _lookup_fact(players_by_id[pid], facts_idx)}
                for pid in lineup["starters"]
            ]
            bench = [
                {"player": players_by_id[pid], "fact": _lookup_fact(players_by_id[pid], facts_idx)}
                for pid in lineup["bench"]
            ]
            score = compute_lineup_score(starters, bench)
            now = _now()
            result_doc = {
                "id": str(uuid.uuid4()),
                "league_id": league_id,
                "user_id": lineup["user_id"],
                "matchday": data.matchday,
                "total_fantavoto": score["total"],
                "breakdown": score["breakdown"],
                "bench_used": score["bench_used"],
                "bench_left": score["bench_left"],
                "computed_at": now,
            }
            await db.fg_matchday_results.update_one(
                {"league_id": league_id, "user_id": lineup["user_id"], "matchday": data.matchday},
                {"$set": result_doc},
                upsert=True,
            )
            m = await db.fg_memberships.find_one(
                {"league_id": league_id, "user_id": lineup["user_id"]}, {"_id": 0}
            )
            results.append({
                "user_id": lineup["user_id"],
                "nickname": (m or {}).get("nickname"),
                "total_fantavoto": score["total"],
                "starters_used": sum(1 for b in score["breakdown"] if b["final_fantavoto"] is not None),
            })

        results.sort(key=lambda r: r["total_fantavoto"] or 0, reverse=True)
        # Update current_matchday on the league
        await db.fg_leagues.update_one(
            {"id": league_id},
            {"$set": {"current_matchday": data.matchday}},
        )
        return {
            "matchday": data.matchday,
            "league_id": league_id,
            "results": results,
            "settled_users": len(results),
        }

    @router.get("/leagues/{league_id}/lineups/{matchday}")
    async def list_all_lineups(
        league_id: str, matchday: int, user: dict = Depends(current_user),
    ):
        """Return every member's lineup for the given matchday.

        Visibility rule (aligned with Survival & the other games): each
        member's lineup is included ONLY when the global deadline for that
        matchday has already elapsed. Before then, the caller sees just
        their OWN lineup and the entries for the others are marked
        ``hidden: true``.
        """
        await _ensure_member(league_id, user["id"])
        lg = await db.fg_leagues.find_one({"id": league_id}, {"_id": 0, "season": 1})
        season = (lg or {}).get("season") or "2026-27"
        deadline_passed = await _global_deadline_passed(db, season, matchday)

        # Gather members
        members = [
            m async for m in db.fg_memberships.find(
                {"league_id": league_id}, {"_id": 0},
            )
        ]
        # Gather lineups
        lineups_by_uid: Dict[str, dict] = {}
        async for ln in db.fg_lineups.find(
            {"league_id": league_id, "matchday": matchday}, {"_id": 0},
        ):
            lineups_by_uid[ln["user_id"]] = ln

        rows: List[dict] = []
        for m in members:
            uid = m["user_id"]
            is_self = uid == user["id"]
            can_see = deadline_passed or is_self
            ln = lineups_by_uid.get(uid)
            row = {
                "user_id": uid,
                "nickname": m.get("nickname"),
                "has_lineup": bool(ln),
                "hidden": not can_see,
            }
            if can_see and ln:
                # Resolve player details for display
                ids = list(ln.get("starters", [])) + list(ln.get("bench", []))
                players_by_id = {
                    p["id"]: p async for p in db.sal_players.find(
                        {"id": {"$in": ids}}, {"_id": 0},
                    )
                }
                row.update({
                    "matchday": ln.get("matchday"),
                    "module": ln.get("module"),
                    "starters": [players_by_id.get(i) for i in ln.get("starters", [])],
                    "bench": [players_by_id.get(i) for i in ln.get("bench", [])],
                    "updated_at": ln.get("updated_at"),
                })
            rows.append(row)
        return {
            "league_id": league_id,
            "matchday": matchday,
            "deadline_passed": deadline_passed,
            "members": rows,
        }

    @router.get("/leagues/{league_id}/results/{matchday}")
    async def get_matchday_results(league_id: str, matchday: int, user: dict = Depends(current_user)):
        await _ensure_member(league_id, user["id"])
        rows: List[dict] = []
        async for r in db.fg_matchday_results.find(
            {"league_id": league_id, "matchday": matchday}, {"_id": 0}
        ):
            m = await db.fg_memberships.find_one(
                {"league_id": league_id, "user_id": r["user_id"]}, {"_id": 0}
            )
            rows.append({
                **r,
                "nickname": (m or {}).get("nickname"),
            })
        rows.sort(key=lambda r: r.get("total_fantavoto") or 0, reverse=True)
        return {"league_id": league_id, "matchday": matchday, "results": rows}

    @router.get("/leagues/{league_id}/leaderboard")
    async def leaderboard(league_id: str, user: dict = Depends(current_user)):
        """Cumulative leaderboard across all matchdays settled so far."""
        await _ensure_member(league_id, user["id"])
        pipeline = [
            {"$match": {"league_id": league_id}},
            {"$group": {
                "_id": "$user_id",
                "total": {"$sum": "$total_fantavoto"},
                "matchdays_played": {"$sum": 1},
            }},
            {"$sort": {"total": -1}},
        ]
        rows = []
        async for r in db.fg_matchday_results.aggregate(pipeline):
            m = await db.fg_memberships.find_one(
                {"league_id": league_id, "user_id": r["_id"]}, {"_id": 0}
            )
            rows.append({
                "user_id": r["_id"],
                "nickname": (m or {}).get("nickname"),
                "total": round(r["total"], 2),
                "matchdays_played": r["matchdays_played"],
            })
        # Include members with 0 points
        seen = {r["user_id"] for r in rows}
        async for m in db.fg_memberships.find({"league_id": league_id}, {"_id": 0}):
            if m["user_id"] not in seen:
                rows.append({
                    "user_id": m["user_id"], "nickname": m.get("nickname"),
                    "total": 0.0, "matchdays_played": 0,
                })
        return {"league_id": league_id, "leaderboard": rows}

    return router


# =========================================================================
# Indexes
# =========================================================================

async def ensure_indexes(db) -> None:
    await db.fg_leagues.create_index("admin_user_id")
    await db.fg_leagues.create_index("invite_code", unique=True, sparse=True)
    await db.fg_invites.create_index("code", unique=True)
    await db.fg_invites.create_index([("league_id", 1), ("used_by_user_id", 1)])
    await db.fg_memberships.create_index(
        [("league_id", 1), ("user_id", 1)], unique=True
    )
    await db.fg_lineups.create_index(
        [("league_id", 1), ("user_id", 1), ("matchday", 1)], unique=True
    )
    await db.fg_matchday_results.create_index(
        [("league_id", 1), ("user_id", 1), ("matchday", 1)], unique=True
    )
    await db.fg_matchday_results.create_index([("league_id", 1), ("matchday", 1)])
