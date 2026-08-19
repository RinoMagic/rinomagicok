"""Unified matchday settlement orchestrator (admin console).

This module powers the *Calcola Giornata N* button in the admin settings.
It exposes 2 endpoints:

  • ``POST /api/admin/settle-matchday/preview``
      Given a matchday number + first-scorer info (admin picks manually),
      the voti PDF for that matchday must already be uploaded (via the
      existing ``POST /api/admin/voti`` route → ``matchday_facts``).
      Returns a **dry-run** describing what would be settled across the
      four games (Survival, Score, Tiket, Fanta) plus their bonuses.

  • ``POST /api/admin/settle-matchday/commit``
      Runs the actual settlements: matches are counted, lives deducted,
      bonuses paid out, tournaments auto-advanced to matchday N+1 and
      Tiket rooms archived.

Both endpoints leverage the per-game settlement HTTP endpoints internally
(via ``httpx``) using the caller's admin JWT, so we don't duplicate any
scoring logic and each game keeps its single-source-of-truth in its
own module.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("matchday_settle")


class SettleInput(BaseModel):
    matchday: int = Field(ge=1, le=38)
    season: str = "2026-27"
    # Manual entry: the FIRST scorer of the matchday (needed for the
    # first_scorer bonus which cannot be derived from voti data).
    first_scorer_player_name: Optional[str] = None
    first_scorer_team: Optional[str] = None
    # Manual overrides for fixtures missing/incorrect data in the voti PDF.
    # Each item is {home_team, away_team, home_score, away_score}. This is
    # merged over the PDF-derived fixtures before settlement.
    fixture_overrides: Optional[List[Dict[str, Any]]] = None
    # Postponed / cancelled matches — for each item {home_team, away_team}
    # the following priority rules apply and OVERRIDE the PDF data:
    #   • Tiket: fixture quota forced to 1.00 (both_scored=False, no result)
    #   • Survival: users who picked this match get their life SAVED
    #   • Score: users who picked this match get their life SAVED
    #   • Fanta: every player of the two teams gets a 6.0 "6 politico"
    postponed_matches: Optional[List[Dict[str, Any]]] = None
    # Suspended matches — interrupted with a PARTIAL score. Each item is
    # {home_team, away_team, home_score, away_score} (score at suspension).
    # Betting rules apply: Tiket settles per-market (1X2 void; Over/Gol/etc.
    # only if already decided); Survival/Score save the life (1X2 void).
    suspended_matches: Optional[List[Dict[str, Any]]] = None


# =========================================================================
# Helpers to derive per-fixture scores + scorers from matchday_facts
# =========================================================================
async def _per_team_goals(db, matchday: int) -> Dict[str, Dict[str, int]]:
    """Aggregate goals per team from matchday_facts.

    Returns: ``{team_name: {"scored": int, "conceded": int}}``.
    """
    pipeline = [
        {"$match": {"matchday": matchday}},
        {"$group": {
            "_id": "$team",
            "goals_scored": {"$sum": {"$add": ["$gf", "$rf"]}},
            "gk_goals_conceded": {
                "$sum": {"$cond": [{"$eq": ["$role", "P"]}, "$gs", 0]},
            },
        }},
    ]
    agg = await db.matchday_facts.aggregate(pipeline).to_list(length=None)
    out: Dict[str, Dict[str, int]] = {}
    for row in agg:
        out[row["_id"]] = {
            "scored": int(row.get("goals_scored", 0)),
            "conceded": int(row.get("gk_goals_conceded", 0)),
        }
    return out


def _norm(s: str) -> str:
    return (s or "").strip().lower()


async def _fixtures_with_scores(
    db, matchday: int, season: str,
    overrides: Optional[List[Dict[str, Any]]] = None,
    postponed: Optional[List[Dict[str, Any]]] = None,
    suspended: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Return the matchday fixtures from ``sal_calendar`` with their final
    score derived from ``matchday_facts``. Postponed / unreported matches
    end up with ``home_score = away_score = None``.

    If ``overrides`` is provided, each entry ``{home_team, away_team,
    home_score, away_score}`` REPLACES the auto-derived score for that
    fixture (matched case-insensitively). This lets the admin fill in
    missing/wrong data before commit.

    If ``postponed`` is provided (list of ``{home_team, away_team}``), the
    matching fixtures are forcibly marked as ``postponed=True`` with no
    score — the per-game commit logic then applies the priority rules
    (Tiket quota 1.00, Survival/Score life saved, Fanta 6 politico).
    """
    per_team = await _per_team_goals(db, matchday)
    per_team_norm = {_norm(k): v for k, v in per_team.items()}
    # Index overrides by (home_norm, away_norm) for O(1) lookup
    ov_map: Dict[tuple, Dict[str, int]] = {}
    for ov in (overrides or []):
        try:
            ov_map[(_norm(ov["home_team"]), _norm(ov["away_team"]))] = {
                "home_score": int(ov["home_score"]),
                "away_score": int(ov["away_score"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    post_set = set()
    for p in (postponed or []):
        try:
            post_set.add((_norm(p["home_team"]), _norm(p["away_team"])))
        except (KeyError, TypeError):
            continue
    sus_map: Dict[tuple, Dict[str, int]] = {}
    for s in (suspended or []):
        try:
            sus_map[(_norm(s["home_team"]), _norm(s["away_team"]))] = {
                "home_score": int(s.get("home_score") or 0),
                "away_score": int(s.get("away_score") or 0),
            }
        except (KeyError, TypeError, ValueError):
            continue
    fixtures = []
    async for fx in db.sal_calendar.find(
        {"season": season, "matchday": matchday},
        {"_id": 0, "home_team": 1, "away_team": 1, "kickoff_iso": 1,
         "excluded": 1},
    ):
        home = fx["home_team"]
        away = fx["away_team"]
        is_postponed = (_norm(home), _norm(away)) in post_set
        sus = sus_map.get((_norm(home), _norm(away)))
        is_suspended = sus is not None and not is_postponed
        if is_suspended:
            home_score = sus["home_score"]
            away_score = sus["away_score"]
            manual = True
        elif is_postponed:
            home_score = None
            away_score = None
            manual = False
        else:
            ov = ov_map.get((_norm(home), _norm(away)))
            if ov:
                home_score = ov["home_score"]
                away_score = ov["away_score"]
                manual = True
            else:
                h = per_team_norm.get(_norm(home))
                a = per_team_norm.get(_norm(away))
                home_score = h["scored"] if h else None
                away_score = a["scored"] if a else None
                manual = False
        # A suspended match is NOT "played" (no final result) → 1X2 games save
        # the life; Tiket applies per-market betting rules using the score.
        played = (home_score is not None and away_score is not None
                  and not is_postponed and not is_suspended)
        fixtures.append({
            "home_team": home,
            "away_team": away,
            "home_score": home_score,
            "away_score": away_score,
            "played": played,
            "manual": manual,
            "postponed": is_postponed,
            "suspended": is_suspended,
            "excluded": bool(fx.get("excluded", False)),
        })
    return fixtures


async def _scorers_by_fixture(
    db, matchday: int, fixtures: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Return ``{fixture_key: [{player_id, name, team, goals}]}`` from
    ``matchday_facts`` where ``player.total_goals > 0``.
    """
    fx_map = {_norm(fx["home_team"]): fx for fx in fixtures}
    fx_map.update({_norm(fx["away_team"]): fx for fx in fixtures})
    out: Dict[str, List[Dict[str, Any]]] = {}
    async for row in db.matchday_facts.find(
        {"matchday": matchday, "total_goals": {"$gt": 0}},
        {"_id": 0, "player_name": 1, "team": 1, "total_goals": 1,
         "player_code": 1, "role": 1},
    ):
        fx = fx_map.get(_norm(row["team"]))
        if not fx:
            continue
        key = f"{fx['home_team']}||{fx['away_team']}"
        out.setdefault(key, []).append({
            "player_code": row.get("player_code"),
            "player_name": row.get("player_name"),
            "team": row.get("team"),
            "goals": int(row.get("total_goals", 0)),
        })
    return out


async def _list_scorers(db, matchday: int) -> List[Dict[str, Any]]:
    scorers = []
    async for row in db.matchday_facts.find(
        {"matchday": matchday, "total_goals": {"$gt": 0}},
        {"_id": 0, "player_name": 1, "team": 1, "total_goals": 1,
         "player_code": 1, "role": 1, "voto": 1},
    ):
        scorers.append({
            "player_code": row.get("player_code"),
            "player_name": row.get("player_name"),
            "team": row.get("team"),
            "role": row.get("role"),
            "goals": int(row.get("total_goals", 0)),
            "voto": row.get("voto"),
        })
    # Sort: goals desc then name asc
    scorers.sort(key=lambda x: (-x["goals"], x["player_name"] or ""))
    return scorers


# =========================================================================
# Router
# =========================================================================
def build_router(*, db, require_admin) -> APIRouter:
    router = APIRouter(prefix="/admin/settle-matchday", tags=["admin.settle"])

    @router.get("/state")
    async def get_state(
        matchday: int, season: str = "2026-27",
        user: dict = Depends(require_admin),
    ):
        """Return the current settlement state for the given matchday:
        whether voti data is loaded, which games have open work.
        """
        # Voti loaded?
        voti_count = await db.matchday_facts.count_documents({"matchday": matchday})
        # Affected entities
        sv_tourn = await db.sv_tournaments.count_documents({
            "season": season, "current_matchday": matchday,
            "status": {"$ne": "closed"},
        })
        sal_tourn = 0
        try:
            sal_tourn = await db.sal_tournaments.count_documents({
                "season": season, "status": {"$in": ["active", "open", None]},
            })
        except Exception:
            pass
        tiket_rooms = await db.rooms.count_documents({
            "matchday": matchday, "status": {"$ne": "settled"},
        })
        # Fanta leagues to settle: a league is "affected" if it has at
        # least one lineup for the given matchday and is still open.
        # ``current_matchday`` on the league doc is often not kept in sync
        # (users may save lineups without updating that field), so we go
        # through ``fg_lineups`` for the ground truth.
        fanta_league_ids = await db.fg_lineups.distinct(
            "league_id", {"matchday": matchday},
        )
        if fanta_league_ids:
            fanta_leagues = await db.fg_leagues.count_documents({
                "id": {"$in": fanta_league_ids},
                "status": {"$ne": "closed"},
            })
        else:
            fanta_leagues = 0
        bonus_open = await db.bonus_configs.count_documents({
            "season": season, "matchday": matchday, "settled_at": None,
        })
        return {
            "matchday": matchday,
            "season": season,
            "voti_loaded": voti_count > 0,
            "voti_rows": voti_count,
            "affected": {
                "survival_tournaments": sv_tourn,
                "score_tournaments": sal_tourn,
                "tiket_rooms": tiket_rooms,
                "fanta_leagues": fanta_leagues,
                "bonus_configs_open": bonus_open,
            },
        }

    @router.post("/preview")
    async def preview(
        body: SettleInput, user: dict = Depends(require_admin),
    ):
        """Dry-run the settlement — nothing is written, just report what
        would happen.
        """
        matchday = body.matchday
        season = body.season

        voti_count = await db.matchday_facts.count_documents({"matchday": matchday})
        if voti_count == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Nessun dato voti caricato per la giornata {matchday}. "
                    "Carica prima il PDF Voti dal pannello admin."
                ),
            )

        fixtures = await _fixtures_with_scores(
            db, matchday, season, body.fixture_overrides, body.postponed_matches,
            body.suspended_matches,
        )
        played = [fx for fx in fixtures if fx["played"]]
        postponed = [fx for fx in fixtures if not fx["played"]]
        scorers = await _list_scorers(db, matchday)

        # Big Match (from active exact_score bonus for this matchday)
        big_match_cfg = await db.bonus_configs.find_one(
            {"season": season, "matchday": matchday,
             "bonus_type": "exact_score", "settled_at": None},
            {"_id": 0},
        )
        big_match_fx = None
        if big_match_cfg and big_match_cfg.get("big_match"):
            bm = big_match_cfg["big_match"]
            found = next(
                (f for f in fixtures
                 if _norm(f["home_team"]) == _norm(bm["home_team"])
                 and _norm(f["away_team"]) == _norm(bm["away_team"])),
                None,
            )
            big_match_fx = {
                "home_team": bm["home_team"],
                "away_team": bm["away_team"],
                "home_score": found["home_score"] if found else None,
                "away_score": found["away_score"] if found else None,
                "played": bool(found and found["played"]),
            }

        first_scorer_cfg = await db.bonus_configs.find_one(
            {"season": season, "matchday": matchday,
             "bonus_type": "first_scorer", "settled_at": None},
            {"_id": 0},
        )

        # Affected counts
        state_resp = await get_state(matchday, season, user)  # type: ignore
        return {
            "matchday": matchday,
            "season": season,
            "fixtures": {
                "total": len(fixtures),
                "played": len(played),
                "postponed": len(postponed),
                "list": fixtures,
            },
            "scorers": scorers,
            "big_match": big_match_fx,
            "big_match_bonus_open": bool(big_match_cfg),
            "first_scorer_bonus_open": bool(first_scorer_cfg),
            "first_scorer_input": {
                "player_name": body.first_scorer_player_name,
                "team": body.first_scorer_team,
            },
            "affected": state_resp["affected"],
            "warnings": _collect_warnings(
                fixtures, big_match_cfg, big_match_fx,
                first_scorer_cfg, body,
            ),
        }

    @router.post("/commit")
    async def commit(
        body: SettleInput,
        request: Request,
        user: dict = Depends(require_admin),
    ):
        """Actually settle every open entity for the matchday. Uses
        internal HTTP calls to the per-game settle endpoints, so the
        single-source-of-truth scoring logic stays in each game module.
        """
        matchday = body.matchday
        season = body.season

        voti_count = await db.matchday_facts.count_documents({"matchday": matchday})
        if voti_count == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Nessun dato voti caricato per la giornata {matchday}. "
                    "Carica prima il PDF Voti."
                ),
            )

        fixtures = await _fixtures_with_scores(
            db, matchday, season, body.fixture_overrides, body.postponed_matches,
            body.suspended_matches,
        )
        scorers_map = await _scorers_by_fixture(db, matchday, fixtures)

        # Build an internal HTTP client that uses the caller's admin JWT.
        auth = request.headers.get("Authorization")
        if not auth:
            raise HTTPException(status_code=401, detail="Missing auth token")
        base_url = os.environ.get("INTERNAL_API_BASE", "http://localhost:8001")
        api = f"{base_url}/api"
        client = httpx.AsyncClient(
            timeout=60, headers={"Authorization": auth},
        )
        log: List[Dict[str, Any]] = []

        try:
            # ---- Survival tournaments (season, current_matchday=N) -----
            async for t in db.sv_tournaments.find(
                {"season": season, "current_matchday": matchday,
                 "status": {"$ne": "closed"}},
                {"_id": 0, "id": 1, "name": 1},
            ):
                md_doc = await db.sv_matchdays.find_one(
                    {"tournament_id": t["id"], "matchday": matchday},
                    {"_id": 0, "id": 1, "status": 1},
                )
                if not md_doc or md_doc.get("status") == "settled":
                    log.append({"game": "survival", "tournament": t["name"],
                                "skipped": True, "reason": "già liquidato"})
                    continue
                # Postponed fixtures are included with ``postponed=True`` so
                # picks on those matches remain PENDING (no life lost /
                # gained). This is Survival's built-in life-save behaviour.
                results = []
                for fx in fixtures:
                    if fx.get("postponed"):
                        results.append({
                            "home_team": fx["home_team"],
                            "away_team": fx["away_team"],
                            "postponed": True,
                        })
                    elif fx["played"]:
                        results.append({
                            "home_team": fx["home_team"],
                            "away_team": fx["away_team"],
                            "home_score": fx["home_score"] or 0,
                            "away_score": fx["away_score"] or 0,
                        })
                r = await client.post(
                    f"{api}/sv/tournaments/{t['id']}/matchdays/{md_doc['id']}/settle",
                    json={"results": results},
                )
                body = r.json() if r.status_code == 200 else None
                entry = {
                    "game": "survival", "tournament": t["name"],
                    "status": r.status_code,
                    "detail": body if body else r.text[:200],
                }
                if body:
                    bm_count = int(body.get("big_match_bonus_count") or 0)
                    if bm_count > 0:
                        entry["big_match_bonus_count"] = bm_count
                log.append(entry)

            # ---- Score tournaments ------------------------------------
            async for t in db.sal_tournaments.find(
                {"season": season, "status": {"$in": ["active", "open", None]}},
                {"_id": 0, "id": 1, "name": 1},
            ):
                md_doc = await db.sal_matchdays.find_one(
                    {"tournament_id": t["id"], "matchday_number": matchday},
                    {"_id": 0, "id": 1, "status": 1, "fixtures": 1},
                )
                if not md_doc or md_doc.get("status") == "settled":
                    log.append({"game": "score", "tournament": t["name"],
                                "skipped": True, "reason": "già liquidato o assente"})
                    continue
                # Build a set of postponed fixture keys (home||away)
                postponed_keys = {
                    f"{fx['home_team']}||{fx['away_team']}"
                    for fx in fixtures if fx.get("postponed")
                }
                # Map postponed keys to the tournament's per-md fixture idx
                # so Score's settle receives ``postponed_during`` correctly.
                postponed_during: List[int] = []
                sco = []
                for fx in md_doc.get("fixtures", []):
                    key = f"{fx['home_team']}||{fx['away_team']}"
                    if key in postponed_keys:
                        postponed_during.append(fx["idx"])
                        continue  # skip scorers for postponed
                    for s in scorers_map.get(key, []):
                        sco.append({
                            "fixture_idx": fx["idx"],
                            "player_name": s["player_name"],
                            "team": s["team"],
                        })
                r = await client.post(
                    f"{api}/sal/tournaments/{t['id']}/matchdays/{md_doc['id']}/settle",
                    json={"scorers": sco, "postponed_during": postponed_during},
                )
                log.append({"game": "score", "tournament": t["name"],
                            "status": r.status_code,
                            "detail": r.json() if r.status_code == 200 else r.text[:200]})

            # ---- Tiket rooms -----------------------------------------
            # Tiket has no ``/settle`` endpoint — settlement happens by
            # writing fixture scores into ``fixtures`` collection. For
            # postponed matches we still write a placeholder with
            # ``postponed=True`` and ``both_scored=False`` so the schedina
            # ranking treats predictions on it as neutral (quota 1.00).
            async for room in db.rooms.find(
                {"matchday": matchday, "status": {"$ne": "settled"}},
                {"_id": 0, "id": 1, "name": 1},
            ):
                room_fx = []
                for fx in fixtures:
                    if fx.get("excluded"):
                        continue  # Excluded pre-round → do not create schedina event
                    if fx.get("suspended"):
                        room_fx.append({
                            "home_team": fx["home_team"],
                            "away_team": fx["away_team"],
                            "home_score": fx["home_score"] or 0,
                            "away_score": fx["away_score"] or 0,
                            "both_scored": (fx["home_score"] or 0) > 0
                                           and (fx["away_score"] or 0) > 0,
                            "suspended": True,
                        })
                    elif fx.get("postponed"):
                        room_fx.append({
                            "home_team": fx["home_team"],
                            "away_team": fx["away_team"],
                            "home_score": 0, "away_score": 0,
                            "both_scored": False,
                            "postponed": True,
                        })
                    elif fx["played"]:
                        room_fx.append({
                            "home_team": fx["home_team"],
                            "away_team": fx["away_team"],
                            "home_score": fx["home_score"] or 0,
                            "away_score": fx["away_score"] or 0,
                            "both_scored": (fx["home_score"] or 0) > 0
                                           and (fx["away_score"] or 0) > 0,
                        })
                r = await client.post(
                    f"{api}/rooms/{room['id']}/fixtures",
                    json={"fixtures": room_fx},
                )
                if r.status_code == 200:
                    # Room is now settled → shows as "Concluso" in the UI.
                    await db.rooms.update_one(
                        {"id": room["id"]}, {"$set": {"status": "settled"}},
                    )
                log.append({"game": "tiket", "room": room["name"],
                            "status": r.status_code,
                            "detail": r.json() if r.status_code == 200 else r.text[:200]})

            # ---- Fanta leagues ---------------------------------------
            # Same rule as _count_affected: a league needs settling if it
            # has any lineup for this matchday and is still open.
            fanta_league_ids = await db.fg_lineups.distinct(
                "league_id", {"matchday": matchday},
            )
            async for lg in db.fg_leagues.find(
                {"id": {"$in": fanta_league_ids or []},
                 "status": {"$ne": "closed"}},
                {"_id": 0, "id": 1, "name": 1},
            ):
                r = await client.post(
                    f"{api}/fg/leagues/{lg['id']}/settle",
                    json={"matchday": matchday},
                )
                log.append({"game": "fanta", "league": lg["name"],
                            "status": r.status_code,
                            "detail": r.json() if r.status_code == 200 else r.text[:200]})

            # ---- Bonus: exact_score (Big Match) ----------------------
            bm_cfg = await db.bonus_configs.find_one(
                {"season": season, "matchday": matchday,
                 "bonus_type": "exact_score", "settled_at": None},
                {"_id": 0},
            )
            if bm_cfg and bm_cfg.get("big_match"):
                bm = bm_cfg["big_match"]
                fx = next(
                    (f for f in fixtures
                     if _norm(f["home_team"]) == _norm(bm["home_team"])
                     and _norm(f["away_team"]) == _norm(bm["away_team"])),
                    None,
                )
                if fx and fx["played"]:
                    r = await client.post(
                        f"{api}/bonus/configs/{bm_cfg['id']}/settle-exact",
                        json={"home_score": fx["home_score"],
                              "away_score": fx["away_score"]},
                    )
                    log.append({"game": "bonus_exact",
                                "config_id": bm_cfg["id"],
                                "status": r.status_code,
                                "detail": r.json() if r.status_code == 200 else r.text[:200]})
                else:
                    log.append({"game": "bonus_exact", "skipped": True,
                                "reason": "Big Match non giocata"})

            # ---- Bonus: first_scorer --------------------------------
            fs_cfg = await db.bonus_configs.find_one(
                {"season": season, "matchday": matchday,
                 "bonus_type": "first_scorer", "settled_at": None},
                {"_id": 0},
            )
            if fs_cfg:
                if body.first_scorer_player_name:
                    r = await client.post(
                        f"{api}/bonus/configs/{fs_cfg['id']}/settle-scorer",
                        json={"player_name": body.first_scorer_player_name},
                    )
                    log.append({"game": "bonus_first_scorer",
                                "config_id": fs_cfg["id"],
                                "status": r.status_code,
                                "detail": r.json() if r.status_code == 200 else r.text[:200]})
                else:
                    log.append({"game": "bonus_first_scorer", "skipped": True,
                                "reason": "Primo marcatore non specificato"})
        finally:
            await client.aclose()

        summary = _summarize_log(log)
        logger.info("Matchday %s settled: %s", matchday, summary)
        return {
            "matchday": matchday, "season": season,
            "log": log, "summary": summary,
        }

    return router


def _collect_warnings(
    fixtures: List[Dict[str, Any]],
    big_match_cfg: Optional[Dict[str, Any]],
    big_match_fx: Optional[Dict[str, Any]],
    first_scorer_cfg: Optional[Dict[str, Any]],
    body: SettleInput,
) -> List[str]:
    w: List[str] = []
    postponed = [fx for fx in fixtures if not fx["played"]]
    if postponed:
        names = ", ".join(f"{fx['home_team']}-{fx['away_team']}" for fx in postponed[:5])
        w.append(
            f"{len(postponed)} partita/e senza dati (rinviate?): {names}"
            + (" …" if len(postponed) > 5 else ""),
        )
    if big_match_cfg and not big_match_fx:
        w.append("Bonus Big Match attivo ma la partita non è in calendario.")
    if big_match_cfg and big_match_fx and not big_match_fx["played"]:
        w.append(
            f"Bonus Big Match {big_match_fx['home_team']}-{big_match_fx['away_team']} "
            "non giocata: il bonus NON verrà liquidato adesso.",
        )
    if first_scorer_cfg and not body.first_scorer_player_name:
        w.append(
            "Bonus Primo Marcatore attivo: inserisci il nome del primo marcatore "
            "prima di premere «Salva».",
        )
    return w


def _summarize_log(log: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {"survival": 0, "score": 0, "tiket": 0, "fanta": 0,
              "bonus_exact": 0, "bonus_first_scorer": 0}
    skipped = 0
    errors = 0
    for row in log:
        if row.get("skipped"):
            skipped += 1
            continue
        if row.get("status") not in (200, 201):
            errors += 1
            continue
        g = row.get("game", "")
        if g in counts:
            counts[g] += 1
    return {"settled": counts, "skipped": skipped, "errors": errors,
            "total": len(log)}


__all__ = ["build_router"]
