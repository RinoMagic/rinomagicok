import os
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from fastapi import FastAPI, APIRouter, Depends, HTTPException, Query
from starlette.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import get_db, close_db
from auth import authenticate, get_current_user, require_admin
from web_push import push_router, send_push_to_all

SEASON = "2026-27"

app = FastAPI(title="Schedina Bar API")
api = APIRouter(prefix="/api")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(doc: dict) -> dict:
    if doc is None:
        return None
    doc.pop("_id", None)
    return doc


# ----------------------------- Models -----------------------------
class LoginRequest(BaseModel):
    identifier: str
    password: str


class TiketRoundCreate(BaseModel):
    matchday: int
    deadline: str
    big_match_fixture_id: Optional[str] = None


class TiketResults(BaseModel):
    results: Dict[str, str]  # fixture_id -> "1"/"X"/"2"


class SchedinaSubmit(BaseModel):
    predictions: Dict[str, str]  # fixture_id -> "1"/"X"/"2"
    big_match_bonus: bool = False


class SurvivalCreate(BaseModel):
    name: str
    start_matchday: int = 1


class SurvivalPick(BaseModel):
    matchday: int
    team: str


class SurvivalResolve(BaseModel):
    matchday: int
    results: Dict[str, str]  # fixture_id -> "1"/"X"/"2"


# ----------------------------- Auth -----------------------------
@api.post("/auth/login")
async def login(body: LoginRequest):
    return await authenticate(body.identifier, body.password)


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


# ----------------------------- Serie A reference -----------------------------
@api.get("/serie-a/matchdays")
async def serie_a_matchdays():
    db = get_db()
    mds = await db.sal_calendar.distinct("matchday", {"season": SEASON})
    return {"season": SEASON, "matchdays": sorted([m for m in mds if isinstance(m, int)])}


@api.get("/serie-a/teams")
async def serie_a_teams():
    db = get_db()
    teams = await db.sal_calendar.distinct("home_team", {"season": SEASON})
    return {"teams": sorted(teams)}


@api.get("/serie-a/calendar")
async def serie_a_calendar(matchday: Optional[int] = Query(default=None)):
    db = get_db()
    q = {"season": SEASON}
    if matchday is not None:
        q["matchday"] = matchday
    docs = await db.sal_calendar.find(q, {"_id": 0}).to_list(500)
    docs.sort(key=lambda d: (d.get("matchday", 0), d.get("home_team", "")))
    return {"season": SEASON, "matches": docs}


@api.get("/players")
async def players(
    role: Optional[str] = None,
    team: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 60,
    skip: int = 0,
):
    db = get_db()
    q = {"active": True}
    if role:
        q["role"] = role
    if team:
        q["team"] = team
    if search:
        q["full_name"] = {"$regex": search, "$options": "i"}
    total = await db.sal_players.count_documents(q)
    docs = (
        await db.sal_players.find(q, {"_id": 0})
        .sort([("price_current", -1)])
        .skip(skip)
        .limit(min(limit, 200))
        .to_list(200)
    )
    return {"total": total, "players": docs}


async def _matchday_fixtures(matchday: int) -> List[dict]:
    db = get_db()
    docs = await db.sal_calendar.find(
        {"season": SEASON, "matchday": matchday}, {"_id": 0}
    ).to_list(50)
    docs.sort(key=lambda d: d.get("home_team", ""))
    return docs


def _winner_from_result(fixture: dict, result: str) -> Optional[str]:
    if result == "1":
        return fixture.get("home_team")
    if result == "2":
        return fixture.get("away_team")
    return None  # draw -> no winner


# ----------------------------- TIKET -----------------------------
@api.get("/tiket/rounds")
async def tiket_rounds(user: dict = Depends(get_current_user)):
    db = get_db()
    rounds = await db.tiket_rounds.find({}, {"_id": 0}).sort([("matchday", -1)]).to_list(100)
    return {"rounds": rounds}


@api.get("/tiket/rounds/{round_id}")
async def tiket_round_detail(round_id: str, user: dict = Depends(get_current_user)):
    db = get_db()
    rnd = clean(await db.tiket_rounds.find_one({"id": round_id}))
    if not rnd:
        raise HTTPException(404, "Giornata non trovata")
    my = clean(await db.tiket_schedine.find_one({"round_id": round_id, "user_id": user["id"]}))
    return {"round": rnd, "my_schedina": my}


@api.post("/tiket/rounds")
async def tiket_create_round(body: TiketRoundCreate, admin: dict = Depends(require_admin)):
    db = get_db()
    fixtures = await _matchday_fixtures(body.matchday)
    if not fixtures:
        raise HTTPException(400, "Nessuna partita per questa giornata")
    existing = await db.tiket_rounds.find_one({"matchday": body.matchday})
    if existing:
        raise HTTPException(400, "Giornata Tiket già creata")
    big = body.big_match_fixture_id or fixtures[0]["id"]
    rnd = {
        "id": str(uuid.uuid4()),
        "season": SEASON,
        "matchday": body.matchday,
        "deadline": body.deadline,
        "big_match_fixture_id": big,
        "status": "open",
        "fixtures": fixtures,
        "results": {},
        "created_at": now_iso(),
    }
    await db.tiket_rounds.insert_one(dict(rnd))
    await send_push_to_all(
        "Nuova Schedina Tiket",
        f"Giornata {body.matchday} aperta! Compila la tua schedina.",
        "/tiket",
    )
    return clean(rnd)


@api.post("/tiket/rounds/{round_id}/schedina")
async def tiket_submit(round_id: str, body: SchedinaSubmit, user: dict = Depends(get_current_user)):
    db = get_db()
    rnd = await db.tiket_rounds.find_one({"id": round_id})
    if not rnd:
        raise HTTPException(404, "Giornata non trovata")
    if rnd.get("status") != "open":
        raise HTTPException(400, "Giornata chiusa")
    try:
        if datetime.fromisoformat(rnd["deadline"]) < datetime.now(timezone.utc):
            raise HTTPException(400, "Deadline superata")
    except HTTPException:
        raise
    except Exception:
        pass
    valid_ids = {f["id"] for f in rnd["fixtures"]}
    if len(body.predictions) != len(valid_ids):
        raise HTTPException(400, "Devi pronosticare tutte le partite")
    for fid, val in body.predictions.items():
        if fid not in valid_ids or val not in ("1", "X", "2"):
            raise HTTPException(400, "Pronostico non valido")
    doc = {
        "round_id": round_id,
        "user_id": user["id"],
        "nickname": user["nickname"],
        "matchday": rnd["matchday"],
        "predictions": body.predictions,
        "big_match_bonus": body.big_match_bonus,
        "points": None,
        "updated_at": now_iso(),
    }
    await db.tiket_schedine.update_one(
        {"round_id": round_id, "user_id": user["id"]},
        {"$set": doc, "$setOnInsert": {"id": str(uuid.uuid4())}},
        upsert=True,
    )
    return {"ok": True}


@api.post("/tiket/rounds/{round_id}/results")
async def tiket_set_results(round_id: str, body: TiketResults, admin: dict = Depends(require_admin)):
    db = get_db()
    rnd = await db.tiket_rounds.find_one({"id": round_id})
    if not rnd:
        raise HTTPException(404, "Giornata non trovata")
    big = rnd.get("big_match_fixture_id")
    # score all schedine
    async for s in db.tiket_schedine.find({"round_id": round_id}):
        pts = 0
        for fid, res in body.results.items():
            pred = s.get("predictions", {}).get(fid)
            if pred and pred == res:
                if fid == big:
                    pts += 3 if s.get("big_match_bonus") else 2
                else:
                    pts += 1
        await db.tiket_schedine.update_one({"_id": s["_id"]}, {"$set": {"points": pts}})
    await db.tiket_rounds.update_one(
        {"id": round_id}, {"$set": {"results": body.results, "status": "scored"}}
    )
    await send_push_to_all(
        "Risultati Tiket",
        f"Giornata {rnd['matchday']} calcolata. Controlla la classifica!",
        "/tiket",
    )
    return {"ok": True}


@api.get("/tiket/standings")
async def tiket_standings(user: dict = Depends(get_current_user)):
    db = get_db()
    agg = {}
    async for s in db.tiket_schedine.find({"points": {"$ne": None}}):
        uid = s["user_id"]
        if uid not in agg:
            agg[uid] = {"user_id": uid, "nickname": s.get("nickname"), "points": 0, "played": 0}
        agg[uid]["points"] += s.get("points") or 0
        agg[uid]["played"] += 1
    rows = sorted(agg.values(), key=lambda r: r["points"], reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return {"standings": rows}


# ----------------------------- SURVIVAL -----------------------------
@api.get("/survival/tournaments")
async def survival_list(user: dict = Depends(get_current_user)):
    db = get_db()
    tours = await db.sv_games.find({}, {"_id": 0}).sort([("created_at", -1)]).to_list(100)
    for t in tours:
        t["participant_count"] = await db.sv_entries.count_documents({"tournament_id": t["id"]})
        me = await db.sv_entries.find_one({"tournament_id": t["id"], "user_id": user["id"]})
        t["joined"] = me is not None
    return {"tournaments": tours}


@api.post("/survival/tournaments")
async def survival_create(body: SurvivalCreate, admin: dict = Depends(require_admin)):
    db = get_db()
    t = {
        "id": str(uuid.uuid4()),
        "name": body.name,
        "season": SEASON,
        "current_matchday": body.start_matchday,
        "start_matchday": body.start_matchday,
        "status": "open",
        "created_by": admin["id"],
        "created_at": now_iso(),
    }
    await db.sv_games.insert_one(dict(t))
    await send_push_to_all("Nuovo torneo Survival", f"'{body.name}' è aperto. Iscriviti!", "/survival")
    return clean(t)


@api.post("/survival/tournaments/{tid}/join")
async def survival_join(tid: str, user: dict = Depends(get_current_user)):
    db = get_db()
    t = await db.sv_games.find_one({"id": tid})
    if not t:
        raise HTTPException(404, "Torneo non trovato")
    existing = await db.sv_entries.find_one({"tournament_id": tid, "user_id": user["id"]})
    if existing:
        return {"ok": True}
    entry = {
        "id": str(uuid.uuid4()),
        "tournament_id": tid,
        "user_id": user["id"],
        "nickname": user["nickname"],
        "status": "alive",
        "used_teams": [],
        "eliminated_matchday": None,
        "created_at": now_iso(),
    }
    await db.sv_entries.insert_one(dict(entry))
    return {"ok": True}


@api.get("/survival/tournaments/{tid}")
async def survival_detail(tid: str, user: dict = Depends(get_current_user)):
    db = get_db()
    t = clean(await db.sv_games.find_one({"id": tid}))
    if not t:
        raise HTTPException(404, "Torneo non trovato")
    entries = await db.sv_entries.find({"tournament_id": tid}, {"_id": 0}).to_list(200)
    entries.sort(key=lambda e: (e["status"] != "alive", e.get("eliminated_matchday") or 999))
    my = next((e for e in entries if e["user_id"] == user["id"]), None)
    fixtures = await _matchday_fixtures(t["current_matchday"])
    my_pick = None
    if my:
        my_pick = clean(
            await db.sv_picks.find_one(
                {"tournament_id": tid, "user_id": user["id"], "matchday": t["current_matchday"]}
            )
        )
    return {
        "tournament": t,
        "entries": entries,
        "my_entry": my,
        "current_fixtures": fixtures,
        "my_pick": my_pick,
    }


@api.post("/survival/tournaments/{tid}/pick")
async def survival_pick(tid: str, body: SurvivalPick, user: dict = Depends(get_current_user)):
    db = get_db()
    t = await db.sv_games.find_one({"id": tid})
    if not t:
        raise HTTPException(404, "Torneo non trovato")
    entry = await db.sv_entries.find_one({"tournament_id": tid, "user_id": user["id"]})
    if not entry:
        raise HTTPException(400, "Non sei iscritto a questo torneo")
    if entry.get("status") != "alive":
        raise HTTPException(400, "Sei stato eliminato")
    if body.matchday != t["current_matchday"]:
        raise HTTPException(400, "Giornata non attiva")
    if body.team in entry.get("used_teams", []):
        raise HTTPException(400, "Hai già usato questa squadra")
    fixtures = await _matchday_fixtures(body.matchday)
    teams = {f["home_team"] for f in fixtures} | {f["away_team"] for f in fixtures}
    if body.team not in teams:
        raise HTTPException(400, "Squadra non in calendario")
    doc = {
        "tournament_id": tid,
        "user_id": user["id"],
        "nickname": user["nickname"],
        "matchday": body.matchday,
        "team": body.team,
        "result": "pending",
        "updated_at": now_iso(),
    }
    await db.sv_picks.update_one(
        {"tournament_id": tid, "user_id": user["id"], "matchday": body.matchday},
        {"$set": doc, "$setOnInsert": {"id": str(uuid.uuid4())}},
        upsert=True,
    )
    return {"ok": True}


@api.post("/survival/tournaments/{tid}/resolve")
async def survival_resolve(tid: str, body: SurvivalResolve, admin: dict = Depends(require_admin)):
    db = get_db()
    t = await db.sv_games.find_one({"id": tid})
    if not t:
        raise HTTPException(404, "Torneo non trovato")
    if body.matchday != t.get("current_matchday"):
        raise HTTPException(400, "Puoi risolvere solo la giornata corrente")
    fixtures = await _matchday_fixtures(body.matchday)
    fixture_map = {f["id"]: f for f in fixtures}
    winners = set()
    for fid, res in body.results.items():
        f = fixture_map.get(fid)
        if f:
            w = _winner_from_result(f, res)
            if w:
                winners.add(w)
    # resolve picks
    async for pick in db.sv_picks.find({"tournament_id": tid, "matchday": body.matchday}):
        survived = pick["team"] in winners
        await db.sv_picks.update_one(
            {"_id": pick["_id"]},
            {"$set": {"result": "survived" if survived else "eliminated"}},
        )
        entry = await db.sv_entries.find_one({"tournament_id": tid, "user_id": pick["user_id"]})
        if entry and entry.get("status") == "alive":
            used = entry.get("used_teams", [])
            if pick["team"] not in used:
                used = used + [pick["team"]]
            update = {"used_teams": used}
            if not survived:
                update["status"] = "eliminated"
                update["eliminated_matchday"] = body.matchday
            await db.sv_entries.update_one({"_id": entry["_id"]}, {"$set": update})
    # eliminate alive players who did NOT pick
    async for entry in db.sv_entries.find({"tournament_id": tid, "status": "alive"}):
        pick = await db.sv_picks.find_one(
            {"tournament_id": tid, "user_id": entry["user_id"], "matchday": body.matchday}
        )
        if not pick:
            await db.sv_entries.update_one(
                {"_id": entry["_id"]},
                {"$set": {"status": "eliminated", "eliminated_matchday": body.matchday}},
            )
    await db.sv_games.update_one(
        {"id": tid},
        {"$set": {"current_matchday": body.matchday + 1, f"resolved.{body.matchday}": body.results}},
    )
    alive = await db.sv_entries.count_documents({"tournament_id": tid, "status": "alive"})
    await send_push_to_all(
        "Survival aggiornato",
        f"Giornata {body.matchday} risolta. Superstiti rimasti: {alive}.",
        "/survival",
    )
    return {"ok": True, "alive": alive}


@api.get("/")
async def root():
    return {"message": "Schedina Bar API", "season": SEASON}


app.include_router(api)
app.include_router(push_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown():
    close_db()
