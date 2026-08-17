"""Matchday Deadlines — global lock-time per Serie A giornata.

A single deadline per (season, matchday) is shared by every mini-game:
Survival, ScoreAndLive, FantaGiornata, TheBestTiket and Bonus. Once the
deadline elapses no more submissions are accepted and all picks / lineups
become publicly visible.

MongoDB collection: ``matchday_deadlines``
Schema::

    {
      season: str,
      matchday: int,           # 1..38
      deadline_at: iso str,    # stored as UTC ISO datetime
      updated_at: iso str,
      updated_by: str,         # admin user id
    }

Unique index on (season, matchday).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from zoneinfo import ZoneInfo
    ROME_TZ = ZoneInfo("Europe/Rome")
except Exception:  # pragma: no cover — Python < 3.9 or missing tzdata
    ROME_TZ = timezone.utc


DEFAULT_SEASON = "2026-27"


# =========================================================================
# Datetime helpers
# =========================================================================

def _parse_deadline_input(value: str) -> datetime:
    """Parse admin input into an aware UTC datetime.

    Accepts:
      * ISO with tz (``2026-08-24T18:00:00+02:00`` or trailing ``Z``)
      * Naive ISO (``2026-08-24T18:00:00`` / ``2026-08-24 18:00``) — treated
        as Europe/Rome local time.
    """
    v = (value or "").strip().replace(" ", "T")
    if not v:
        raise HTTPException(400, "Data mancante")
    if v.endswith("Z"):
        try:
            dt = datetime.fromisoformat(v[:-1]).replace(tzinfo=timezone.utc)
        except ValueError as e:
            raise HTTPException(400, f"Formato data invalido: {value}") from e
    else:
        try:
            dt = datetime.fromisoformat(v)
        except ValueError as e:
            raise HTTPException(400, f"Formato data invalido: {value}") from e
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ROME_TZ)
    return dt.astimezone(timezone.utc)


def _parse_stored(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


# =========================================================================
# Public helpers used by every game module
# =========================================================================

async def get_deadline(
    db, season: str, matchday: int,
) -> Optional[datetime]:
    """Return the deadline (aware, UTC) for (season, matchday), or None."""
    doc = await db.matchday_deadlines.find_one(
        {"season": season, "matchday": matchday},
        {"_id": 0, "deadline_at": 1},
    )
    if not doc:
        return None
    return _parse_stored(doc.get("deadline_at"))


async def is_matchday_locked(
    db, season: str, matchday: int,
) -> bool:
    """True when the deadline exists and has already elapsed.

    If no deadline is configured, returns ``False`` (game modules must
    apply their own fallback rules — typically "allow" until admin sets it).
    """
    dl = await get_deadline(db, season, matchday)
    if dl is None:
        return False
    return datetime.now(timezone.utc) >= dl


async def get_current_matchday_info(
    db, season: str = DEFAULT_SEASON,
) -> Dict[str, Any]:
    """Return info about the "current" matchday, useful for hub countdowns.

    Preferred: the next matchday that has a deadline in the future.
    Fallback: the most-recent matchday that has already been locked.
    """
    now = datetime.now(timezone.utc)
    upcoming = None
    latest_locked = None
    async for d in db.matchday_deadlines.find(
        {"season": season}, {"_id": 0}
    ).sort("matchday", 1):
        dt = _parse_stored(d.get("deadline_at"))
        if not dt:
            continue
        if dt > now:
            if upcoming is None or dt < upcoming["dt"]:
                upcoming = {"matchday": d["matchday"], "dt": dt}
        else:
            if latest_locked is None or dt > latest_locked["dt"]:
                latest_locked = {"matchday": d["matchday"], "dt": dt}
    if upcoming:
        return {
            "season": season,
            "matchday": upcoming["matchday"],
            "deadline_at": upcoming["dt"].isoformat(),
            "locked": False,
            "server_now": now.isoformat(),
        }
    if latest_locked:
        return {
            "season": season,
            "matchday": latest_locked["matchday"],
            "deadline_at": latest_locked["dt"].isoformat(),
            "locked": True,
            "server_now": now.isoformat(),
        }
    return {
        "season": season,
        "matchday": None,
        "deadline_at": None,
        "locked": False,
        "server_now": now.isoformat(),
    }


# =========================================================================
# Pydantic models
# =========================================================================

class DeadlineIn(BaseModel):
    """Single-matchday update payload.

    Set ``deadline_at`` to ``null`` / empty string to clear the deadline.
    """
    deadline_at: Optional[str] = None


class BulkItemIn(BaseModel):
    matchday: int = Field(ge=1, le=38)
    deadline_at: Optional[str] = None


class BulkIn(BaseModel):
    season: str = Field(default=DEFAULT_SEASON, min_length=3, max_length=10)
    deadlines: List[BulkItemIn]


# =========================================================================
# MongoDB indexes + backfill
# =========================================================================

async def ensure_indexes(db) -> None:
    await db.matchday_deadlines.create_index(
        [("season", 1), ("matchday", 1)], unique=True
    )


async def backfill_from_tiket_rooms(db) -> Dict[str, int]:
    """One-shot migration: copy per-room deadline_at (TheBestTiket) into the
    new global collection when the (season, matchday) slot is still empty.

    Idempotent — safe to call on every startup.
    """
    copied = 0
    skipped = 0
    async for room in db.rooms.find(
        {"deadline_at": {"$exists": True, "$nin": [None, ""]}},
        {"_id": 0, "matchday": 1, "deadline_at": 1, "season": 1},
    ):
        md = room.get("matchday")
        if not isinstance(md, int) or md < 1 or md > 38:
            continue
        season = room.get("season") or DEFAULT_SEASON
        existing = await db.matchday_deadlines.find_one(
            {"season": season, "matchday": md}, {"_id": 0}
        )
        if existing:
            skipped += 1
            continue
        try:
            dt = _parse_stored(room["deadline_at"])
            if dt is None:
                continue
        except Exception:
            continue
        now_iso = datetime.now(timezone.utc).isoformat()
        await db.matchday_deadlines.insert_one({
            "season": season,
            "matchday": md,
            "deadline_at": dt.astimezone(timezone.utc).isoformat(),
            "updated_at": now_iso,
            "updated_by": "backfill",
        })
        copied += 1
    return {"copied": copied, "skipped": skipped}


# =========================================================================
# Router
# =========================================================================

def build_router(*, db, current_user, require_admin) -> APIRouter:
    router = APIRouter(prefix="/deadlines", tags=["deadlines"])

    def _decorate(doc: Dict[str, Any]) -> Dict[str, Any]:
        dt = _parse_stored(doc.get("deadline_at"))
        locked = bool(dt and datetime.now(timezone.utc) >= dt)
        return {**doc, "locked": locked}

    @router.get("")
    async def list_deadlines(
        season: str = DEFAULT_SEASON,
        user: dict = Depends(current_user),
    ):
        """Return all 38 matchdays for the season (unset slots included)."""
        by_md: Dict[int, dict] = {}
        async for d in db.matchday_deadlines.find(
            {"season": season}, {"_id": 0},
        ):
            by_md[d["matchday"]] = d
        rows = []
        for md in range(1, 39):
            d = by_md.get(md, {"season": season, "matchday": md, "deadline_at": None})
            rows.append(_decorate(d))
        return {
            "season": season,
            "server_now": datetime.now(timezone.utc).isoformat(),
            "deadlines": rows,
        }

    @router.get("/current")
    async def current(
        season: str = DEFAULT_SEASON,
        user: dict = Depends(current_user),
    ):
        return await get_current_matchday_info(db, season=season)

    @router.get("/{matchday}")
    async def get_single(
        matchday: int,
        season: str = DEFAULT_SEASON,
        user: dict = Depends(current_user),
    ):
        if matchday < 1 or matchday > 38:
            raise HTTPException(400, "matchday deve essere 1..38")
        d = await db.matchday_deadlines.find_one(
            {"season": season, "matchday": matchday}, {"_id": 0},
        ) or {"season": season, "matchday": matchday, "deadline_at": None}
        return {
            **_decorate(d),
            "server_now": datetime.now(timezone.utc).isoformat(),
        }

    @router.put("/{matchday}")
    async def set_single(
        matchday: int,
        body: DeadlineIn,
        season: str = DEFAULT_SEASON,
        user: dict = Depends(require_admin),
    ):
        if matchday < 1 or matchday > 38:
            raise HTTPException(400, "matchday deve essere 1..38")
        if body.deadline_at is None or body.deadline_at.strip() == "":
            await db.matchday_deadlines.delete_one(
                {"season": season, "matchday": matchday}
            )
            return {
                "season": season, "matchday": matchday,
                "deadline_at": None, "locked": False, "cleared": True,
            }
        dt = _parse_deadline_input(body.deadline_at)
        now_iso = datetime.now(timezone.utc).isoformat()
        await db.matchday_deadlines.update_one(
            {"season": season, "matchday": matchday},
            {"$set": {
                "season": season,
                "matchday": matchday,
                "deadline_at": dt.isoformat(),
                "updated_at": now_iso,
                "updated_by": user["id"],
            }},
            upsert=True,
        )
        return _decorate({
            "season": season, "matchday": matchday,
            "deadline_at": dt.isoformat(),
        })

    @router.post("/bulk")
    async def bulk_set(
        body: BulkIn,
        user: dict = Depends(require_admin),
    ):
        applied = 0
        cleared = 0
        errors: List[dict] = []
        now_iso = datetime.now(timezone.utc).isoformat()
        seen: set = set()
        for d in body.deadlines:
            if d.matchday in seen:
                continue
            seen.add(d.matchday)
            if d.matchday < 1 or d.matchday > 38:
                errors.append({"matchday": d.matchday, "error": "out of range"})
                continue
            if d.deadline_at is None or d.deadline_at.strip() == "":
                r = await db.matchday_deadlines.delete_one(
                    {"season": body.season, "matchday": d.matchday}
                )
                cleared += r.deleted_count
                continue
            try:
                dt = _parse_deadline_input(d.deadline_at)
            except HTTPException as e:
                errors.append({"matchday": d.matchday, "error": str(e.detail)})
                continue
            await db.matchday_deadlines.update_one(
                {"season": body.season, "matchday": d.matchday},
                {"$set": {
                    "season": body.season, "matchday": d.matchday,
                    "deadline_at": dt.isoformat(),
                    "updated_at": now_iso, "updated_by": user["id"],
                }},
                upsert=True,
            )
            applied += 1
        return {"applied": applied, "cleared": cleared, "errors": errors}

    return router
