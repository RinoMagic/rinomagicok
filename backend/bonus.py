"""RinoMagic — Bonus Games module.

Each player earns the right to a bonus mini-game every matchday, provided
they are subscribed (via invite) to at least one tournament/room/league of
the corresponding main game. There are two bonus games — the same question
is shared between the two paired main games, but each user's pick is stored
separately per game so a user subscribed to both can play twice.

## Bonus types
- ``exact_score`` — Tiket + Survival: guess the exact final score of the
  admin-selected Big Match of the matchday.
- ``first_scorer`` — Score + Fanta: guess the first goalscorer of the
  matchday.

## Rewards granted on settle
- Tiket: pending ``bonus_credit`` (admin handles the extra bet slip manually)
- Survival: ``+1`` life on every active (non-eliminated) participation
- Score: ``+1`` life on every active (non-eliminated) participation
- Fanta: ``+3`` on the winner's ``fg_matchday_results.total_fantavoto`` for
  every league they were part of that matchday

Rewards are cumulative — no upper cap on lives.

## Collections
- ``bonus_configs``   — one doc per (season, matchday, bonus_type)
- ``bonus_picks``     — one doc per (user_id, game, season, matchday)
- ``bonus_credits``   — Tiket-only pending rewards (admin dashboard)

Endpoints exposed under ``/api/bonus``.
"""
import re
import uuid
import unicodedata
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Callable, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from deadlines import is_matchday_locked as _global_deadline_passed

logger = logging.getLogger(__name__)

# Games supported and their bonus type mapping
GAMES = ("tiket", "score", "fanta", "survival")
BONUS_TYPE_BY_GAME: Dict[str, str] = {
    "tiket": "exact_score",
    "survival": "exact_score",
    "score": "first_scorer",
    "fanta": "first_scorer",
}
GAMES_BY_TYPE: Dict[str, Tuple[str, ...]] = {
    "exact_score": ("tiket", "survival"),
    "first_scorer": ("score", "fanta"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_scorer(name: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace.

    Used to compare user picks with the admin-provided first-scorer name in
    a case- and accent-insensitive way.
    """
    if not name:
        return ""
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = n.lower()
    n = re.sub(r"[^a-z0-9\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


# =========================================================================
# Pydantic bodies
# =========================================================================

class BigMatch(BaseModel):
    home_team: str = Field(min_length=1, max_length=60)
    away_team: str = Field(min_length=1, max_length=60)
    kickoff_iso: Optional[str] = None


class ConfigCreate(BaseModel):
    """Create or update a bonus config for a given (season, matchday, type)."""
    season: str = Field(min_length=3, max_length=10)
    matchday: int = Field(ge=1, le=38)
    bonus_type: str = Field(pattern=r"^(exact_score|first_scorer)$")
    big_match: Optional[BigMatch] = None  # required for exact_score

    @field_validator("bonus_type")
    @classmethod
    def _vt(cls, v):
        return v.strip()


class SettleExact(BaseModel):
    """Admin settles an ``exact_score`` bonus with the final score."""
    home_score: int = Field(ge=0, le=30)
    away_score: int = Field(ge=0, le=30)


class SettleScorer(BaseModel):
    """Admin settles a ``first_scorer`` bonus with the player name."""
    player_name: str = Field(min_length=1, max_length=80)


class PickExactSubmit(BaseModel):
    game: str = Field(pattern=r"^(tiket|survival)$")
    season: str = Field(default="2026-27", min_length=3, max_length=10)
    subscription_id: str = Field(min_length=1)
    home_score: int = Field(ge=0, le=30)
    away_score: int = Field(ge=0, le=30)


class PickScorerSubmit(BaseModel):
    game: str = Field(pattern=r"^(score|fanta)$")
    season: str = Field(default="2026-27", min_length=3, max_length=10)
    subscription_id: str = Field(min_length=1)
    player_name: str = Field(min_length=1, max_length=80)


# =========================================================================
# Router builder
# =========================================================================

def build_router(*, db, current_user, require_admin, display_name) -> APIRouter:
    router = APIRouter(prefix="/bonus", tags=["bonus"])

    # ------------------------------------------------------------------
    # Eligibility check
    # ------------------------------------------------------------------

    async def _user_subscriptions(uid: str, game: str) -> List[dict]:
        """Return the list of active subscriptions (rooms/tournaments/leagues)
        the user is part of, for the given game. Each subscription entitles
        the user to ONE separate bonus pick + ONE separate reward.
        """
        out: List[dict] = []
        if game == "tiket":
            room_ids = [m["room_id"] async for m in db.memberships.find(
                {"user_id": uid}, {"room_id": 1, "_id": 0},
            )]
            if not room_ids:
                return []
            rooms = [r async for r in db.rooms.find(
                {"id": {"$in": room_ids}}, {"_id": 0, "id": 1, "name": 1, "color": 1},
            )]
            for r in rooms:
                out.append({
                    "id": r["id"], "name": r.get("name") or "Stanza",
                    "kind": "tiket_room", "game": "tiket",
                    "color": r.get("color"),
                })
        elif game == "score":
            # Only ALIVE participations (lives > 0 and not eliminated) grant a
            # bonus slot. Eliminated players lose access to future bonuses on
            # that tournament, but keep the slot on any other tournament where
            # they are still in the game.
            tour_ids = [
                p["tournament_id"] async for p in db.sal_participants.find(
                    {
                        "user_id": uid,
                        "eliminated_at_matchday": None,
                        "lives_remaining": {"$gt": 0},
                    },
                    {"tournament_id": 1, "_id": 0},
                )
            ]
            if not tour_ids:
                return []
            tours = [t async for t in db.sal_tournaments.find(
                {"id": {"$in": tour_ids}}, {"_id": 0, "id": 1, "name": 1},
            )]
            for t in tours:
                out.append({
                    "id": t["id"], "name": t.get("name") or "Torneo",
                    "kind": "sal_tournament", "game": "score",
                })
        elif game == "fanta":
            league_ids = [m["league_id"] async for m in db.fg_memberships.find(
                {"user_id": uid}, {"league_id": 1, "_id": 0},
            )]
            if not league_ids:
                return []
            leagues = [l async for l in db.fg_leagues.find(
                {"id": {"$in": league_ids}}, {"_id": 0, "id": 1, "name": 1},
            )]
            for l in leagues:
                out.append({
                    "id": l["id"], "name": l.get("name") or "Lega",
                    "kind": "fg_league", "game": "fanta",
                })
        elif game == "survival":
            # Only ALIVE participations grant a bonus slot. If a player has
            # been eliminated (0 vite / eliminated_at set), they lose the
            # bonus right on that tournament but keep it on any other
            # tournament where they still have vite.
            tour_ids = [
                p["tournament_id"] async for p in db.sv_participants.find(
                    {
                        "user_id": uid,
                        "eliminated_at": None,
                        "lives_left": {"$gt": 0},
                    },
                    {"tournament_id": 1, "_id": 0},
                )
            ]
            if not tour_ids:
                return []
            tours = [t async for t in db.sv_tournaments.find(
                {"id": {"$in": tour_ids}}, {"_id": 0, "id": 1, "name": 1},
            )]
            for t in tours:
                out.append({
                    "id": t["id"], "name": t.get("name") or "Torneo",
                    "kind": "sv_tournament", "game": "survival",
                })
        return out

    async def _user_eligible(uid: str, game: str) -> bool:
        """Kept for backwards-compatible endpoints — a user is eligible for a
        game's bonus if they have at least one subscription of that game."""
        subs = await _user_subscriptions(uid, game)
        return len(subs) > 0

    async def _user_in_subscription(uid: str, game: str, subscription_id: str) -> Optional[dict]:
        """Verify the user is actually a member of the given room/tournament/
        league. Returns the subscription dict when authorised, else None."""
        subs = await _user_subscriptions(uid, game)
        for s in subs:
            if s["id"] == subscription_id:
                return s
        return None

    async def _earliest_kickoff(season: str, matchday: int) -> Optional[str]:
        first = None
        async for fx in db.sal_calendar.find(
            {"season": season, "matchday": matchday}, {"kickoff_iso": 1, "_id": 0},
        ):
            k = fx.get("kickoff_iso")
            if k and (first is None or k < first):
                first = k
        return first

    async def _matchday_fixtures(season: str, matchday: int) -> List[dict]:
        out = []
        async for fx in db.sal_calendar.find(
            {"season": season, "matchday": matchday,
             "excluded": {"$ne": True}}, {"_id": 0},
        ):
            out.append({
                "home_team": fx.get("home_team"),
                "away_team": fx.get("away_team"),
                "kickoff_iso": fx.get("kickoff_iso"),
            })
        return out

    def _config_status(cfg: dict) -> str:
        if cfg.get("settled_at"):
            return "settled"
        lock = cfg.get("lock_at")
        if lock:
            # Mongo strips tzinfo → normalise back to UTC for comparison
            if isinstance(lock, datetime) and lock.tzinfo is None:
                lock = lock.replace(tzinfo=timezone.utc)
            if _now() >= lock:
                return "locked"
        return "open"

    async def _config_dict(cfg: dict) -> dict:
        return {
            "id": cfg["id"],
            "season": cfg["season"],
            "matchday": cfg["matchday"],
            "bonus_type": cfg["bonus_type"],
            "games": list(GAMES_BY_TYPE[cfg["bonus_type"]]),
            "big_match": cfg.get("big_match"),
            "lock_at": (cfg.get("lock_at").isoformat() if cfg.get("lock_at") else None),
            "result": cfg.get("result"),
            "status": _config_status(cfg),
            "created_at": (cfg.get("created_at").isoformat() if cfg.get("created_at") else None),
            "settled_at": (cfg.get("settled_at").isoformat() if cfg.get("settled_at") else None),
        }

    async def _pick_dict(p: dict) -> dict:
        return {
            "id": p["id"],
            "user_id": p["user_id"],
            "nickname": p.get("nickname"),
            "game": p["game"],
            "subscription_id": p.get("subscription_id"),
            "subscription_name": p.get("subscription_name"),
            "season": p["season"],
            "matchday": p["matchday"],
            "bonus_type": p["bonus_type"],
            "pick": p.get("pick"),
            "submitted_at": (p.get("submitted_at").isoformat() if p.get("submitted_at") else None),
            "is_correct": p.get("is_correct"),
            "reward_granted_at": (
                p.get("reward_granted_at").isoformat() if p.get("reward_granted_at") else None
            ),
            "reward_details": p.get("reward_details"),
        }

    # ------------------------------------------------------------------
    # Admin: manage bonus configs
    # ------------------------------------------------------------------

    @router.post("/configs")
    async def create_or_update_config(body: ConfigCreate, user: dict = Depends(require_admin)):
        if body.bonus_type == "exact_score":
            if not body.big_match:
                raise HTTPException(status_code=400, detail="Big Match richiesto per bonus 'exact_score'")
            # Verify the big match is in the season calendar for that matchday
            # and is not excluded pre-round.
            fx = await db.sal_calendar.find_one({
                "season": body.season, "matchday": body.matchday,
                "home_team": body.big_match.home_team, "away_team": body.big_match.away_team,
            }, {"_id": 0})
            if not fx:
                raise HTTPException(
                    status_code=400,
                    detail="Big Match non presente nel calendario di questa giornata",
                )
            if fx.get("excluded"):
                raise HTTPException(
                    status_code=400,
                    detail="Big Match escluso dall'admin pre-turno: scegli un'altra partita",
                )
            kickoff = fx.get("kickoff_iso") or body.big_match.kickoff_iso
            big_match = {
                "home_team": body.big_match.home_team,
                "away_team": body.big_match.away_team,
                "kickoff_iso": kickoff,
            }
            lock_iso = kickoff
        else:  # first_scorer
            big_match = None
            # Confirm there are actually fixtures for this matchday (calendar
            # loaded), but do NOT require kickoff_iso — the Serie A PDF often
            # ships without dates and we still want to allow bonus creation.
            has_fixtures = await db.sal_calendar.count_documents({
                "season": body.season, "matchday": body.matchday,
            })
            if not has_fixtures:
                raise HTTPException(
                    status_code=400,
                    detail="Nessuna partita nel calendario per questa giornata",
                )
            lock_iso = await _earliest_kickoff(body.season, body.matchday)
        lock_at = None
        if lock_iso:
            try:
                lock_at = datetime.fromisoformat(lock_iso.replace("Z", "+00:00"))
                if lock_at.tzinfo is None:
                    lock_at = lock_at.replace(tzinfo=timezone.utc)
            except Exception:
                lock_at = None
        existing = await db.bonus_configs.find_one({
            "season": body.season, "matchday": body.matchday, "bonus_type": body.bonus_type,
        }, {"_id": 0})
        if existing and existing.get("settled_at"):
            raise HTTPException(
                status_code=400,
                detail="Bonus già liquidato, non modificabile",
            )
        if existing:
            await db.bonus_configs.update_one(
                {"id": existing["id"]},
                {"$set": {"big_match": big_match, "lock_at": lock_at}},
            )
            cfg = await db.bonus_configs.find_one({"id": existing["id"]}, {"_id": 0})
        else:
            cfg = {
                "id": str(uuid.uuid4()),
                "season": body.season,
                "matchday": body.matchday,
                "bonus_type": body.bonus_type,
                "big_match": big_match,
                "lock_at": lock_at,
                "result": None,
                "created_at": _now(),
                "created_by": user["id"],
                "settled_at": None,
            }
            await db.bonus_configs.insert_one(cfg)
        return await _config_dict(cfg)

    @router.get("/configs")
    async def list_configs(user: dict = Depends(current_user)):
        rows = [c async for c in db.bonus_configs.find({}, {"_id": 0})
                                                  .sort([("season", -1), ("matchday", -1)])]
        return [await _config_dict(c) for c in rows]

    @router.get("/configs/{cid}")
    async def get_config(cid: str, user: dict = Depends(current_user)):
        cfg = await db.bonus_configs.find_one({"id": cid}, {"_id": 0})
        if not cfg:
            raise HTTPException(status_code=404, detail="Config non trovata")
        return await _config_dict(cfg)

    @router.delete("/configs/{cid}")
    async def delete_config(cid: str, user: dict = Depends(require_admin)):
        cfg = await db.bonus_configs.find_one({"id": cid}, {"_id": 0})
        if not cfg:
            raise HTTPException(status_code=404, detail="Config non trovata")
        # Admin can always delete (open, closed, or settled): removes config
        # and all associated bonus_picks for that (season, matchday, bonus_type).
        await db.bonus_configs.delete_one({"id": cid})
        deleted = await db.bonus_picks.delete_many({
            "season": cfg["season"], "matchday": cfg["matchday"],
            "bonus_type": cfg["bonus_type"],
        })
        return {"ok": True, "deleted_picks": deleted.deleted_count}

    @router.post("/configs/{cid}/kick/{user_id}")
    async def kick_from_bonus(
        cid: str, user_id: str, user: dict = Depends(require_admin),
    ):
        """Hard-remove a user's bonus picks for a given bonus config.
        Deletes all bonus_picks for that user in (season, matchday, bonus_type).
        Cannot be applied after the config is settled. Irreversible."""
        cfg = await db.bonus_configs.find_one({"id": cid}, {"_id": 0})
        if not cfg:
            raise HTTPException(status_code=404, detail="Config non trovata")
        if cfg.get("settled_at"):
            raise HTTPException(
                status_code=400,
                detail="Bonus già liquidato: non è più possibile escludere giocatori",
            )
        target = await db.users.find_one(
            {"id": user_id}, {"_id": 0, "password_hash": 0}
        )
        if not target:
            raise HTTPException(status_code=404, detail="Utente non trovato")
        deleted = await db.bonus_picks.delete_many({
            "season": cfg["season"],
            "matchday": cfg["matchday"],
            "bonus_type": cfg["bonus_type"],
            "user_id": user_id,
        })
        if deleted.deleted_count == 0:
            raise HTTPException(
                status_code=404,
                detail="Il giocatore non ha fatto pronostici per questo bonus",
            )
        return {
            "ok": True,
            "deleted_picks": deleted.deleted_count,
            "kicked_user_id": user_id,
        }

    # ------------------------------------------------------------------
    # Reward granting
    # ------------------------------------------------------------------

    async def _grant_reward(pick: dict) -> Dict[str, Any]:
        """Apply the game-specific reward for a winning pick and return
        a JSON-serialisable ``reward_details`` blob describing what was
        granted. The reward is scoped to the subscription (room/tournament/
        league) that generated the pick — so a user with N subscriptions
        earns N independent rewards.
        """
        game = pick["game"]
        uid = pick["user_id"]
        matchday = pick["matchday"]
        sub_id = pick.get("subscription_id")
        sub_name = pick.get("subscription_name")
        details: Dict[str, Any] = {
            "game": game,
            "subscription_id": sub_id,
            "subscription_name": sub_name,
        }
        if game == "tiket":
            # Manual handling by admin — pending credit tied to this specific room.
            credit_id = str(uuid.uuid4())
            await db.bonus_credits.insert_one({
                "id": credit_id, "user_id": uid, "game": "tiket",
                "matchday": matchday, "season": pick["season"],
                "room_id": sub_id, "room_name": sub_name,
                "kind": "extra_bet_slip", "pending": True,
                "pick_id": pick["id"], "created_at": _now(),
                "consumed_at": None, "consumed_by": None,
            })
            details["kind"] = "extra_bet_slip_pending"
            details["credit_id"] = credit_id
            return details
        if game == "survival":
            # Survival "Big Match" (bonus_type=="exact_score") bonus lives
            # are granted by ``surviva.settle_matchday`` itself so that the
            # +1 can double as a rescue from 0 (needs to happen WITHIN the
            # Survival settle transaction, before eliminated_at is set).
            # We MUST NOT $inc lives here or the bonus would be applied
            # twice, inflating lives_left silently.
            # The "first_scorer" bonus, however, is still granted here —
            # it's independent from the Survival life-per-life mechanic
            # and safely adds +1 on top of the current state.
            if pick.get("bonus_type") == "exact_score":
                details["kind"] = "extra_life"
                details["tournament_id"] = sub_id
                details["granted_by"] = "surviva.settle_matchday"
                details["participations_updated"] = 0  # no-op here
                return details
            # +1 life on THIS SPECIFIC tournament's participation (non-eliminated).
            r = await db.sv_participants.update_one(
                {"tournament_id": sub_id, "user_id": uid, "eliminated_at": None},
                {"$inc": {"lives_left": 1}},
            )
            details["kind"] = "extra_life"
            details["tournament_id"] = sub_id
            details["participations_updated"] = int(r.modified_count)
            return details
        if game == "score":
            # v2.1: +3 lives per first_scorer bonus win (was +1). Cap at 15.
            r = await db.sal_participants.update_one(
                {
                    "tournament_id": sub_id, "user_id": uid,
                    "eliminated_at_matchday": None,
                    "lives_remaining": {"$lt": 15},
                },
                [
                    {"$set": {
                        "lives_remaining": {
                            "$min": [15, {"$add": ["$lives_remaining", 3]}],
                        },
                    }},
                ],
            )
            details["kind"] = "extra_life"
            details["lives_awarded"] = 3
            details["tournament_id"] = sub_id
            details["participations_updated"] = int(r.modified_count)
            return details
        if game == "fanta":
            # +3 on the SPECIFIC league's matchday total.
            r = await db.fg_matchday_results.update_one(
                {"league_id": sub_id, "user_id": uid, "matchday": matchday},
                {
                    "$inc": {"total_fantavoto": 3, "bonus_extra": 3},
                    "$setOnInsert": {
                        "id": str(uuid.uuid4()),
                        "league_id": sub_id, "user_id": uid,
                        "matchday": matchday,
                        "computed_at": _now(),
                    },
                },
                upsert=True,
            )
            details["kind"] = "fanta_bonus_points"
            details["points"] = 3
            details["league_id"] = sub_id
            details["updated"] = bool(r.modified_count or r.upserted_id)
            return details
        details["kind"] = "unknown"
        return details

    # ------------------------------------------------------------------
    # Admin: settle a bonus (compute winners + grant rewards)
    # ------------------------------------------------------------------

    async def _settle(cfg: dict, admin_user: dict) -> dict:
        picks = [
            p async for p in db.bonus_picks.find({
                "season": cfg["season"], "matchday": cfg["matchday"],
                "bonus_type": cfg["bonus_type"],
            }, {"_id": 0})
        ]
        winners = 0
        already_granted = 0
        for p in picks:
            if cfg["bonus_type"] == "exact_score":
                r = cfg.get("result") or {}
                pk = p.get("pick") or {}
                is_ok = (
                    pk.get("home_score") == r.get("home_score")
                    and pk.get("away_score") == r.get("away_score")
                )
            else:  # first_scorer
                r = cfg.get("result") or {}
                pk = p.get("pick") or {}
                is_ok = (
                    _normalize_scorer(pk.get("player_name") or "")
                    == _normalize_scorer(r.get("player_name") or "")
                    and bool(r.get("player_name"))
                )
            already = bool(p.get("reward_granted_at"))
            patch: Dict[str, Any] = {"is_correct": bool(is_ok)}
            if is_ok and not already:
                details = await _grant_reward(p)
                patch["reward_granted_at"] = _now()
                patch["reward_details"] = details
                winners += 1
            elif is_ok and already:
                already_granted += 1
            await db.bonus_picks.update_one({"id": p["id"]}, {"$set": patch})
        await db.bonus_configs.update_one(
            {"id": cfg["id"]},
            {"$set": {"settled_at": _now(), "settled_by": admin_user["id"]}},
        )
        cfg = await db.bonus_configs.find_one({"id": cfg["id"]}, {"_id": 0})
        return {
            "config": await _config_dict(cfg),
            "total_picks": len(picks),
            "winners": winners,
            "already_granted": already_granted,
        }

    @router.post("/configs/{cid}/settle-exact")
    async def settle_exact(cid: str, body: SettleExact, user: dict = Depends(require_admin)):
        cfg = await db.bonus_configs.find_one({"id": cid}, {"_id": 0})
        if not cfg:
            raise HTTPException(status_code=404, detail="Config non trovata")
        if cfg["bonus_type"] != "exact_score":
            raise HTTPException(status_code=400, detail="Bonus non 'exact_score'")
        await db.bonus_configs.update_one(
            {"id": cid},
            {"$set": {"result": {"home_score": body.home_score, "away_score": body.away_score}}},
        )
        cfg = await db.bonus_configs.find_one({"id": cid}, {"_id": 0})
        return await _settle(cfg, user)

    @router.post("/configs/{cid}/settle-scorer")
    async def settle_scorer(cid: str, body: SettleScorer, user: dict = Depends(require_admin)):
        cfg = await db.bonus_configs.find_one({"id": cid}, {"_id": 0})
        if not cfg:
            raise HTTPException(status_code=404, detail="Config non trovata")
        if cfg["bonus_type"] != "first_scorer":
            raise HTTPException(status_code=400, detail="Bonus non 'first_scorer'")
        await db.bonus_configs.update_one(
            {"id": cid},
            {"$set": {"result": {"player_name": body.player_name.strip()}}},
        )
        cfg = await db.bonus_configs.find_one({"id": cid}, {"_id": 0})
        return await _settle(cfg, user)

    # ------------------------------------------------------------------
    # Player: eligibility, subscriptions, available bonus, picks
    # ------------------------------------------------------------------

    @router.get("/eligibility")
    async def eligibility(user: dict = Depends(current_user)):
        """Boolean per-game eligibility summary + count of subscriptions.
        Frontend uses this to know how many bonus plays are available per game.
        """
        out: Dict[str, Any] = {}
        for g in GAMES:
            subs = await _user_subscriptions(user["id"], g)
            out[g] = {"eligible": len(subs) > 0, "subscriptions": len(subs)}
        return out

    @router.get("/subscriptions")
    async def subscriptions(game: str, user: dict = Depends(current_user)):
        """List the user's active subscriptions for a game — each entitles
        them to a separate bonus pick."""
        if game not in GAMES:
            raise HTTPException(status_code=400, detail="Gioco non valido")
        return await _user_subscriptions(user["id"], game)

    @router.get("/available")
    async def available(
        game: str,
        season: str = "2026-27",
        user: dict = Depends(current_user),
    ):
        """Current open/locked bonus for a game with one card PER subscription.

        Response shape:
            {
              game, bonus_type, season,
              config: {...} | null,
              subscriptions: [
                { id, name, kind, my_pick: {...} | null }
              ],
              fixtures: [...]  # only for exact_score, used by admin dropdown
            }
        """
        if game not in GAMES:
            raise HTTPException(status_code=400, detail="Gioco non valido")
        bonus_type = BONUS_TYPE_BY_GAME[game]
        subs = await _user_subscriptions(user["id"], game)
        cfg = await db.bonus_configs.find_one(
            {"season": season, "bonus_type": bonus_type, "settled_at": None},
            {"_id": 0}, sort=[("matchday", 1)],
        )
        subs_out: List[dict] = []
        if cfg:
            for s in subs:
                p = await db.bonus_picks.find_one({
                    "user_id": user["id"], "game": game,
                    "subscription_id": s["id"],
                    "season": season, "matchday": cfg["matchday"],
                }, {"_id": 0})
                subs_out.append({
                    **s,
                    "my_pick": (await _pick_dict(p)) if p else None,
                })
        else:
            subs_out = [{**s, "my_pick": None} for s in subs]
        fixtures = []
        if cfg and bonus_type == "exact_score":
            fixtures = await _matchday_fixtures(season, cfg["matchday"])
        return {
            "game": game, "bonus_type": bonus_type, "season": season,
            "eligible": len(subs) > 0,
            "config": await _config_dict(cfg) if cfg else None,
            "subscriptions": subs_out,
            "fixtures": fixtures,
        }

    async def _check_config_open(cfg: dict) -> None:
        status = _config_status(cfg)
        if status == "settled":
            raise HTTPException(status_code=400, detail="Bonus già liquidato")
        if status == "locked":
            raise HTTPException(status_code=400, detail="Countdown scaduto: pronostici bloccati")
        # Global cross-game deadline (shared with Survival/Score/Fanta/Tiket)
        season = cfg.get("season") or "2026-27"
        md = cfg.get("matchday")
        if isinstance(md, int) and await _global_deadline_passed(db, season, md):
            raise HTTPException(
                status_code=403,
                detail="Il timer di invio pronostici è scaduto per questa giornata.",
            )

    @router.post("/picks/exact")
    async def submit_exact(body: PickExactSubmit, user: dict = Depends(current_user)):
        if BONUS_TYPE_BY_GAME[body.game] != "exact_score":
            raise HTTPException(status_code=400, detail="Gioco non valido per bonus 'exact_score'")
        sub = await _user_in_subscription(user["id"], body.game, body.subscription_id)
        if not sub:
            raise HTTPException(
                status_code=403,
                detail=f"Non sei iscritto a questa stanza/torneo di {body.game.title()}",
            )
        cfg = await db.bonus_configs.find_one(
            {"season": body.season, "bonus_type": "exact_score", "settled_at": None},
            {"_id": 0}, sort=[("matchday", 1)],
        )
        if not cfg:
            raise HTTPException(status_code=404, detail="Nessun bonus attivo")
        await _check_config_open(cfg)
        pick_data = {"home_score": body.home_score, "away_score": body.away_score}
        return await _upsert_pick(cfg, body.game, user, pick_data, sub)

    @router.post("/picks/scorer")
    async def submit_scorer(body: PickScorerSubmit, user: dict = Depends(current_user)):
        if BONUS_TYPE_BY_GAME[body.game] != "first_scorer":
            raise HTTPException(status_code=400, detail="Gioco non valido per bonus 'first_scorer'")
        sub = await _user_in_subscription(user["id"], body.game, body.subscription_id)
        if not sub:
            raise HTTPException(
                status_code=403,
                detail=f"Non sei iscritto a questa lega/torneo di {body.game.title()}",
            )
        cfg = await db.bonus_configs.find_one(
            {"season": body.season, "bonus_type": "first_scorer", "settled_at": None},
            {"_id": 0}, sort=[("matchday", 1)],
        )
        if not cfg:
            raise HTTPException(status_code=404, detail="Nessun bonus attivo")
        await _check_config_open(cfg)
        name = body.player_name.strip()
        pick_data = {"player_name": name, "player_name_norm": _normalize_scorer(name)}
        return await _upsert_pick(cfg, body.game, user, pick_data, sub)

    async def _upsert_pick(
        cfg: dict, game: str, user: dict, pick_data: dict, sub: dict,
    ) -> dict:
        existing = await db.bonus_picks.find_one({
            "user_id": user["id"], "game": game,
            "subscription_id": sub["id"],
            "season": cfg["season"], "matchday": cfg["matchday"],
        }, {"_id": 0})
        if existing:
            await db.bonus_picks.update_one(
                {"id": existing["id"]},
                {"$set": {
                    "pick": pick_data, "submitted_at": _now(),
                    "subscription_name": sub.get("name"),
                }},
            )
            existing = await db.bonus_picks.find_one({"id": existing["id"]}, {"_id": 0})
            return await _pick_dict(existing)
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "nickname": display_name(user),
            "game": game,
            "subscription_id": sub["id"],
            "subscription_name": sub.get("name"),
            "season": cfg["season"],
            "matchday": cfg["matchday"],
            "bonus_type": cfg["bonus_type"],
            "pick": pick_data,
            "submitted_at": _now(),
            "is_correct": None,
            "reward_granted_at": None,
            "reward_details": None,
        }
        await db.bonus_picks.insert_one(doc)
        return await _pick_dict(doc)

    @router.get("/history")
    async def history(
        game: str,
        season: str = "2026-27",
        limit: int = 20,
        user: dict = Depends(current_user),
    ):
        if game not in GAMES:
            raise HTTPException(status_code=400, detail="Gioco non valido")
        rows = [
            p async for p in db.bonus_picks.find({
                "user_id": user["id"], "game": game, "season": season,
            }, {"_id": 0}).sort("matchday", -1).limit(limit)
        ]
        return [await _pick_dict(p) for p in rows]

    @router.get("/current-locked-picks")
    async def current_locked_picks(
        game: str,
        season: str = "2026-27",
        user: dict = Depends(current_user),
    ):
        """Riassunto pubblico dei pick del bonus corrente.

        Restituisce l'elenco completo dei partecipanti + i loro pronostici
        SOLO se la deadline è passata e la giornata è bloccata (locked)
        oppure il bonus è già stato liquidato. Prima del kickoff torna una
        lista vuota per non rivelare le scelte in anticipo.

        Fix: itera le config dal matchday più recente al più vecchio e
        ritorna la prima "visibile" (locked / deadline scaduta / settled).
        Questo evita che una config futura in stato ``draft`` (creata
        dall'admin per la prossima giornata) nasconda i pick della
        giornata appena chiusa.
        """
        if game not in GAMES:
            raise HTTPException(status_code=400, detail="Gioco non valido")
        bonus_type = BONUS_TYPE_BY_GAME[game]
        # Match the SAME query used by /available so the "current locked"
        # config is always consistent with the one the frontend shows for
        # submission. This avoids the inconsistency where /available shows
        # config X but current-locked-picks returned config Y.
        cfg = await db.bonus_configs.find_one(
            {"season": season, "bonus_type": bonus_type, "settled_at": None},
            {"_id": 0}, sort=[("matchday", 1)],
        )
        if cfg is None:
            return {"visible": False, "reason": "no_config", "picks": []}

        status = _config_status(cfg)
        md = cfg.get("matchday")
        deadline_passed = False
        if isinstance(md, int):
            deadline_passed = await _global_deadline_passed(db, season, md)

        if not (status == "locked" or deadline_passed):
            return {
                "visible": False,
                "reason": "deadline_not_passed",
                "matchday": cfg.get("matchday"),
                "big_match": cfg.get("big_match"),
                "picks": [],
            }

        raw = [
            p async for p in db.bonus_picks.find({
                "season": season,
                "matchday": cfg["matchday"],
                "bonus_type": bonus_type,
                "game": game,
            }, {"_id": 0})
        ]
        # Sort alphabetically by subscription name then nickname — simple
        # and neutral (does not reveal submission order).
        raw.sort(key=lambda p: (
            (p.get("subscription_name") or "").lower(),
            (p.get("nickname") or "").lower(),
        ))
        picks_out = [await _pick_dict(p) for p in raw]
        return {
            "visible": True,
            "settled": status == "settled",
            "matchday": cfg.get("matchday"),
            "big_match": cfg.get("big_match"),
            "result": cfg.get("result"),
            "picks": picks_out,
        }

    @router.get("/history/full")
    async def history_full(
        game: str,
        season: str = "2026-27",
        limit: int = 20,
        user: dict = Depends(current_user),
    ):
        """Full public history for a game: every settled bonus with ALL
        participants' picks (winners flagged).

        Dopo il settle (ovvero: quando la deadline è passata e l'admin ha
        chiuso il bonus con il risultato ufficiale) i pronostici Big Match
        sono PUBBLICI a tutti gli utenti autenticati, indipendentemente dalla
        stanza/torneo di appartenenza — allineato al principio "dopo la
        deadline tutti vedono tutto" richiesto dal committente.
        """
        if game not in GAMES:
            raise HTTPException(status_code=400, detail="Gioco non valido")
        bonus_type = BONUS_TYPE_BY_GAME[game]

        # Only settled configs count as history
        configs = [
            cfg async for cfg in db.bonus_configs.find(
                {"season": season, "bonus_type": bonus_type,
                 "settled_at": {"$ne": None}},
                {"_id": 0},
            ).sort("matchday", -1).limit(limit)
        ]

        out: List[dict] = []
        for cfg in configs:
            picks = [
                p async for p in db.bonus_picks.find({
                    "season": cfg["season"],
                    "matchday": cfg["matchday"],
                    "bonus_type": cfg["bonus_type"],
                    "game": game,
                }, {"_id": 0})
            ]
            # Publicly visible — no subscription filter after settle.
            # Sort alphabetically by subscription name then nickname to keep
            # ordering stable and neutral (does not reveal submission order).
            picks.sort(key=lambda p: (
                (p.get("subscription_name") or "").lower(),
                (p.get("nickname") or "").lower(),
            ))
            out.append({
                "matchday": cfg["matchday"],
                "bonus_type": cfg["bonus_type"],
                "big_match": cfg.get("big_match"),
                "result": cfg.get("result"),
                "settled_at": (
                    cfg["settled_at"].isoformat()
                    if hasattr(cfg["settled_at"], "isoformat")
                    else cfg.get("settled_at")
                ),
                "picks": [
                    {
                        "user_id": p["user_id"],
                        "nickname": p.get("nickname"),
                        "subscription_id": p.get("subscription_id"),
                        "subscription_name": p.get("subscription_name"),
                        "pick": p.get("pick"),
                        "is_correct": bool(p.get("is_correct")),
                        "reward_details": p.get("reward_details"),
                    }
                    for p in picks
                ],
            })
        return out

    # Public matchday leaderboard (aggregate — no per-user picks leaked
    # before kickoff, mirroring the Riassunto Giornata privacy pattern).
    @router.get("/configs/{cid}/summary")
    async def summary(cid: str, user: dict = Depends(current_user)):
        cfg = await db.bonus_configs.find_one({"id": cid}, {"_id": 0})
        if not cfg:
            raise HTTPException(status_code=404, detail="Config non trovata")
        status = _config_status(cfg)
        # Reveal picks when the bonus is locked/settled, OR when the global
        # matchday deadline has already elapsed (aligned with Survival & co).
        season = cfg.get("season") or "2026-27"
        md = cfg.get("matchday")
        deadline_passed = False
        if isinstance(md, int):
            deadline_passed = await _global_deadline_passed(db, season, md)
        reveal = status in ("locked", "settled") or deadline_passed
        picks = [
            p async for p in db.bonus_picks.find({
                "season": cfg["season"], "matchday": cfg["matchday"],
                "bonus_type": cfg["bonus_type"],
            }, {"_id": 0})
        ]
        by_game: Dict[str, int] = {g: 0 for g in GAMES_BY_TYPE[cfg["bonus_type"]]}
        winners_by_game: Dict[str, int] = {g: 0 for g in GAMES_BY_TYPE[cfg["bonus_type"]]}
        for p in picks:
            g = p.get("game")
            if g in by_game:
                by_game[g] += 1
            if p.get("is_correct") and g in winners_by_game:
                winners_by_game[g] += 1
        details = None
        if reveal:
            details = [
                {
                    "nickname": p.get("nickname"),
                    "game": p.get("game"),
                    "pick": p.get("pick"),
                    "is_correct": p.get("is_correct"),
                }
                for p in picks
            ]
        return {
            "config": await _config_dict(cfg),
            "total_picks": len(picks),
            "picks_by_game": by_game,
            "winners_by_game": winners_by_game,
            "details": details,
        }

    # Admin-only: raw list of picks for a config (used for kick / management).
    @router.get("/configs/{cid}/picks-admin")
    async def picks_admin(cid: str, user: dict = Depends(require_admin)):
        cfg = await db.bonus_configs.find_one({"id": cid}, {"_id": 0})
        if not cfg:
            raise HTTPException(status_code=404, detail="Config non trovata")
        rows = [
            {
                "user_id": p.get("user_id"),
                "nickname": p.get("nickname"),
                "game": p.get("game"),
                "pick": p.get("pick"),
                "subscription_id": p.get("subscription_id"),
                "subscription_name": p.get("subscription_name"),
                "is_correct": p.get("is_correct"),
            }
            async for p in db.bonus_picks.find({
                "season": cfg["season"], "matchday": cfg["matchday"],
                "bonus_type": cfg["bonus_type"],
            }, {"_id": 0})
        ]
        return {"config_id": cid, "picks": rows}

    return router


async def ensure_indexes(db) -> None:
    await db.bonus_configs.create_index("id", unique=True)
    await db.bonus_configs.create_index(
        [("season", 1), ("matchday", 1), ("bonus_type", 1)],
        unique=True,
    )
    await db.bonus_picks.create_index("id", unique=True)
    # New unique key: one pick per subscription (user can have multiple
    # subscriptions per game and thus multiple picks). Drop the legacy
    # index (without subscription_id) if it still exists.
    try:
        await db.bonus_picks.drop_index("user_id_1_game_1_season_1_matchday_1")
    except Exception:
        pass
    await db.bonus_picks.create_index(
        [("user_id", 1), ("game", 1), ("subscription_id", 1),
         ("season", 1), ("matchday", 1)],
        unique=True,
        name="uniq_pick_per_subscription",
    )
    await db.bonus_picks.create_index(
        [("season", 1), ("matchday", 1), ("bonus_type", 1)]
    )
    await db.bonus_credits.create_index("id", unique=True)
    await db.bonus_credits.create_index([("user_id", 1), ("game", 1), ("pending", 1)])


__all__ = ["build_router", "ensure_indexes", "ensure_bonus_draft"]


# =========================================================================
# Public helper — used by surviva.py, scoreandlive.py, fantagiornata.py,
# thebesttiket.py to auto-create a bonus draft the first time a
# tournament / room / league is created for a matchday.
# =========================================================================
async def ensure_bonus_draft(
    db,
    *,
    season: str,
    matchday: int,
    bonus_type: str,
    created_by: Optional[str] = None,
    max_advance: int = 38,
) -> Optional[dict]:
    """Guarantee that a bonus_config exists for the earliest matchday
    ``≥ matchday`` (up to matchday 38) that is NOT already settled.

    Rationale: when the admin creates a new Survival/Score/Fanta/Tiket
    tournament that starts at matchday N, they want an active bonus to
    appear for that same matchday. But if matchday N's bonus for that
    type is already ``settled`` (results locked in), we advance to N+1,
    N+2, … until we find a usable slot.

    Behaviour:
      • If a config for the target (season, matchday, bonus_type) already
        exists (draft OR ready), return it unchanged.
      • Otherwise create a *draft* config:
          - ``exact_score`` → big_match=None (admin must complete later)
          - ``first_scorer`` → immediately usable (no big match needed)
      • Never overwrites an existing config.

    Returns the config document (or ``None`` if no valid matchday was
    found up to matchday 38, which is an edge case).
    """
    if bonus_type not in {"exact_score", "first_scorer"}:
        raise ValueError(f"invalid bonus_type: {bonus_type}")
    md = int(matchday)
    while md <= max_advance:
        existing = await db.bonus_configs.find_one(
            {"season": season, "matchday": md, "bonus_type": bonus_type},
            {"_id": 0},
        )
        if existing:
            if existing.get("settled_at"):
                md += 1
                continue
            return existing  # already exists (draft or complete)
        # Verify the calendar has fixtures for this matchday (excluded
        # ones don't count — a fully-excluded matchday should be skipped).
        has_fixtures = await db.sal_calendar.count_documents({
            "season": season, "matchday": md,
            "excluded": {"$ne": True},
        })
        if not has_fixtures:
            md += 1
            continue
        # Create draft
        lock_iso = None
        if bonus_type == "first_scorer":
            # earliest kickoff of the matchday (may be None if calendar
            # lacks kickoff dates — we still create the config)
            earliest = await db.sal_calendar.find_one(
                {"season": season, "matchday": md,
                 "kickoff_iso": {"$ne": None},
                 "excluded": {"$ne": True}},
                {"_id": 0, "kickoff_iso": 1},
                sort=[("kickoff_iso", 1)],
            )
            if earliest:
                lock_iso = earliest.get("kickoff_iso")
        lock_at = None
        if lock_iso:
            try:
                lock_at = datetime.fromisoformat(lock_iso.replace("Z", "+00:00"))
                if lock_at.tzinfo is None:
                    lock_at = lock_at.replace(tzinfo=timezone.utc)
            except Exception:
                lock_at = None
        cfg = {
            "id": str(uuid.uuid4()),
            "season": season,
            "matchday": md,
            "bonus_type": bonus_type,
            "big_match": None,
            "lock_at": lock_at,
            "result": None,
            "created_at": _now(),
            "created_by": created_by,
            "settled_at": None,
        }
        try:
            await db.bonus_configs.insert_one(cfg)
        except Exception:
            # race condition: another creator inserted concurrently
            existing = await db.bonus_configs.find_one(
                {"season": season, "matchday": md, "bonus_type": bonus_type},
                {"_id": 0},
            )
            return existing
        return cfg
    return None
