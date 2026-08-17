"""RinoMagic — FastAPI core: auth, startup wiring, and mounting of the
three mini-game modules.

Historical note: everything below used to live in this file. As of June
2026 each mini-game owns its own module (mirroring how ``scoreandlive`` and
``fantagiornata`` were structured from the start):

* :mod:`thebesttiket`   — betting-slip challenge (rooms, schedine, OCR, ...)
* :mod:`scoreandlive`   — survivor game (goalscorer picks)
* :mod:`fantagiornata`  — one-matchday fantacalcio
* :mod:`matchday_facts` — universal Voti/Marcatori PDF ingestion (truth data
                          consumed by all three games for auto-settlement)

Keeping this file tight makes it easy to add / retire mini-games and it
gives us a single place to reason about global concerns: MongoDB, JWT auth,
CORS, and startup hooks.

For backwards compatibility the OCR + prediction helpers used by the pytest
suite (``ocr_screenshot``, ``_evaluate_prediction``, ``_classify_bet``) are
re-exported from :mod:`thebesttiket` at the bottom of this module.
"""
import os
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

app = FastAPI(title="RinoMagic API")
api = APIRouter(prefix="/api")
security = HTTPBearer(auto_error=False)

logger = logging.getLogger("rinomagic")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)


# =========================================================================
# Auth wiring — the auth router is built first because every game module
# depends on `current_user` / `require_admin`.
# =========================================================================
from auth import build_auth_router, seed_admin_if_missing  # noqa: E402

_auth_router, _current_user_dep = build_auth_router(db)
api.include_router(_auth_router)


async def current_user(user: dict = Depends(_current_user_dep)) -> dict:
    return user


async def require_admin(user: dict = Depends(_current_user_dep)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Solo admin")
    return user


def display_name(user: dict) -> str:
    """Return a friendly display name for a user (username or email prefix)."""
    if user.get("username"):
        return user["username"]
    email = user.get("email") or ""
    return email.split("@")[0] if email else "admin"


# =========================================================================
# Mini-game routers (mounted under /api)
# =========================================================================
# --- TheBestTiket (schedine + games hub) ---------------------------------
from thebesttiket import (  # noqa: E402
    build_router as _build_tbt_router,
    ensure_indexes as _tbt_ensure_indexes,
    backfill_legacy as _tbt_backfill,
    # Re-exports for backwards-compatible tests
    ocr_screenshot,  # noqa: F401
    _evaluate_prediction,  # noqa: F401
    _classify_bet,  # noqa: F401
)
_tbt_router = _build_tbt_router(
    db=db,
    current_user=current_user,
    require_admin=require_admin,
    display_name=display_name,
)
api.include_router(_tbt_router)

# --- ScoreAndLive (survivor tournaments) ---------------------------------
from scoreandlive import (  # noqa: E402
    build_router as _build_sal_router,
    ensure_indexes as _sal_ensure_indexes,
)
_sal_router = _build_sal_router(
    db=db,
    current_user=current_user,
    require_admin=require_admin,
    display_name=display_name,
)
api.include_router(_sal_router)

# --- Matchday Facts (universal Voti/Marcatori PDF ingestion) -------------
from matchday_facts import (  # noqa: E402
    build_router as _build_facts_router,
    ensure_indexes as _facts_ensure_indexes,
)
_facts_router = _build_facts_router(
    db=db,
    current_user=current_user,
    require_admin=require_admin,
)
api.include_router(_facts_router)

# --- FantaGiornata (one-matchday fantacalcio) ----------------------------
from fantagiornata import (  # noqa: E402
    build_router as _build_fg_router,
    ensure_indexes as _fg_ensure_indexes,
)
_fg_router = _build_fg_router(
    db=db,
    current_user=current_user,
    require_admin=require_admin,
    display_name=display_name,
)
api.include_router(_fg_router)

# --- Surviva 2.0 (1X2 elimination tournament) ----------------------------
from surviva import (  # noqa: E402
    build_router as _build_sv_router,
    ensure_indexes as _sv_ensure_indexes,
)
_sv_router = _build_sv_router(
    db=db,
    current_user=current_user,
    require_admin=require_admin,
    display_name=display_name,
)
api.include_router(_sv_router)


# --- Bonus games (5th slot) ----------------------------------------------
from bonus import (  # noqa: E402
    build_router as _build_bonus_router,
    ensure_indexes as _bonus_ensure_indexes,
)
_bonus_router = _build_bonus_router(
    db=db,
    current_user=current_user,
    require_admin=require_admin,
    display_name=display_name,
)
api.include_router(_bonus_router)


from matchday_settle import build_router as _build_settle_router  # noqa: E402
_settle_router = _build_settle_router(
    db=db,
    require_admin=require_admin,
)
api.include_router(_settle_router)


# --- Global Matchday Deadlines (shared timer for all games) --------------
from deadlines import (  # noqa: E402
    build_router as _build_deadlines_router,
    ensure_indexes as _deadlines_ensure_indexes,
    backfill_from_tiket_rooms as _deadlines_backfill,
)
_deadlines_router = _build_deadlines_router(
    db=db,
    current_user=current_user,
    require_admin=require_admin,
)
api.include_router(_deadlines_router)


# =========================================================================
# Web Push (PWA notifications) — VAPID-based
# =========================================================================
from web_push import create_router as _build_push_router  # noqa: E402

_push_router = _build_push_router(
    db=db,
    current_user=current_user,
    current_admin=require_admin,
)
api.include_router(_push_router)


# =========================================================================
# Startup / shutdown
# =========================================================================

@app.on_event("startup")
async def startup():
    # User-level indexes (owned by the auth module conceptually, but they
    # sit in the same collection touched by every game, so we create them
    # up-front here to keep the seed idempotent).
    # NOTE: the production Atlas DB may already contain equivalent indexes
    # with slightly different options (e.g. sparse vs partial). Creating them
    # is best-effort so we never crash startup on an IndexKeySpecsConflict.
    async def _safe_index(coll, *args, **kwargs):
        try:
            await coll.create_index(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("skip index on %s: %s", coll.name, exc)

    await _safe_index(db.users, "id", unique=True)
    await _safe_index(
        db.users, "email", unique=True,
        partialFilterExpression={"email": {"$type": "string"}},
    )
    await _safe_index(
        db.users, "username", unique=True,
        partialFilterExpression={"username": {"$type": "string"}},
    )
    await _safe_index(db.reset_tokens, "token", unique=True)
    await _safe_index(db.reset_tokens, "expires_at", expireAfterSeconds=0)

    # Per-game indexes + backfills (best-effort — existing prod indexes may
    # differ in options; never crash startup on a conflict).
    async def _safe(coro_fn, *a, label=""):
        try:
            await coro_fn(*a)
        except Exception as exc:  # noqa: BLE001
            logger.warning("startup step %s skipped: %s", label or coro_fn, exc)

    await _safe(_tbt_ensure_indexes, db, label="tbt_idx")
    await _safe(_tbt_backfill, db, label="tbt_backfill")
    await _safe(_sal_ensure_indexes, db, label="sal_idx")
    await _safe(_facts_ensure_indexes, db, label="facts_idx")
    await _safe(_fg_ensure_indexes, db, label="fg_idx")
    await _safe(_sv_ensure_indexes, db, label="sv_idx")
    await _safe(_bonus_ensure_indexes, db, label="bonus_idx")

    # Global deadlines: indexes + one-shot backfill from legacy per-room fields
    await _safe(_deadlines_ensure_indexes, db, label="deadlines_idx")
    try:
        stats = await _deadlines_backfill(db)
        if stats["copied"]:
            logger.info("deadlines backfill: %s", stats)
    except Exception:
        logger.exception("deadlines backfill failed")

    await seed_admin_if_missing(db)
    logger.info("RinoMagic API started")


@app.on_event("shutdown")
async def shutdown():
    client.close()


# =========================================================================
# Root / mount
# =========================================================================

@api.get("/")
async def root():
    return {"service": "RinoMagic", "status": "ok"}


app.include_router(api)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================================
# Backwards-compatible re-exports (used by regression tests)
# =========================================================================
# The pytest suite imports these directly:
#   from server import ocr_screenshot, _evaluate_prediction, _classify_bet
# They live in :mod:`thebesttiket` now — keep the aliases so the tests
# don't have to change.
_: Optional[object] = None  # silence linters about unused imports
