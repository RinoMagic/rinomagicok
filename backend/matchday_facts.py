"""Matchday Facts — universal source of truth for match ratings, goals & bookings.

This module ingests the "Voti Fantacalcio" PDF (weekly report distributed by
fantacalcio.it) and stores structured per-player facts for a given Serie A
matchday. These facts feed multiple RinoMagic sub-games:

* **ScoreAndLive** — uses the scorers list to auto-settle picks.
* **TheBestTiket** — uses the fixtures (derived from goals + team layout) to
  settle bet slips (planned).
* **FantaGiornata** — uses the full ratings (voto + bonuses/mali) to compute
  fantavoto (planned).

MongoDB collection: ``matchday_facts``

Document schema::

    {
      id: str,                # uuid
      matchday: int,          # 1..38 (Serie A giornata)
      team: str,              # canonical Italian team name
      player_code: int,       # fantacalcio.it stable player ID ("Cod.")
      player_name: str,       # last name (as in the PDF)
      role: str,              # P | D | C | A | ALL (allenatore)
      voto: float | None,     # fantavoto (None if not graded)
      sv: bool,               # senza voto (marked with * in the PDF)
      gf: int,                # gol fatti (open play)
      gs: int,                # gol subiti (portieri)
      rp: int,                # rigori parati (portieri)
      rs: int,                # rigori sbagliati
      rf: int,                # rigori segnati
      au: int,                # autogol
      amm: int,               # ammonizioni
      esp: int,               # espulsioni
      ass: int,               # assist
      total_goals: int,       # gf + rf (used to identify scorers)
      created_at: iso str,
      updated_at: iso str,
    }

Unique index on (matchday, team, player_code).
"""
from __future__ import annotations

import io
import re
import uuid
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Callable, Tuple

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

logger = logging.getLogger("matchday_facts")

# Serie A + historical variants encountered in past PDFs (last ~5 seasons).
SERIE_A_TEAMS = {
    "Atalanta", "Bologna", "Cagliari", "Como", "Cremonese", "Fiorentina",
    "Genoa", "Inter", "Juventus", "Lazio", "Lecce", "Milan", "Napoli",
    "Parma", "Pisa", "Roma", "Sassuolo", "Torino", "Udinese", "Verona",
    # historical / possibly-recurring variants (last ~5 seasons)
    "Hellas Verona", "Empoli", "Monza", "Frosinone", "Salernitana", "Venezia",
    "Spezia", "Sampdoria", "Benevento", "Brescia", "Chievo", "Spal",
    "Palermo", "Novara", "Livorno", "Bari",
}
_SERIE_A_LOWER = {t.lower(): t for t in SERIE_A_TEAMS}

# Canonicalize team labels (e.g. "Hellas Verona" -> "Verona") if desired later.
TEAM_ALIASES = {
    "Hellas Verona": "Verona",
}

ROLE_TOKENS = {"P", "D", "C", "A", "ALL"}


# =========================================================================
# PDF Parser
# =========================================================================

# Player row: <code> <role> <name...> <voto> <gf> <gs> <rp> <rs> <rf> <au> [<amm> <esp> <ass>]
# `voto` supports comma decimals and optional trailing `*` (senza voto marker).
# Two PDF formats are supported:
#   - long  (13 fields): includes Amm / Esp / Ass at the tail
#   - short (10 fields): the older/light Fantacalcio.it layout (Gf..Au only)
# Trailing whitespace / extra spurious tokens tolerated with `\s*.*$`.
_ROW_RE = re.compile(
    r"^(\d+)\s+(P|D|C|A|ALL)\s+(.+?)\s+([\d]+(?:[.,]\d+)?\*?)\s+"
    r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)"
    r"(?:\s+(\d+)\s+(\d+)\s+(\d+))?"
    r"\s*.*$"
)
_MATCHDAY_RE = re.compile(r"(\d+)\s*[ªa°]?\s*giornata", re.IGNORECASE)


def _team_from_line(line: str) -> Optional[str]:
    """Return the canonical team name if *line* is a Serie A team header,
    otherwise None. Tolerates case + surrounding whitespace/punctuation."""
    key = re.sub(r"[^a-zA-Z\s]", "", line).strip().lower()
    if key in _SERIE_A_LOWER:
        return _canonical_team(_SERIE_A_LOWER[key])
    return None


def _canonical_team(name: str) -> str:
    return TEAM_ALIASES.get(name, name)


def _parse_voti_pdf(pdf_bytes: bytes) -> Tuple[Optional[int], List[dict], Dict[str, Any]]:
    """Parse a fantacalcio.it "Voti" PDF.

    Returns ``(matchday, rows, diagnostics)`` where diagnostics contains
    useful fields when the parse yields 0 rows (line counts, sample lines,
    teams seen, etc.) so the admin can debug format issues.
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"pdfplumber non installato: {e}") from e

    matchday: Optional[int] = None
    current_team: Optional[str] = None
    rows: List[dict] = []
    seen: set = set()
    total_lines = 0
    teams_seen: List[str] = []
    row_looking_lines = 0        # lines that "look like" a row (start with digits)
    row_matched_lines = 0        # lines that actually match _ROW_RE
    sample_unmatched: List[str] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw_line in text.split("\n"):
                line = raw_line.strip()
                if not line:
                    continue
                total_lines += 1

                if matchday is None:
                    m = _MATCHDAY_RE.search(line)
                    if m:
                        try:
                            matchday = int(m.group(1))
                        except ValueError:
                            pass

                # Team header — tolerant match (case, punctuation)
                team_name = _team_from_line(line)
                if team_name:
                    current_team = team_name
                    if team_name not in teams_seen:
                        teams_seen.append(team_name)
                    continue

                if line.startswith("Cod."):
                    continue

                # Track "looks like a row" for diagnostics (line starts with number+role)
                if re.match(r"^\d+\s+(P|D|C|A|ALL)\s", line):
                    row_looking_lines += 1

                pm = _ROW_RE.match(line)
                if not pm or not current_team:
                    if pm and not current_team and len(sample_unmatched) < 5:
                        sample_unmatched.append(f"[team?] {line[:80]}")
                    elif not pm and re.match(r"^\d+\s+(P|D|C|A|ALL)\s", line) \
                         and len(sample_unmatched) < 5:
                        sample_unmatched.append(line[:100])
                    continue

                row_matched_lines += 1
                (code, role, name, voto_raw,
                 gf, gs, rp, rs, rf, au, amm, esp, ass) = pm.groups()

                # Short-format PDFs omit Amm/Esp/Ass — treat as 0.
                amm = amm or "0"
                esp = esp or "0"
                ass = ass or "0"

                voto_txt = voto_raw.replace("*", "").replace(",", ".")
                try:
                    voto_val: Optional[float] = float(voto_txt)
                except ValueError:
                    voto_val = None

                code_int = int(code)
                dedupe_key = (current_team, code_int)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                gf_i = int(gf); rf_i = int(rf)
                rows.append({
                    "matchday": matchday or 0,
                    "team": current_team,
                    "player_code": code_int,
                    "player_name": name.strip(),
                    "role": role,
                    "voto": voto_val,
                    "sv": "*" in voto_raw,
                    "gf": gf_i,
                    "gs": int(gs),
                    "rp": int(rp),
                    "rs": int(rs),
                    "rf": rf_i,
                    "au": int(au),
                    "amm": int(amm),
                    "esp": int(esp),
                    "ass": int(ass),
                    "total_goals": gf_i + rf_i,
                })

    diagnostics = {
        "total_lines": total_lines,
        "teams_seen_count": len(teams_seen),
        "teams_seen": teams_seen,
        "row_looking_lines": row_looking_lines,
        "row_matched_lines": row_matched_lines,
        "sample_unmatched": sample_unmatched,
        "matchday_detected": matchday,
    }
    return matchday, rows, diagnostics


def summarize(rows: List[dict]) -> Dict[str, Any]:
    """Return quick sanity-check aggregates for a parsed matchday."""
    by_team: Dict[str, int] = {}
    by_role: Dict[str, int] = {}
    scorers: List[dict] = []
    total_goals = 0
    for r in rows:
        by_team[r["team"]] = by_team.get(r["team"], 0) + 1
        by_role[r["role"]] = by_role.get(r["role"], 0) + 1
        if r["total_goals"] > 0:
            scorers.append({
                "team": r["team"],
                "player_name": r["player_name"],
                "goals": r["total_goals"],
                "voto": r["voto"],
            })
            total_goals += r["total_goals"]
    return {
        "players": len(rows),
        "teams": len(by_team),
        "by_team": dict(sorted(by_team.items())),
        "by_role": dict(sorted(by_role.items())),
        "scorers_count": len(scorers),
        "total_goals": total_goals,
        "scorers": scorers,
    }


# =========================================================================
# MongoDB indexes
# =========================================================================

async def ensure_indexes(db) -> None:
    try:
        await db.matchday_facts.create_index(
            [("matchday", 1), ("team", 1), ("player_code", 1)], unique=True
        )
        await db.matchday_facts.create_index([("matchday", 1)])
        await db.matchday_facts.create_index([("matchday", 1), ("total_goals", -1)])
    except Exception:  # pragma: no cover
        logger.exception("Failed to create matchday_facts indexes")


# =========================================================================
# Router factory
# =========================================================================

def build_router(
    db,
    current_user: Callable,
    require_admin: Callable,
) -> APIRouter:
    router = APIRouter(prefix="/admin/voti")

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- Upload ------------------------------------------------------------

    @router.post("/upload-pdf")
    async def upload_voti_pdf(
        file: UploadFile = File(...),
        dry_run: bool = True,
        replace: bool = True,
        matchday_override: Optional[int] = None,
        user: dict = Depends(require_admin),
    ):
        """Upload a "Voti Fantacalcio" PDF for a Serie A matchday.

        Params:
            dry_run: if ``true`` (default) only returns a preview.
            replace: if ``true`` (default) removes previous facts for the same
                matchday before inserting the new ones (idempotent re-uploads).
            matchday_override: force a matchday number if the parser cannot
                detect it from the header.
        """
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Serve un file .pdf")
        raw = await file.read()
        if len(raw) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="PDF troppo grande (max 20MB)")

        try:
            matchday, rows, diagnostics = _parse_voti_pdf(raw)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("PDF parse error")
            raise HTTPException(status_code=400, detail=f"Errore nell'analisi del PDF: {e}")

        if matchday_override is not None:
            matchday = matchday_override
            for r in rows:
                r["matchday"] = matchday

        if not rows:
            # Detailed diagnostics message so the admin knows WHAT went wrong
            teams_str = ", ".join(diagnostics["teams_seen"][:10]) or "nessuna"
            sample_str = (
                " · Righe simili non riconosciute: "
                + " || ".join(diagnostics["sample_unmatched"][:3])
                if diagnostics["sample_unmatched"] else ""
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Nessun giocatore riconosciuto ({diagnostics['total_lines']} righe totali, "
                    f"{diagnostics['row_looking_lines']} sembrano giocatori, "
                    f"{diagnostics['row_matched_lines']} riconosciute). "
                    f"Squadre trovate: {teams_str}."
                    + sample_str
                    + " Assicurati che il PDF sia il 'Voti Fantacalcio' ufficiale."
                ),
            )
        if not matchday:
            raise HTTPException(
                status_code=400,
                detail="Giornata non rilevata dal PDF. Passa 'matchday_override' esplicito.",
            )

        summary = summarize(rows)
        result: Dict[str, Any] = {
            "matchday": matchday,
            "dry_run": dry_run,
            **summary,
        }

        if dry_run:
            result["rows"] = rows
            return result

        now = _now()
        if replace:
            await db.matchday_facts.delete_many({"matchday": matchday})

        # Prepare docs with stable id
        docs = []
        for r in rows:
            docs.append({
                "id": str(uuid.uuid4()),
                **r,
                "created_at": now,
                "updated_at": now,
            })
        # Idempotent upserts (if replace=False and a doc already exists for the
        # same (matchday, team, player_code) we skip; if replace=True we already
        # cleared the matchday above).
        inserted = 0
        for d in docs:
            try:
                await db.matchday_facts.update_one(
                    {
                        "matchday": d["matchday"],
                        "team": d["team"],
                        "player_code": d["player_code"],
                    },
                    {"$setOnInsert": d},
                    upsert=True,
                )
                inserted += 1
            except Exception:  # pragma: no cover
                logger.exception("Upsert failed for %s / %s", d["team"], d["player_name"])

        result["inserted"] = inserted
        result["stored_total"] = await db.matchday_facts.count_documents({"matchday": matchday})
        return result

    @router.post("/upload-xlsx")
    async def upload_voti_xlsx(
        file: UploadFile = File(...),
        dry_run: bool = True,
        replace: bool = True,
        matchday_override: Optional[int] = None,
        sheet: str = "Fantacalcio",
        user: dict = Depends(require_admin),
    ):
        """Upload a "Voti Fantacalcio" **XLSX** for a Serie A matchday.

        Same contract as ``upload-pdf`` but reads the official fantacalcio.it
        Excel export. Reliable, fast, no OCR — the matchday is auto-detected
        from the title row (e.g. "38ª giornata"). Rows with ``Ruolo == "ALL"``
        (allenatore) are automatically excluded.

        Query params:
            dry_run: if ``true`` (default) only returns a preview
            replace: if ``true`` (default) removes previous facts for the same
                matchday before inserting the new ones (idempotent re-uploads)
            matchday_override: force a matchday number if auto-detect fails
            sheet: which sheet to read (``Fantacalcio`` [default], ``Statistico``
                or ``Italia`` — same rows, different rating models)
        """
        if not file.filename or not file.filename.lower().endswith(".xlsx"):
            raise HTTPException(status_code=400, detail="Serve un file .xlsx")
        raw = await file.read()
        if len(raw) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="XLSX troppo grande (max 20MB)")

        from excel_parser import parse_voti_xlsx  # local import → light startup
        try:
            matchday, rows, diagnostics = parse_voti_xlsx(raw, sheet=sheet)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("XLSX parse error")
            raise HTTPException(status_code=400, detail=f"Errore nell'analisi dell'XLSX: {e}")

        if matchday_override is not None:
            matchday = matchday_override
            for r in rows:
                r["matchday"] = matchday

        if not rows:
            teams_str = ", ".join(diagnostics.get("teams_seen", [])[:10]) or "nessuna"
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Nessun giocatore riconosciuto nell'XLSX ({diagnostics.get('lines_scanned', 0)} righe scansionate). "
                    f"Squadre trovate: {teams_str}. Foglio usato: {diagnostics.get('sheet_used')}. "
                    f"Assicurati che il file sia il 'Voti Fantacalcio' ufficiale."
                ),
            )
        if not matchday:
            raise HTTPException(
                status_code=400,
                detail="Giornata non rilevata dall'XLSX. Passa 'matchday_override' esplicito.",
            )

        summary = summarize(rows)
        result: Dict[str, Any] = {
            "matchday": matchday,
            "dry_run": dry_run,
            "source": "xlsx",
            "sheet_used": diagnostics.get("sheet_used"),
            "excluded_all": diagnostics.get("excluded_all", 0),
            **summary,
        }
        if dry_run:
            result["rows"] = rows
            return result

        now = _now()
        if replace:
            await db.matchday_facts.delete_many({"matchday": matchday})

        docs = []
        for r in rows:
            docs.append({
                "id": str(uuid.uuid4()),
                **r,
                "created_at": now,
                "updated_at": now,
            })
        inserted = 0
        for d in docs:
            try:
                await db.matchday_facts.update_one(
                    {
                        "matchday": d["matchday"],
                        "team": d["team"],
                        "player_code": d["player_code"],
                    },
                    {"$setOnInsert": d},
                    upsert=True,
                )
                inserted += 1
            except Exception:  # pragma: no cover
                logger.exception("Upsert failed for %s / %s", d["team"], d["player_name"])

        result["inserted"] = inserted
        result["stored_total"] = await db.matchday_facts.count_documents({"matchday": matchday})
        return result

    # --- Read --------------------------------------------------------------

    @router.get("/{matchday}")
    async def list_facts(matchday: int, user: dict = Depends(require_admin)):
        if matchday < 1 or matchday > 38:
            raise HTTPException(status_code=400, detail="matchday deve essere 1..38")
        docs = await db.matchday_facts.find(
            {"matchday": matchday}, {"_id": 0}
        ).sort([("team", 1), ("role", 1), ("player_name", 1)]).to_list(length=None)
        return {
            "matchday": matchday,
            "count": len(docs),
            "items": docs,
        }

    @router.get("/{matchday}/scorers")
    async def list_scorers(matchday: int, user: dict = Depends(require_admin)):
        """Return only players with ``total_goals > 0`` for a matchday.

        Used by ScoreAndLive auto-settlement (future).
        """
        if matchday < 1 or matchday > 38:
            raise HTTPException(status_code=400, detail="matchday deve essere 1..38")
        docs = await db.matchday_facts.find(
            {"matchday": matchday, "total_goals": {"$gt": 0}},
            {"_id": 0},
        ).sort([("total_goals", -1), ("team", 1)]).to_list(length=None)
        return {
            "matchday": matchday,
            "count": len(docs),
            "total_goals": sum(d.get("total_goals", 0) for d in docs),
            "scorers": docs,
        }

    @router.get("")
    async def list_matchdays(user: dict = Depends(require_admin)):
        """List which matchdays already have facts stored."""
        pipeline = [
            {"$group": {
                "_id": "$matchday",
                "players": {"$sum": 1},
                "total_goals": {"$sum": "$total_goals"},
                "updated_at": {"$max": "$updated_at"},
            }},
            {"$sort": {"_id": 1}},
        ]
        agg = await db.matchday_facts.aggregate(pipeline).to_list(length=None)
        return {
            "matchdays": [
                {
                    "matchday": row["_id"],
                    "players": row["players"],
                    "total_goals": row["total_goals"],
                    "updated_at": row.get("updated_at"),
                }
                for row in agg
            ]
        }

    # --- Team results (derived from voti) --------------------------------

    @router.get("/{matchday}/team-results")
    async def team_results(matchday: int, user: dict = Depends(require_admin)):
        """Aggregate per-team goal totals from voti (no separate PDF needed).

        For each team:
          * ``goals_scored`` = Σ (gf + rf) over all its players (own goals excluded)
          * ``goals_conceded`` = ``gs`` of that team's goalkeeper(s)
          * ``goals_conceded_check`` = Σ (au) of that team (own goals count for
            the opponent — informational)

        Also returns a global sanity check that
        ``sum(goals_scored) == sum(goals_conceded)``. If it doesn't match, some
        matches are still to be played / postponed or a portiere row is missing.
        """
        if matchday < 1 or matchday > 38:
            raise HTTPException(status_code=400, detail="matchday deve essere 1..38")

        pipeline = [
            {"$match": {"matchday": matchday}},
            {"$group": {
                "_id": "$team",
                "goals_scored": {"$sum": {"$add": ["$gf", "$rf"]}},
                "own_goals": {"$sum": "$au"},
                "gk_goals_conceded": {
                    "$sum": {"$cond": [{"$eq": ["$role", "P"]}, "$gs", 0]}
                },
                "players_graded": {
                    "$sum": {"$cond": [{"$eq": ["$sv", False]}, 1, 0]}
                },
                "players_total": {"$sum": 1},
                "yellow_cards": {"$sum": "$amm"},
                "red_cards": {"$sum": "$esp"},
            }},
            {"$sort": {"_id": 1}},
        ]
        agg = await db.matchday_facts.aggregate(pipeline).to_list(length=None)
        if not agg:
            raise HTTPException(
                status_code=404,
                detail=f"Nessun dato voti per la giornata {matchday}. Carica prima il PDF Voti.",
            )

        rows = []
        for r in agg:
            rows.append({
                "team": r["_id"],
                # goals attributed *by their own players* (open play + rigori)
                "goals_scored_openplay": r["goals_scored"],
                # if the team scored also from opponent own-goals, we can't
                # know it from this dataset alone → we surface `own_goals`
                # separately (goals attributed to their players as autogol,
                # which count for the opponent).
                "own_goals": r["own_goals"],
                # goals conceded, measured on the goalkeeper Gs column
                "goals_conceded": r["gk_goals_conceded"],
                "players_graded": r["players_graded"],
                "players_total": r["players_total"],
                "yellow_cards": r["yellow_cards"],
                "red_cards": r["red_cards"],
            })

        tot_scored = sum(x["goals_scored_openplay"] for x in rows)
        tot_own = sum(x["own_goals"] for x in rows)
        tot_conceded = sum(x["goals_conceded"] for x in rows)
        # Actual goals in the matchday: open-play goals scored + own goals
        # (autogol are goals for the opponent, not tallied in Gf/Rf).
        implied_goals = tot_scored + tot_own

        return {
            "matchday": matchday,
            "teams": rows,
            "sanity": {
                "goals_scored_openplay": tot_scored,
                "own_goals": tot_own,
                "implied_total_goals": implied_goals,
                "gk_goals_conceded": tot_conceded,
                # If everything is consistent these two must match.
                "consistent": implied_goals == tot_conceded,
            },
        }

    # --- Delete ------------------------------------------------------------

    @router.delete("/{matchday}")
    async def delete_matchday(matchday: int, user: dict = Depends(require_admin)):
        if matchday < 1 or matchday > 38:
            raise HTTPException(status_code=400, detail="matchday deve essere 1..38")
        r = await db.matchday_facts.delete_many({"matchday": matchday})
        return {"matchday": matchday, "deleted": r.deleted_count}

    return router
