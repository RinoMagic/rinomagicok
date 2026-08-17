"""TheBestTiket — betting-slip challenge among friends (a RinoMagic mini-game).

Extracted from ``server.py`` (June 2026) so this game's models, OCR pipeline,
prediction evaluator and REST endpoints all live next to each other — mirroring
the layout of :mod:`scoreandlive` and :mod:`fantagiornata`.

Flow:
1. Admin creates a Room for a matchday (with color + max_events per schedina)
2. Friends join with invite code + nickname (no password)
3. Each user uploads a betting-slip screenshot → AI Vision (Gemini 3 Flash)
   or Tesseract fallback parses events + odds
4. User confirms/edits parsed events → stored in DB
5. Admin fetches or manually inputs the Serie A matchday results
6. System computes each user's product-of-odds on WON predictions only
7. Leaderboard: highest total wins, lowest pays.

Public surface consumed by :mod:`server`:
* :data:`GAMES`, :data:`DEFAULT_GAME`, :data:`ROOM_COLORS`
* :func:`ocr_screenshot`, :func:`_evaluate_prediction`, :func:`_classify_bet`
  (re-exported from ``server`` for backwards-compatible tests)
* :func:`build_router` — returns the ``APIRouter`` mounted under ``/api``
* :func:`ensure_indexes`, :func:`backfill_legacy` — invoked from startup
"""
from __future__ import annotations

import io
import os
import re
import uuid
import base64
import string
import random
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytesseract
from PIL import Image, ImageFilter, ImageOps
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument

from schedina_vision import (
    extract_events_from_image as vision_extract_events,
    is_available as vision_is_available,
)
from deadlines import (
    get_deadline as _global_deadline_get,
    is_matchday_locked as _global_deadline_passed,
)

logger = logging.getLogger("thebesttiket")

TESSERACT_LANG = os.environ.get("TESSERACT_LANG", "ita+eng")

# =========================================================================
# Games registry (RinoMagic umbrella)
# =========================================================================
# RinoMagic is the "umbrella" that hosts multiple mini-games. Each room and
# each invite is scoped to exactly one game — a TheBestTiket invite cannot
# be used to join a ScoreAndLive room, and vice-versa.
GAMES: Dict[str, Dict[str, Any]] = {
    "thebesttiket": {
        "id": "thebesttiket",
        "name": "TheBestTiket",
        "tagline": "Schedine Serie A tra amici",
        "color": "#FFB300",
        "icon": "trophy",
        "enabled": True,
    },
    "scoreandlive": {
        "id": "scoreandlive",
        "name": "ScoreAndLive",
        "tagline": "Indovina i marcatori e sopravvivi",
        "color": "#3B82F6",
        "icon": "pulse",
        "enabled": False,
    },
    "fantagiornata": {
        "id": "fantagiornata",
        "name": "FantaGiornata",
        "tagline": "Fantacalcio a giornata singola",
        "color": "#A855F7",
        "icon": "football",
        "enabled": False,
    },
    "surviva": {
        "id": "surviva",
        "name": "Survival 2.0",
        "tagline": "3 vite, 1 pronostico a giornata: sopravvivi!",
        "color": "#EF4444",
        "icon": "heart",
        "enabled": True,
    },
}
DEFAULT_GAME = "thebesttiket"

ROOM_COLORS = [
    "#00D95F", "#FFB300", "#EF4444", "#3B82F6",
    "#A855F7", "#EC4899", "#14B8A6", "#F97316",
]


# =========================================================================
# Tesseract auto-install (best-effort startup hook)
# =========================================================================

def _ensure_tesseract() -> bool:
    """Verify tesseract binary is available. If missing on a Debian-based
    container, attempt a best-effort install (idempotent). Returns True when
    the binary is usable after this call."""
    import shutil
    import subprocess
    if shutil.which("tesseract"):
        return True
    logger.warning("tesseract binary missing; attempting to install...")
    try:
        env = os.environ.copy()
        env.setdefault("DEBIAN_FRONTEND", "noninteractive")
        subprocess.run(
            ["apt-get", "install", "-y", "--no-install-recommends",
             "tesseract-ocr", "tesseract-ocr-ita"],
            check=True, env=env, capture_output=True, timeout=180,
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.error("Failed to auto-install tesseract: %s", exc)
        return False
    ok = shutil.which("tesseract") is not None
    if ok:
        logger.info("tesseract installed successfully")
    return ok


# Trigger the check once at import time so the very first schedina upload
# doesn't pay the install cost.
_ensure_tesseract()


# =========================================================================
# Prediction codes: validation + evaluation
# =========================================================================
# Prediction codes we accept.
# Simple markets:
#   1  X  2  1X  X2  12          -> 1X2 + Double chance (final score)
#   GOL  NOGOL                     -> Both teams to score
#   OVER-0.5..OVER-4.5             -> Over goals with threshold
#   UNDER-0.5..UNDER-4.5           -> Under goals with threshold
#   MG-<a>-<b>                     -> Multigol totale (a..b inclusive, SI)
#   MG-<a>-<b>-NO                  -> Multigol totale NO
#   MGH-<a>-<b>[-NO]               -> Multigol casa
#   MGA-<a>-<b>[-NO]               -> Multigol ospite
# Combo: any of the above joined by '+', all conditions must be true, e.g.:
#   1+GOL          X+OVER-2.5      1X+MG-1-3       12+GOL+OVER-1.5
_SIMPLE_ATOM_RE = re.compile(
    r"^(?:"
    r"1X|X2|12|1|X|2"                    # 1X2 / Double chance (final score)
    r"|GOL|NOGOL"
    r"|(?:OVER|UNDER)-\d(?:\.\d)?"       # Over/Under with threshold
    r"|MG[HA]?-\d-\d(?:-NO)?"            # Multigol total/home/away
    r"|RE-\d+-\d+"                       # Risultato esatto
    r")$"
)


def _validate_prediction_code(code: str) -> str:
    """Validate and normalize a prediction code.

    Accepts legacy short forms and returns a canonical code:
        "OVER" -> "OVER-2.5"    "UNDER" -> "UNDER-2.5"
        "OVER2.5" or "OVER 2.5" -> "OVER-2.5"
    Combos joined by '+' are validated atom-by-atom.
    """
    if not code:
        raise ValueError("Pronostico mancante")

    def normalize_atom(a: str) -> str:
        a = a.strip().upper()
        m = re.match(r"^(OVER|UNDER)[\s-]*(\d(?:[.,]\d)?)?$", a)
        if m:
            side = m.group(1)
            thr = (m.group(2) or "2.5").replace(",", ".")
            if "." not in thr:
                thr = f"{thr}.5"
            return f"{side}-{thr}"
        return a

    atoms = [normalize_atom(a) for a in code.split("+")]
    canonical = "+".join(atoms)
    for a in atoms:
        if not _SIMPLE_ATOM_RE.match(a):
            raise ValueError(f"Mercato non ammesso: {code}")
    return canonical


# =========================================================================
# Anti-tamper: sanity caps on odds per market type
# =========================================================================
# Empirical caps observed on staryes.it for the italian Serie A. Any odd
# higher than the cap is treated as a red flag (possible screenshot ritoccato
# per gonfiare la vincita). Values are generous — realistic caps on staryes
# rarely exceed these even for extreme underdogs.
_MAX_ODD_BY_ATOM = {
    "1": 25.0, "X": 8.0, "2": 25.0,          # 1X2 (extreme underdog wins ~15x)
    "1X": 8.0, "X2": 8.0, "12": 6.0,          # Double chance
    "GOL": 5.0, "NOGOL": 5.0,                 # Both teams to score
}
# Fallback caps by market family (used when the specific atom isn't listed).
_MAX_ODD_BY_FAMILY = {
    "OVER": 15.0, "UNDER": 15.0,               # Over/Under (0.5..4.5)
    "MG": 20.0, "MGH": 30.0, "MGA": 30.0,      # Multigol
    "RE": 100.0,                                # Risultato esatto
}
# For combos (atoms joined by '+'), the cap grows multiplicatively but is
# capped at these values (staryes doesn't return arbitrarily high combo odds).
_MAX_COMBO_ODD = {
    2: 50.0,     # 2 atoms
    3: 200.0,    # 3 atoms
    4: 600.0,    # 4 atoms
    5: 999.0,    # >=5 atoms (matches Pydantic upper bound)
}


def _max_odd_for_prediction(prediction: str) -> float:
    """Return the maximum plausible staryes.it odd for *prediction*.

    Any observed odd above this cap should be treated as suspicious and either
    blocked outright or flagged for admin review. The cap is conservative
    (i.e. slightly above the highest odd ever seen on staryes for that market)
    so legitimate slips never trigger a false positive.
    """
    if not prediction:
        return 999.0
    atoms = [a.strip().upper() for a in prediction.split("+") if a.strip()]
    if not atoms:
        return 999.0

    def _atom_cap(atom: str) -> float:
        if atom in _MAX_ODD_BY_ATOM:
            return _MAX_ODD_BY_ATOM[atom]
        for family, cap in _MAX_ODD_BY_FAMILY.items():
            if atom.startswith(family + "-") or atom == family:
                return cap
        return 999.0

    if len(atoms) == 1:
        return _atom_cap(atoms[0])
    # Combo: product-of-caps, then bound by the per-length cap.
    product = 1.0
    for a in atoms:
        product *= _atom_cap(a)
    return min(product, _MAX_COMBO_ODD.get(min(len(atoms), 5), 999.0))


def _odd_exceeds_cap(prediction: str, odd: float) -> bool:
    """Return True when *odd* is higher than the sanity cap for *prediction*.

    A small tolerance (+10%) is applied to absorb rounding differences and
    edge cases where staryes' promotional odds slightly exceed our baseline.
    """
    if odd <= 0:
        return False
    cap = _max_odd_for_prediction(prediction)
    return odd > cap * 1.10


def _norm_team(name: str) -> str:
    """Aggressive team-name normalization for matching predictions vs results.

    Lowercase, strip punctuation, remove common suffixes (FC, AC, CF, US, ...).
    """
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(
        r"\b(fc|ac|cf|us|ss|calcio|football|club|serie|a)\b",
        " ",
        s,
    )
    return re.sub(r"\s+", " ", s).strip()


def _team_match(a: str, b: str) -> bool:
    na, nb = _norm_team(a), _norm_team(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return False
    overlap = ta & tb
    return len(overlap) >= 1 and len(overlap) >= min(len(ta), len(tb)) // 2


def _eval_1x2_dc(pick: str, home: int, away: int) -> bool:
    pick = pick.upper()
    if pick == "1":
        return home > away
    if pick == "X":
        return home == away
    if pick == "2":
        return home < away
    if pick == "1X":
        return home >= away
    if pick == "X2":
        return home <= away
    if pick == "12":
        return home != away
    return False


def _evaluate_prediction(pred: str, fx: dict) -> bool:
    """Return True if `pred` is correct given the fixture final score.

    Supports combos: multiple atoms joined by '+' — all must be true.
    `fx` must contain: home_score, away_score.
    """
    if not pred:
        return False
    home = int(fx.get("home_score", 0))
    away = int(fx.get("away_score", 0))
    total = home + away

    def eval_atom(atom: str) -> bool:
        atom = atom.upper().strip()
        m = re.match(r"^RE-(\d+)-(\d+)$", atom)
        if m:
            return home == int(m.group(1)) and away == int(m.group(2))
        m = re.match(r"^(MG|MGH|MGA)-(\d)-(\d)(-NO)?$", atom)
        if m:
            kind, a, b, no = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
            if kind == "MG":
                value = total
            elif kind == "MGH":
                value = home
            else:  # MGA
                value = away
            in_range = a <= value <= b
            return (not in_range) if no else in_range
        m = re.match(r"^(OVER|UNDER)-(\d(?:\.\d)?)$", atom)
        if m:
            side, thr = m.group(1), float(m.group(2))
            return total > thr if side == "OVER" else total < thr
        if atom == "GOL":
            return home > 0 and away > 0
        if atom == "NOGOL":
            return home == 0 or away == 0
        return _eval_1x2_dc(atom, home, away)

    for a in pred.split("+"):
        if not eval_atom(a):
            return False
    return True


# =========================================================================
# OCR pipeline (staryes.it bet slips)
# =========================================================================

def _preprocess_image(raw_bytes: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    if w < 900:
        scale = 900 / w
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray, cutoff=2)
    gray = gray.filter(ImageFilter.SHARPEN)
    return gray


# Each event block on staryes.it looks like:
#   CALCIO - SERIE A | 18:30           <-- header line (competition + kickoff)
#   7965  FROSINONE  -  JUVENTUS       <-- optional event id + teams
#   1X2: 2                       1.46  <-- market + prediction + odd
STARYES_HEADER_RE = re.compile(r"CALCIO\s*[-–]\s*SERIE\s*A", re.IGNORECASE)
STARYES_TEAMS_RE = re.compile(
    r"^\s*(?:\d{3,5}\s+)?([A-ZÀ-Ú][A-ZÀ-Ú\s.'’]+?)\s+[-–]\s+([A-ZÀ-Ú][A-ZÀ-Ú\s.'’]+?)\s*$"
)
STARYES_ODD_TAIL_RE = re.compile(r"([1-9]\d?[.,]\d{1,3})\s*$")


def _titleize(name: str) -> str:
    """FROSINONE -> Frosinone;  HELLAS VERONA -> Hellas Verona."""
    parts = re.split(r"(\s+)", name.strip())
    return "".join(p if p.isspace() else (p[:1].upper() + p[1:].lower()) for p in parts)


def _normalize_ocr_token(token: str) -> str:
    """Fix common OCR misreads on staryes bet-slip picks and market labels."""
    if not token:
        return token
    t = token
    for wrong in ("4X", "IX", "LX", "TX", "DX", "JX", "|X", "iX", "lX"):
        if wrong in t:
            t = t.replace(wrong, "1X")
    for wrong in ("X4", "XI", "XL", "XT", "XJ", "X|"):
        if wrong in t:
            t = t.replace(wrong, "X1")
    for wrong in ("42", "I2", "L2", "T2", "J2", "|2"):
        if wrong in t and len(t) <= 4:
            t = t.replace(wrong, "12")
    t = re.sub(r"\b0([A-Z])", r"O\1", t)
    return t


def _classify_bet(market_raw: str, pick_raw: str) -> Optional[str]:
    """Given the market label and pick as printed on staryes.it, return the
    canonical prediction code (or None if unrecognised).

    Examples:
        ("1X2", "2")                    -> "2"
        ("G/NG", "GOL")                 -> "GOL"
        ("U/O 1,5", "UNDER")            -> "UNDER-1.5"
        ("1X", "1X")                    -> "1X"
        ("MULTIGOL 0-1 OSPITE", "SI")   -> "MGA-0-1"
        ("MULTIGOL 0-2 CASA", "SI")     -> "MGH-0-2"
        ("MULTIGOL 1-3", "SI")          -> "MG-1-3"
        ("1X + GG/NG", "1X + NG")       -> "1X+NOGOL"
        ("U/O 2,5 + GG/NG", "GG + OV")  -> "GOL+OVER-2.5"
        ("1X + MULTIGOL 1 3", "SI")     -> "1X+MG-1-3"
        ("1X2 + U/O 1,5", "1 + UN")     -> "1+UNDER-1.5"
    """
    if pick_raw is None:
        return None
    market = _normalize_ocr_token(market_raw.upper().replace("°", "").replace(",", "."))
    pick = _normalize_ocr_token(
        re.sub(r"[^A-Z0-9./,+-]", "", pick_raw.upper().replace(",", "."))
    )
    if not pick:
        return None

    market_atoms = [m.strip() for m in market.split("+") if m.strip()]
    pick_atoms = [p for p in pick.split("+") if p]

    # Combo: the market label lists more than one market joined by '+'
    if len(market_atoms) > 1:
        codes: List[str] = []
        used_picks: set = set()
        for ma in market_atoms:
            code = None
            for k, pa in enumerate(pick_atoms):
                if k in used_picks:
                    continue
                c = _classify_bet(ma, pa)
                if c:
                    code = c
                    used_picks.add(k)
                    break
            if not code:
                c = _classify_bet(ma, ma)
                if c:
                    code = c
            if not code and ("MULTIGOL" in ma or "MULTI GOL" in ma):
                c = _classify_bet(ma, "SI")
                if c:
                    code = c
            if not code:
                return None
            codes.append(code)
        return "+".join(codes)

    # Detect first-half markets — NOT SUPPORTED: any HT market returns None
    if any(tag in market for tag in ("1TEMPO", "1 TEMPO", "PRIMO TEMPO", "1T ", " 1T", "1H", " HT ")):
        return None

    # Multigol (total / home / away). Accept both "1-3" and "1 3" separators.
    if "MULTIGOL" in market or "MULTI GOL" in market:
        rng = (
            re.search(r"(\d)\s*[-–]\s*(\d)", market)
            or re.search(r"(\d)\s+(\d)", market)
        )
        if not rng:
            m2 = re.search(r"\b(\d)(\d)\b", market)
            if m2:
                a, b = int(m2.group(1)), int(m2.group(2))
                if 0 <= a <= b <= 5:
                    rng = m2
        if not rng:
            return None
        a, b = rng.group(1), rng.group(2)
        if "CASA" in market or "HOME" in market:
            base = "MGH"
        elif "OSPITE" in market or "AWAY" in market or "TRASF" in market:
            base = "MGA"
        else:
            base = "MG"
        code = f"{base}-{a}-{b}"
        if pick in {"NO", "N"}:
            code += "-NO"
        elif pick not in {"SI", "S", "YES", "Y", "GOL", "1", ""}:
            return None
        return code

    # G/NG (both teams to score)
    if market in {"G/NG", "GG/NG", "GG", "NG"} or "GOL/NOGOL" in market or "GOL/NO GOL" in market:
        if pick in {"GOL", "GG", "SI", "S", "YES", "1"}:
            return "GOL"
        if pick in {"NOGOL", "NG", "NO", "N", "0"}:
            return "NOGOL"
        return None

    # Over / Under (threshold-aware).
    if (
        "U/O" in market or "O/U" in market
        or "U/0" in market or "0/U" in market
        or "OVER" in market or "UNDER" in market
        or "0VER" in market or "0/O" in market
    ):
        market_norm = (
            market.replace("U/0", "U/O").replace("0/U", "O/U")
            .replace("0VER", "OVER").replace("0/O", "O/O")
        )
        thr_match = re.search(r"(\d(?:\.\d)?)", market_norm)
        threshold = thr_match.group(1) if thr_match else "2.5"
        if "." not in threshold:
            threshold = f"{threshold}.5"
        if pick.startswith("OVER") or pick in {"O", "OV"}:
            return f"OVER-{threshold}"
        if pick.startswith("UNDER") or pick in {"U", "UN"}:
            return f"UNDER-{threshold}"
        return None

    # Draw No Bet
    if "DRAW NO BET" in market or market in {"DNB", "DRAW-NO-BET"}:
        if pick in {"1", "2"}:
            return pick
        return None

    # Risultato esatto
    if (
        "RISULTATO ESATTO" in market or "ESATTO" in market
        or market in {"RE", "R.ESATTO", "R-ESATTO"}
    ):
        m = re.search(r"(\d+)\s*[-–:.]\s*(\d+)", pick)
        if not m:
            m2 = re.search(r"\b(\d)(\d)\b", pick)
            if m2:
                a, b = int(m2.group(1)), int(m2.group(2))
                if a <= 9 and b <= 9:
                    return f"RE-{a}-{b}"
            return None
        a, b = int(m.group(1)), int(m.group(2))
        if a > 20 or b > 20:
            return None
        return f"RE-{a}-{b}"

    # 1X2 / Double chance
    if pick in {"1", "X", "2"}:
        return pick
    if pick in {"1X", "X2", "12", "IX"}:
        return "1X" if pick == "IX" else pick
    if pick in {"GOL", "NOGOL"}:
        return pick

    _ = pick_atoms  # kept for parity with combo branch
    return None


def _parse_staryes_slip(raw_text: str) -> List[dict]:
    """Parse a staryes.it bet slip out of raw OCR text."""
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    if not lines:
        return []

    team_anchors: List[tuple[int, str, str]] = []
    for i, ln in enumerate(lines):
        m = STARYES_TEAMS_RE.match(ln)
        if m:
            team_anchors.append((i, m.group(1).strip(), m.group(2).strip()))
    if not team_anchors:
        return []

    events: List[dict] = []
    for k, (idx, home, away) in enumerate(team_anchors):
        end = team_anchors[k + 1][0] if k + 1 < len(team_anchors) else len(lines)
        for ln in lines[idx + 1:end]:
            om = STARYES_ODD_TAIL_RE.search(ln)
            if not om:
                continue
            try:
                candidate = float(om.group(1).replace(",", "."))
            except ValueError:
                continue
            if not (1.01 <= candidate <= 999):
                continue
            pred_fragment = ln[: om.start()].strip()
            if ":" in pred_fragment:
                market_raw, pick_raw = pred_fragment.rsplit(":", 1)
            else:
                tokens = pred_fragment.split()
                market_raw = " ".join(tokens[:-1]) if len(tokens) > 1 else ""
                pick_raw = tokens[-1] if tokens else ""
            pred = _classify_bet(market_raw, pick_raw)
            events.append({
                "home_team": _titleize(home),
                "away_team": _titleize(away),
                "prediction": pred or "",
                "odd": round(candidate, 3),
                "market_raw": pred_fragment.strip(),
            })
            break  # Only one bet line per event

    seen = set()
    dedup: List[dict] = []
    for e in events:
        key = (e["home_team"].lower(), e["away_team"].lower(), e["prediction"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(e)
    return dedup


# =========================================================================
# Bookmaker validation
# =========================================================================
# TheBestTiket accepts ONLY staryes.it bet slips (anti-cheat: staryes doesn't
# offer live cash-out on odds, and the layout is stable enough for the
# OCR/Vision pipeline to be robust). Slips from other bookmakers are rejected
# outright with a clear message.
ALLOWED_BOOKMAKER_TOKENS = ("staryes", "star yes", "starcasino")

# Common tokens that indicate the slip is from a DIFFERENT bookmaker — used
# to detect non-staryes slips in the Tesseract fallback path (where we don't
# have a dedicated "site" field from the LLM).
NON_STARYES_TOKENS = (
    "snai", "sisal", "bet365", "goldbet", "planetwin", "planet win",
    "lottomatica", "betflag", "bet flag", "eurobet", "william hill",
    "betfair", "888sport", "888 sport", "netbet", "leovegas", "pokerstars",
    "unibet", "betaland", "gioco digitale", "big bet", "starvegas",
    "admiral", "vincitu", "sistemabet",
)


def _is_staryes_bookmaker(name: Optional[str]) -> bool:
    """Return True if *name* looks like a staryes.it identifier.

    Accepts variations like "staryes", "staryes.it", "Star Yes", "starcasino".
    Empty / None / other bookmaker names return False.
    """
    if not name:
        return False
    low = name.strip().lower()
    if not low or low in {"unknown", "?", "n/a"}:
        return False
    return any(tok in low for tok in ALLOWED_BOOKMAKER_TOKENS)


def _detect_non_staryes_hint(raw_text: str) -> Optional[str]:
    """Scan the OCR raw text for keywords of well-known non-staryes bookmakers.

    Returns the offending bookmaker name if found, otherwise None. This is a
    best-effort defence: OCR can misspell brand names, so absence of a match
    doesn't guarantee staryes. See ``_is_staryes_bookmaker`` for the positive
    check (LLM-based).
    """
    if not raw_text:
        return None
    low = raw_text.lower()
    for token in NON_STARYES_TOKENS:
        if token in low:
            return token.upper()
    return None


# --------------------------------------------------------------------------
# COLOR-SIGNATURE detector — the definitive anti-cheat gate
# --------------------------------------------------------------------------
# Star Yes has a very stable visual identity: dark navy background
# (#102040), white text, cyan quotes, green winnings amount, black "GIOCA"
# button. No other Italian bookmaker uses this palette. This lets us
# reject foreign slips *deterministically* — no LLM cost, no OCR misreads.
def _staryes_color_signature(image_bytes: bytes) -> Dict[str, float]:
    """Analyse the pixel palette of a bet-slip screenshot and return the
    ratio of pixels matching each staryes signature colour range.

    Metrics returned (all in %):
      • ``navy_core_pct``   — pixels close to the primary staryes bg #102040
      • ``navy_dark_pct``   — pixels in the darker card/shadow variants
      • ``dark_blue_total_pct`` — sum of the two above (background dominance)
      • ``white_text_pct``  — near-white pixels (team names / odds)
      • ``cyan_prenota_pct``— cyan pixels (~ #1090D0, quote / Prenota btn)
      • ``green_win_pct``   — green pixels (winning amount)

    Returns empty dict on decode error.
    """
    try:
        from PIL import Image  # type: ignore
        from io import BytesIO
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        img.thumbnail((160, 320))  # downsample for speed (~50 k pixels max)
        px = list(img.getdata())
    except Exception:
        return {}
    n = len(px)
    if n == 0:
        return {}

    navy_core = navy_dark = white_text = cyan_prenota = green_win = yellow_gb = 0
    for r, g, b in px:
        # Primary staryes navy #102040 (with generous tolerance)
        if 0 <= r <= 40 and 20 <= g <= 55 and 55 <= b <= 90 and b > r + 20:
            navy_core += 1
        # Darker card / shadow variants (#101830, #101020)
        elif 0 <= r <= 40 and 0 <= g <= 45 and 20 <= b <= 60 and b >= g:
            navy_dark += 1
        # Near-white text
        if r >= 220 and g >= 220 and b >= 220:
            white_text += 1
        # Staryes green (winnings amount, ~ #10c060)
        if 5 <= r <= 90 and 130 <= g <= 230 and 60 <= b <= 150 and g > r + 60:
            green_win += 1
        # Staryes cyan (~ #1090D0)
        if 0 <= r <= 60 and 100 <= g <= 180 and 170 <= b <= 240 and b > g + 20:
            cyan_prenota += 1
        # Bright yellow — Goldbet / Sisal / Snai / Bet365 branding.
        # Staryes never uses yellow. RGB ~ #FFC700 with tolerance.
        if r >= 200 and g >= 160 and b <= 100 and g > b + 60 and abs(r - g) < 80:
            yellow_gb += 1

    def pct(v: int) -> float:
        return round(100 * v / n, 2)

    return {
        "navy_core_pct": pct(navy_core),
        "navy_dark_pct": pct(navy_dark),
        "dark_blue_total_pct": pct(navy_core + navy_dark),
        "white_text_pct": pct(white_text),
        "cyan_prenota_pct": pct(cyan_prenota),
        "green_win_pct": pct(green_win),
        "yellow_pct": pct(yellow_gb),
    }


def _is_staryes_by_color(image_bytes: bytes) -> Tuple[bool, str, Dict[str, float]]:
    """Deterministic anti-cheat: verify a bet slip is from staryes.it by
    checking its colour signature. Returns ``(is_staryes, reason, metrics)``.

    Thresholds calibrated on 12 real staryes fixtures (see
    tests/fixtures/staryes_*) and validated against synthetic samples of
    Goldbet / Sisal / Snai / Bet365 / PlanetWin.

      • dark blue background must dominate → dark_blue_total_pct ≥ 25%
      • staryes signature navy must be present → navy_core_pct ≥ 8%
      • some white text must be visible → white_text_pct ≥ 2%
    """
    sig = _staryes_color_signature(image_bytes)
    if not sig:
        return False, "immagine non leggibile", {}
    # HARD REJECT: bright yellow → other bookmaker (Goldbet, Sisal, Snai,
    # Bet365 all use yellow accents). Staryes NEVER uses yellow.
    if sig["yellow_pct"] > 0.5:
        return False, (
            f"rilevato giallo brillante ({sig['yellow_pct']:.1f}%) — "
            "Star Yes non usa mai il giallo"
        ), sig
    # HARD REJECT: too much white → light background, not staryes
    # (staryes has full dark bg, real fixtures ≤ 22%). Goldbet body is white.
    if sig["white_text_pct"] > 30:
        return False, (
            f"sfondo troppo chiaro ({sig['white_text_pct']:.0f}% pixel "
            "bianchi) — Star Yes ha sfondo pieno blu scuro"
        ), sig
    if sig["dark_blue_total_pct"] < 25:
        return False, (
            "sfondo non compatibile con Star Yes "
            f"(blu scuro rilevato: {sig['dark_blue_total_pct']:.0f}%, atteso ≥ 25%)"
        ), sig
    if sig["navy_core_pct"] < 8:
        return False, (
            "tonalità di blu non corrispondente al blu Star Yes "
            f"(rilevato: {sig['navy_core_pct']:.0f}%, atteso ≥ 8%)"
        ), sig
    # White-text threshold is CONDITIONAL: if navy is very dominant (>40%),
    # the palette is already unequivocally staryes — small amounts of white
    # text can be crushed by JPEG re-compression (e.g. WhatsApp) so we relax
    # to ≥ 0.3%. Otherwise the standard ≥ 2% rule still applies.
    white_min = 0.3 if sig["navy_core_pct"] >= 40 else 2.0
    if sig["white_text_pct"] < white_min:
        return False, (
            "testo bianco insufficiente "
            f"(rilevato: {sig['white_text_pct']:.1f}%, atteso ≥ {white_min}%)"
        ), sig
    return True, "ok", sig



async def ocr_screenshot(image_bytes: bytes, use_vision: bool = True) -> Dict[str, Any]:
    """Extract events from a bet-slip screenshot.

    Primary extractor: **Gemini 3 Flash Vision** via the Emergent LLM Key.
    Tesseract is the fallback when AI Vision is unavailable / returns nothing.

    Pass ``use_vision=False`` to bypass the LLM (used by regression tests
    that lock in the Tesseract parser behaviour without hitting the API).
    """
    # ---- 1) AI Vision (primary) --------------------------------------
    if use_vision and vision_is_available():
        try:
            vres = await vision_extract_events(image_bytes)
        except Exception as exc:  # pragma: no cover
            logger.exception("AI Vision call raised unexpectedly")
            vres = {"events": [], "raw_text": "", "error": str(exc)}

        events = vres.get("events") or []
        vision_bookmaker = (vres.get("bookmaker") or "").strip()
        if events:
            logger.info(
                "Schedina extracted via AI Vision (provider=%s, events=%d, bookmaker=%s)",
                vres.get("provider"), len(events), vision_bookmaker or "?",
            )
            note = vres.get("note")
            return {
                "raw_text": vres.get("raw_text") or (f"AI Vision — {note}" if note else "AI Vision"),
                "events": events,
                "provider": vres.get("provider") or "ai-vision",
                "bookmaker": vision_bookmaker,
            }
        logger.warning(
            "AI Vision returned 0 events — falling back to Tesseract. error=%r note=%r",
            vres.get("error"), vres.get("note"),
        )

    # ---- 2) Tesseract fallback ---------------------------------------
    if not _ensure_tesseract():
        raise HTTPException(
            status_code=503,
            detail=(
                "Impossibile analizzare la schedina: né AI Vision né OCR locale "
                "sono disponibili. Riprova tra qualche secondo."
            ),
        )
    original = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    processed = _preprocess_image(image_bytes)

    best_text = ""
    best_events: List[dict] = []
    best_score: tuple[int, int] = (-1, -1)  # (predictions_ok, events_count)
    ocr_error: Optional[str] = None
    for candidate in (original, processed):
        try:
            text = pytesseract.image_to_string(candidate, lang=TESSERACT_LANG)
        except Exception as exc:
            ocr_error = str(exc)
            logger.warning("OCR failure: %s", exc)
            continue
        events = _parse_staryes_slip(text)
        logger.info("OCR raw text (%d chars):\n%s", len(text), text)
        logger.info("OCR parsed events: %s", events)
        pred_ok = sum(1 for e in events if e.get("prediction"))
        score = (pred_ok, len(events))
        if score > best_score:
            best_events = events
            best_text = text
            best_score = score
        elif not best_text:
            best_text = text
    if not best_text and ocr_error:
        raise HTTPException(
            status_code=503,
            detail=f"Analisi schedina fallita: {ocr_error}. Riprova o contatta l'admin.",
        )
    # Tesseract has no explicit "bookmaker" field: infer heuristically from raw
    # text. If we can spot a NON-staryes token we return it so the caller can
    # reject the slip; otherwise "staryes" (best-effort assumption).
    detected_hint = _detect_non_staryes_hint(best_text)
    tess_bookmaker = detected_hint.lower() if detected_hint else "staryes"
    return {
        "raw_text": best_text,
        "events": best_events,
        "provider": "tesseract",
        "bookmaker": tess_bookmaker,
    }


# =========================================================================
# Datetime helpers (used by the deadline check)
# =========================================================================

def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _is_deadline_passed(deadline_at: Optional[str]) -> bool:
    dt = _parse_iso_datetime(deadline_at)
    if not dt:
        return False
    return datetime.now(timezone.utc) >= dt


def _gen_code(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


# =========================================================================
# Pydantic models
# =========================================================================

class RoomCreate(BaseModel):
    name: str = Field(min_length=2, max_length=40)
    matchday: int = Field(ge=1, le=38)
    max_events: int = Field(ge=1, le=5, default=5)
    color: Optional[str] = None
    game: str = Field(default=DEFAULT_GAME)


class RoomUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=40)
    matchday: Optional[int] = Field(default=None, ge=1, le=38)
    max_events: Optional[int] = Field(default=None, ge=1, le=5)
    color: Optional[str] = None
    deadline_at: Optional[str] = None


class RoomJoin(BaseModel):
    invite_code: str


class SchedinaEventIn(BaseModel):
    home_team: str
    away_team: str
    prediction: str
    odd: float = Field(gt=0, le=1000)

    @field_validator("prediction")
    @classmethod
    def _norm_pred(cls, v: str) -> str:
        return _validate_prediction_code(v.strip().upper().replace(" ", ""))


class SchedinaConfirm(BaseModel):
    events: Optional[List[SchedinaEventIn]] = None
    on_behalf_of: Optional[str] = None
    membership_id: Optional[str] = None


class FixtureIn(BaseModel):
    home_team: str
    away_team: str
    home_score: int = Field(ge=0)
    away_score: int = Field(ge=0)
    both_scored: Optional[bool] = None
    # When True, the match was postponed / cancelled and the fixture is
    # scored neutrally: every user's prediction is treated as WIN with
    # contribution 1.00 to the schedina ranking (no gain, no loss).
    postponed: Optional[bool] = False


class FixturesIn(BaseModel):
    fixtures: List[FixtureIn]


class ScreenshotIn(BaseModel):
    image_base64: str
    on_behalf_of: Optional[str] = None
    membership_id: Optional[str] = None


# =========================================================================
# Startup hooks (indexes + legacy backfill)
# =========================================================================

async def ensure_indexes(db) -> None:
    """Create the collection indexes needed by TheBestTiket routes."""
    await db.rooms.create_index("id", unique=True)
    await db.rooms.create_index("invite_code", unique=True)
    # Multi-entry: a user can hold N memberships in the same room (one per
    # invite they claim), each with its own schedina slot. So the old
    # unique index on (room_id, user_id) is DROPPED and replaced with a
    # non-unique lookup index. Uniqueness is now per-membership-id.
    try:
        await db.memberships.drop_index("room_id_1_user_id_1")
    except Exception:
        pass
    await db.memberships.create_index([("room_id", 1), ("user_id", 1)])
    await db.memberships.create_index("id", unique=True, sparse=True)
    await db.memberships.create_index("invite_id", unique=True, sparse=True)
    # Schedina: 1 per membership (not 1 per user-per-room anymore).
    try:
        await db.schedine.drop_index("room_id_1_user_id_1")
    except Exception:
        pass
    await db.schedine.create_index([("room_id", 1), ("user_id", 1)])
    await db.schedine.create_index("membership_id", unique=True, sparse=True)
    await db.fixtures.create_index(
        [("room_id", 1), ("home_team", 1), ("away_team", 1)], unique=True,
    )
    await db.invites.create_index("code", unique=True)
    await db.invites.create_index([("room_id", 1), ("used_by_user_id", 1)])


async def backfill_legacy(db) -> None:
    """One-shot backfills for legacy rooms/invites.

    * Every existing room/invite belongs to TheBestTiket (default game)
    * For rooms with a legacy ``invite_code`` but no invite doc, create one
      so the invite link still works after the "one-shot invite" migration.
    * Legacy admin auto-enrollment memberships stored ``invite_id: null``
      / ``invite_code: null``; those must be $unset so the unique+sparse
      index on ``invite_id`` (which does NOT skip explicit nulls) does not
      collide when a new admin room is created.
    """
    await db.memberships.update_many(
        {"invite_id": None}, {"$unset": {"invite_id": "", "invite_code": ""}},
    )
    await db.rooms.update_many(
        {"game": {"$exists": False}}, {"$set": {"game": DEFAULT_GAME}},
    )
    await db.invites.update_many(
        {"game": {"$exists": False}}, {"$set": {"game": DEFAULT_GAME}},
    )
    async for r in db.rooms.find(
        {"invite_code": {"$exists": True}},
        {"id": 1, "invite_code": 1, "admin_user_id": 1, "created_at": 1, "_id": 0},
    ):
        existing = await db.invites.find_one({"code": r["invite_code"]})
        if not existing:
            await db.invites.insert_one({
                "id": str(uuid.uuid4()),
                "room_id": r["id"],
                "code": r["invite_code"],
                "used_by_user_id": None,
                "used_at": None,
                "created_at": r.get("created_at") or datetime.now(timezone.utc).isoformat(),
                "created_by": r.get("admin_user_id"),
                "revoked_at": None,
            })


# =========================================================================
# Router factory
# =========================================================================

def build_router(
    db,
    current_user: Callable,
    require_admin: Callable,
    display_name: Callable,
) -> APIRouter:
    """Return the APIRouter carrying every TheBestTiket + games-hub route.

    The returned router carries NO prefix — the caller is expected to mount
    it under ``/api`` (via the shared ``api = APIRouter(prefix="/api")``).
    """
    router = APIRouter()

    # ---- Small helpers scoped to this router --------------------------
    async def _room_dict(room: dict, viewer: Optional[dict] = None) -> dict:
        members_count = await db.memberships.count_documents({"room_id": room["id"]})
        settled = room.get("status") == "settled"
        is_admin_of_room = False
        if viewer:
            is_admin_of_room = viewer["role"] == "admin" or viewer["id"] == room.get("admin_user_id")
        invites_total = await db.invites.count_documents(
            {"room_id": room["id"], "revoked_at": None},
        )
        invites_available = await db.invites.count_documents(
            {"room_id": room["id"], "revoked_at": None, "used_by_user_id": None},
        )
        deadline_at = room.get("deadline_at")
        # Global deadline (shared with all games) has precedence over the
        # legacy room-level deadline. If a global one is set for this
        # (season, matchday) it overrides the per-room field entirely.
        season = room.get("season") or "2026-27"
        md = room.get("matchday")
        deadline_source = "room"
        if isinstance(md, int):
            gdt = await _global_deadline_get(db, season, md)
            if gdt is not None:
                deadline_at = gdt.isoformat()
                deadline_source = "global"
        submissions_locked = _is_deadline_passed(deadline_at)
        return {
            "id": room["id"],
            "name": room["name"],
            "matchday": room["matchday"],
            "max_events": room["max_events"],
            "color": room["color"],
            "game": room.get("game", DEFAULT_GAME),
            "invite_code": room["invite_code"],
            "admin_user_id": room.get("admin_user_id"),
            "status": room.get("status", "open"),
            "created_at": room["created_at"],
            "members_count": members_count,
            "invites_total": invites_total,
            "invites_available": invites_available,
            "deadline_at": deadline_at,
            "deadline_source": deadline_source,
            "submissions_locked": submissions_locked,
            "settled": settled,
            "is_admin": is_admin_of_room,
        }

    async def _ensure_submissions_open(room: dict) -> None:
        # Prefer global deadline; fall back to legacy per-room deadline.
        season = room.get("season") or "2026-27"
        md = room.get("matchday")
        deadline_iso: Optional[str] = None
        if isinstance(md, int):
            gdt = await _global_deadline_get(db, season, md)
            if gdt is not None:
                deadline_iso = gdt.isoformat()
        if deadline_iso is None:
            deadline_iso = room.get("deadline_at")
        if _is_deadline_passed(deadline_iso):
            raise HTTPException(
                status_code=403,
                detail="Termine per l'inserimento delle schedine scaduto",
            )

    async def _ensure_member(room_id: str, user: dict) -> None:
        if user["role"] == "admin":
            return
        m = await db.memberships.find_one({"room_id": room_id, "user_id": user["id"]})
        if not m:
            raise HTTPException(status_code=403, detail="Non sei nella stanza")

    async def _invite_dict(inv: dict) -> dict:
        used_by_nickname = None
        if inv.get("used_by_user_id"):
            u = await db.users.find_one(
                {"id": inv["used_by_user_id"]}, {"_id": 0, "password_hash": 0},
            )
            if u:
                used_by_nickname = u.get("username") or u.get("email")
        return {
            "id": inv["id"],
            "code": inv["code"],
            "used_by_user_id": inv.get("used_by_user_id"),
            "used_by_nickname": used_by_nickname,
            "used_at": inv.get("used_at"),
            "revoked_at": inv.get("revoked_at"),
            "created_at": inv.get("created_at"),
        }

    async def _resolve_target_user(
        room_id: str, actor: dict, target_user_id: Optional[str]
    ) -> dict:
        """Return the schedina owner.

        If `target_user_id` is set and different from the actor, the actor
        MUST be the admin of the room (or a global admin) and the target
        MUST be a member of the room.
        """
        if not target_user_id or target_user_id == actor["id"]:
            return actor
        room = await db.rooms.find_one({"id": room_id}, {"admin_user_id": 1, "_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")
        is_room_admin = actor["role"] == "admin" or actor["id"] == room.get("admin_user_id")
        if not is_room_admin:
            raise HTTPException(
                status_code=403,
                detail="Solo l'admin della stanza può caricare la schedina per un altro giocatore",
            )
        target = await db.users.find_one(
            {"id": target_user_id}, {"password_hash": 0, "_id": 0},
        )
        if not target:
            raise HTTPException(status_code=404, detail="Giocatore non trovato")
        member = await db.memberships.find_one(
            {"room_id": room_id, "user_id": target_user_id},
        )
        if not member:
            raise HTTPException(
                status_code=400,
                detail="Il giocatore non fa parte di questa stanza",
            )
        return target

    async def _resolve_membership(
        room_id: str, owner: dict, membership_id: Optional[str],
    ) -> dict:
        """Pick the membership (slot) targeted by a schedina operation.

        Rules:
          • If ``membership_id`` is provided → must belong to ``owner`` and
            to ``room_id``, otherwise 404/403.
          • If not provided → if the owner has exactly ONE membership in
            the room, use it (backwards compat with single-slot rooms).
          • Otherwise (0 or ≥2 memberships) → 400 with a clear message.
        """
        if membership_id:
            m = await db.memberships.find_one(
                {"id": membership_id, "room_id": room_id, "user_id": owner["id"]},
                {"_id": 0},
            )
            if not m:
                raise HTTPException(
                    status_code=404,
                    detail="Slot iscrizione non trovato per questo giocatore",
                )
            return m
        rows = [m async for m in db.memberships.find(
            {"room_id": room_id, "user_id": owner["id"]}, {"_id": 0},
        )]
        if len(rows) == 1:
            return rows[0]
        if len(rows) == 0:
            raise HTTPException(status_code=400, detail="Il giocatore non è iscritto alla stanza")
        raise HTTPException(
            status_code=400,
            detail=(
                f"Il giocatore ha {len(rows)} iscrizioni in questa stanza. "
                "Specifica 'membership_id' per indicare a quale schedina si riferisce."
            ),
        )

    def _match_prediction_to_fixture(event: dict, fixtures: List[dict]) -> Optional[dict]:
        for f in fixtures:
            if _team_match(event["home_team"], f["home_team"]) and _team_match(event["away_team"], f["away_team"]):
                return f
            if _team_match(event["home_team"], f["away_team"]) and _team_match(event["away_team"], f["home_team"]):
                return f
        return None

    # ==================================================================
    # Games hub
    # ==================================================================

    @router.get("/games")
    async def list_games(user: dict = Depends(current_user)):
        """List of mini-games hosted by the RinoMagic umbrella app."""
        if user["role"] == "admin":
            my_room_ids: Optional[List[str]] = None  # None means "all rooms"
        else:
            my_room_ids = [m["room_id"] async for m in db.memberships.find(
                {"user_id": user["id"]}, {"room_id": 1, "_id": 0})]

        # Surviva 2.0 lives in its own collection: count my active tournaments.
        if user["role"] == "admin":
            surviva_count = await db.sv_tournaments.count_documents({})
        else:
            surviva_count = await db.sv_participants.count_documents(
                {"user_id": user["id"]},
            )
        # ScoreAndLive lives in its own collection too — count where I am a
        # participant (or all tournaments for admins).
        if user["role"] == "admin":
            sal_count = await db.sal_tournaments.count_documents({})
        else:
            sal_count = await db.sal_participants.count_documents(
                {"user_id": user["id"]},
            )

        games: List[dict] = []
        for gid, meta in GAMES.items():
            if gid == "surviva":
                my_rooms_count = surviva_count
            elif gid == "scoreandlive":
                my_rooms_count = sal_count
            else:
                q: dict = {"game": gid}
                if my_room_ids is not None:
                    q["id"] = {"$in": my_room_ids}
                my_rooms_count = await db.rooms.count_documents(q)
            games.append({**meta, "my_rooms_count": my_rooms_count})
        return games

    # ==================================================================
    # Rooms — CRUD
    # ==================================================================

    @router.post("/rooms")
    async def create_room(data: RoomCreate, user: dict = Depends(require_admin)):
        game = data.game if data.game in GAMES else DEFAULT_GAME
        if not GAMES[game].get("enabled", False):
            raise HTTPException(
                status_code=400,
                detail=f"Il gioco '{GAMES[game]['name']}' non è ancora disponibile",
            )
        for _ in range(10):
            code = _gen_code()
            if not await db.rooms.find_one({"invite_code": code}) and not await db.invites.find_one({"code": code}):
                break
        room_id = str(uuid.uuid4())
        color = data.color if data.color in ROOM_COLORS else random.choice(ROOM_COLORS)
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "id": room_id,
            "name": data.name,
            "matchday": data.matchday,
            "max_events": data.max_events,
            "color": color,
            "game": game,
            "invite_code": code,
            "admin_user_id": user["id"],
            "status": "open",
            "created_at": now,
        }
        await db.rooms.insert_one(doc)
        await db.invites.insert_one({
            "id": str(uuid.uuid4()),
            "room_id": room_id,
            "game": game,
            "code": code,
            "used_by_user_id": None,
            "used_at": None,
            "created_at": now,
            "created_by": user["id"],
            "revoked_at": None,
        })
        # Admin auto-enrollment (no invite consumed). We OMIT ``invite_id``
        # and ``invite_code`` entirely (instead of setting them to ``None``)
        # so the unique+sparse indexes on those fields skip this document.
        # Storing ``invite_id: null`` on multiple admin auto-enrollments
        # would collide on the unique index (sparse skips MISSING keys, not
        # explicit ``null`` values).
        #
        # Multi-admin support: enroll ALL admins (creator gets slot 1, then
        # the others in id order) so every admin can play from the start.
        admin_users = [u async for u in db.users.find(
            {"role": "admin"}, {"_id": 0, "id": 1, "username": 1, "email": 1},
        )]
        # Put creator first; sort the rest by id for a stable slot order.
        others = sorted(
            [a for a in admin_users if a["id"] != user["id"]],
            key=lambda a: a["id"],
        )
        # Include the creator explicitly (they might be missing from the query
        # in weird edge cases; also we want to guarantee slot 1).
        ordered = [user] + others
        for idx, adm in enumerate(ordered, start=1):
            existing = await db.memberships.find_one({
                "room_id": room_id, "user_id": adm["id"],
            })
            if existing:
                continue
            await db.memberships.insert_one({
                "id": str(uuid.uuid4()),
                "room_id": room_id,
                "user_id": adm["id"],
                "slot": idx,
                "display_name": display_name(adm),
                "joined_at": now,
            })
        # Auto-create the exact_score bonus draft for this room's matchday
        try:
            from bonus import ensure_bonus_draft
            await ensure_bonus_draft(
                db, season="2026-27",
                matchday=int(data.matchday),
                bonus_type="exact_score", created_by=user["id"],
            )
        except Exception:
            logger.exception("Failed to ensure bonus draft for room %s", room_id)
        return await _room_dict(doc, user)

    @router.get("/rooms")
    async def list_my_rooms(user: dict = Depends(current_user), game: Optional[str] = None):
        game_filter: dict = {}
        if game:
            if game not in GAMES:
                raise HTTPException(status_code=400, detail="Gioco non valido")
            game_filter = {"game": game}
        if user["role"] == "admin":
            cursor = db.rooms.find(game_filter, {"_id": 0}).sort("created_at", -1)
        else:
            member_room_ids = [m["room_id"] async for m in db.memberships.find(
                {"user_id": user["id"]}, {"room_id": 1, "_id": 0})]
            q: dict = {"id": {"$in": member_room_ids}}
            if game_filter:
                q.update(game_filter)
            cursor = db.rooms.find(q, {"_id": 0}).sort("created_at", -1)
        rooms = []
        async for r in cursor:
            rooms.append(await _room_dict(r, user))
        return rooms

    @router.get("/rooms/by-code/{invite_code}")
    async def preview_room(invite_code: str):
        """Public preview of a room by invite code (no auth)."""
        code = invite_code.upper().strip()
        invite = await db.invites.find_one({"code": code})
        if not invite:
            raise HTTPException(status_code=404, detail="Codice invito non valido")
        if invite.get("revoked_at"):
            raise HTTPException(status_code=410, detail="Codice invito revocato")
        if invite.get("used_by_user_id"):
            raise HTTPException(status_code=410, detail="Codice invito già utilizzato")
        room = await db.rooms.find_one({"id": invite["room_id"]}, {"_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")
        return {
            "id": room["id"],
            "name": room["name"],
            "matchday": room["matchday"],
            "max_events": room["max_events"],
            "color": room["color"],
            "game": room.get("game", DEFAULT_GAME),
            "invite_code": code,
            "status": room.get("status", "open"),
        }

    @router.post("/rooms/join")
    async def join_room(data: RoomJoin, user: dict = Depends(current_user)):
        code = data.invite_code.upper().strip()
        now = datetime.now(timezone.utc).isoformat()
        claimed = await db.invites.find_one_and_update(
            {"code": code, "used_by_user_id": None, "revoked_at": None},
            {"$set": {"used_by_user_id": user["id"], "used_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            invite = await db.invites.find_one({"code": code})
            if not invite:
                raise HTTPException(status_code=404, detail="Codice invito non valido")
            if invite.get("revoked_at"):
                raise HTTPException(status_code=410, detail="Codice invito revocato")
            if invite.get("used_by_user_id") == user["id"]:
                room = await db.rooms.find_one({"id": invite["room_id"]}, {"_id": 0})
                if room:
                    return await _room_dict(room, user)
            raise HTTPException(status_code=410, detail="Codice invito già utilizzato")
        room = await db.rooms.find_one({"id": claimed["room_id"]}, {"_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")
        if room.get("status") == "settled":
            await db.invites.update_one(
                {"id": claimed["id"]},
                {"$set": {"used_by_user_id": None, "used_at": None}},
            )
            raise HTTPException(status_code=400, detail="Stanza già chiusa")
        # Multi-entry: every successful invite claim creates a NEW membership
        # (slot). Users can therefore participate multiple times in the same
        # room by holding multiple invites and thus upload multiple schedine
        # — each tied to its own membership slot.
        existing_count = await db.memberships.count_documents({
            "room_id": room["id"], "user_id": user["id"],
        })
        await db.memberships.insert_one({
            "id": str(uuid.uuid4()),
            "room_id": room["id"],
            "user_id": user["id"],
            "invite_id": claimed["id"],
            "invite_code": claimed["code"],
            "slot": existing_count + 1,
            "display_name": display_name(user),
            "joined_at": now,
        })
        return await _room_dict(room, user)

    @router.get("/rooms/{room_id}")
    async def get_room(room_id: str, user: dict = Depends(current_user)):
        await _ensure_member(room_id, user)
        room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")
        return await _room_dict(room, user)

    @router.get("/rooms/{room_id}/my-memberships")
    async def my_memberships(room_id: str, user: dict = Depends(current_user)):
        """Return the caller's memberships (slots) in the given room.

        With multi-entry a single user may hold N memberships in the same
        room, each identified by its own ``id`` (which is what the schedina
        endpoints require as ``membership_id``).
        """
        rows = [m async for m in db.memberships.find(
            {"room_id": room_id, "user_id": user["id"]}, {"_id": 0},
        ).sort("joined_at", 1)]
        # Renumber slots deterministically 1..N by joined_at
        out = []
        for idx, m in enumerate(rows, start=1):
            s = await db.schedine.find_one(
                {"membership_id": m.get("id")}, {"_id": 0, "status": 1, "events": 1},
            )
            # Legacy fallback: memberships created before multi-entry may
            # not have an ``id`` — try to link the (soon-unique) schedina
            # by (room_id, user_id) instead.
            if not s and not m.get("id"):
                s = await db.schedine.find_one(
                    {"room_id": room_id, "user_id": user["id"]}, {"_id": 0, "status": 1, "events": 1},
                )
            out.append({
                "id": m.get("id"),
                "invite_id": m.get("invite_id"),
                "invite_code": m.get("invite_code"),
                "slot": idx,
                "joined_at": m.get("joined_at"),
                "has_schedina": s is not None,
                "schedina_status": s.get("status") if s else None,
                "schedina_events_count": len(s.get("events") or []) if s else 0,
            })
        return out

    @router.patch("/rooms/{room_id}")
    async def update_room(room_id: str, data: RoomUpdate, user: dict = Depends(require_admin)):
        patch = {k: v for k, v in data.model_dump(exclude_unset=True).items()}
        if not patch:
            raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")
        if "color" in patch and patch["color"] not in ROOM_COLORS:
            patch.pop("color")
        if "deadline_at" in patch:
            raw = patch["deadline_at"]
            if raw in (None, ""):
                await db.rooms.update_one({"id": room_id}, {"$unset": {"deadline_at": ""}})
                patch.pop("deadline_at")
            else:
                dt = _parse_iso_datetime(raw)
                if not dt:
                    raise HTTPException(status_code=400, detail="Data/ora termine non valida")
                patch["deadline_at"] = dt.astimezone(timezone.utc).isoformat()
        if patch:
            result = await db.rooms.update_one({"id": room_id}, {"$set": patch})
            if result.matched_count == 0:
                raise HTTPException(status_code=404, detail="Stanza non trovata")
        room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")
        return await _room_dict(room, user)

    @router.delete("/rooms/{room_id}")
    async def delete_room(room_id: str, user: dict = Depends(require_admin)):
        room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")
        await db.rooms.delete_one({"id": room_id})
        await db.memberships.delete_many({"room_id": room_id})
        await db.schedine.delete_many({"room_id": room_id})
        await db.fixtures.delete_many({"room_id": room_id})
        await db.invites.delete_many({"room_id": room_id})
        return {"ok": True}

    @router.post("/rooms/{room_id}/kick/{user_id}")
    async def kick_from_room(
        room_id: str, user_id: str, user: dict = Depends(require_admin),
    ):
        """Hard-remove a player from a Tiket room: removes membership + all
        schedine + frees any used invite slot for that user. Irreversible."""
        room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")
        target = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
        if not target:
            raise HTTPException(status_code=404, detail="Utente non trovato")
        # Cannot kick the room admin
        if room.get("admin_user_id") == user_id:
            raise HTTPException(
                status_code=400, detail="Impossibile escludere l'admin della stanza",
            )
        m = await db.memberships.find_one({"room_id": room_id, "user_id": user_id})
        if not m:
            raise HTTPException(
                status_code=404, detail="Il giocatore non è iscritto a questa stanza",
            )
        deleted_schedine = await db.schedine.delete_many(
            {"room_id": room_id, "user_id": user_id}
        )
        await db.memberships.delete_many({"room_id": room_id, "user_id": user_id})
        return {
            "ok": True,
            "deleted_schedine": deleted_schedine.deleted_count,
            "kicked_user_id": user_id,
        }

    # ==================================================================
    # Rooms — invites (one-shot)
    # ==================================================================

    @router.get("/rooms/{room_id}/invites")
    async def list_invites(room_id: str, user: dict = Depends(require_admin)):
        room = await db.rooms.find_one({"id": room_id}, {"id": 1, "_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")
        invites = [i async for i in db.invites.find(
            {"room_id": room_id}, {"_id": 0},
        ).sort("created_at", 1)]
        return [await _invite_dict(i) for i in invites]

    @router.post("/rooms/{room_id}/invites")
    async def create_invite(room_id: str, user: dict = Depends(require_admin)):
        room = await db.rooms.find_one({"id": room_id}, {"id": 1, "game": 1, "_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")
        for _ in range(20):
            code = _gen_code()
            if not await db.invites.find_one({"code": code}) and not await db.rooms.find_one({"invite_code": code}):
                break
        else:
            raise HTTPException(
                status_code=500,
                detail="Impossibile generare un codice univoco, riprova",
            )
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "id": str(uuid.uuid4()),
            "room_id": room_id,
            "game": room.get("game", DEFAULT_GAME) if isinstance(room, dict) else DEFAULT_GAME,
            "code": code,
            "used_by_user_id": None,
            "used_at": None,
            "created_at": now,
            "created_by": user["id"],
            "revoked_at": None,
        }
        await db.invites.insert_one(doc)
        return await _invite_dict(doc)

    @router.delete("/rooms/{room_id}/invites/{invite_id}")
    async def revoke_invite(room_id: str, invite_id: str, user: dict = Depends(require_admin)):
        inv = await db.invites.find_one({"id": invite_id, "room_id": room_id}, {"_id": 0})
        if not inv:
            raise HTTPException(status_code=404, detail="Invito non trovato")
        if inv.get("used_by_user_id"):
            raise HTTPException(
                status_code=400,
                detail="Impossibile revocare: invito già utilizzato",
            )
        if inv.get("revoked_at"):
            return await _invite_dict(inv)
        now = datetime.now(timezone.utc).isoformat()
        await db.invites.update_one({"id": invite_id}, {"$set": {"revoked_at": now}})
        inv["revoked_at"] = now
        return await _invite_dict(inv)

    @router.get("/rooms/{room_id}/members")
    async def list_members(room_id: str, user: dict = Depends(current_user)):
        await _ensure_member(room_id, user)
        memberships = [m async for m in db.memberships.find({"room_id": room_id}, {"_id": 0})]
        if not memberships:
            return []
        user_ids = [m["user_id"] for m in memberships]
        users_map = {}
        async for u in db.users.find(
            {"id": {"$in": user_ids}}, {"_id": 0, "password_hash": 0},
        ):
            users_map[u["id"]] = u
        submitted_ids = set()
        async for s in db.schedine.find(
            {"room_id": room_id, "status": "confirmed"}, {"user_id": 1, "_id": 0}
        ):
            submitted_ids.add(s["user_id"])
        result = []
        for m in memberships:
            u = users_map.get(m["user_id"], {})
            result.append({
                "user_id": m["user_id"],
                "nickname": m.get("display_name") or u.get("username") or u.get("email") or "?",
                "role": u.get("role", "player"),
                "blocked": u.get("blocked", False),
                "submitted": m["user_id"] in submitted_ids,
            })
        return result

    @router.post("/rooms/{room_id}/close")
    async def close_room(room_id: str, user: dict = Depends(require_admin)):
        await db.rooms.update_one({"id": room_id}, {"$set": {"status": "closed"}})
        return {"ok": True}

    # ==================================================================
    # Schedina — OCR upload + confirm
    # ==================================================================

    @router.post("/rooms/{room_id}/schedina/ocr")
    async def upload_schedina(
        room_id: str, data: ScreenshotIn, user: dict = Depends(current_user),
    ):
        await _ensure_member(room_id, user)
        room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")
        if room.get("status") == "settled":
            raise HTTPException(status_code=400, detail="Stanza chiusa")
        await _ensure_submissions_open(room)

        owner = await _resolve_target_user(room_id, user, data.on_behalf_of)
        membership = await _resolve_membership(room_id, owner, data.membership_id)
        logger.info(
            "OCR upload room=%s actor=%s owner=%s membership=%s (%s)",
            room_id, user.get("username") or user.get("email"),
            owner.get("username") or owner.get("email"),
            membership.get("id"),
            "SELF" if owner["id"] == user["id"] else "ON BEHALF OF",
        )

        b64 = data.image_base64
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        try:
            raw = base64.b64decode(b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Immagine base64 non valida")

        result = await ocr_screenshot(raw)
        parsed = result["events"]

        # ---- ANTI-CHEAT (color-signature) ------------------------------
        # Only Star Yes bet slips are accepted. Detection is done purely
        # on the pixel palette (dark navy #102040 dominance + specific
        # cyan / green accents) so it's deterministic and immune to OCR
        # misreads. See _is_staryes_by_color for thresholds.
        is_sy, sy_reason, sy_metrics = _is_staryes_by_color(raw)
        if not is_sy:
            logger.warning(
                "Rejected non-staryes slip by COLOR (room=%s, actor=%s, "
                "reason=%s, metrics=%s)",
                room_id,
                user.get("username") or user.get("email"),
                sy_reason,
                sy_metrics,
            )
            raise HTTPException(
                status_code=400,
                detail="Schedina non corrispondente al sito staryes.",
            )

        if len(parsed) > room["max_events"]:
            parsed = parsed[: room["max_events"]]

        await db.schedine.update_one(
            {"membership_id": membership["id"]},
            {"$set": {
                "membership_id": membership["id"],
                "room_id": room_id,
                "user_id": owner["id"],
                "nickname": display_name(owner),
                "screenshot_base64": b64,
                "raw_text": result["raw_text"],
                "events": parsed,
                "status": "draft",
                "uploaded_by": user["id"] if owner["id"] != user["id"] else None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        return {
            "events": parsed,
            "membership_id": membership["id"],
            "raw_text": result["raw_text"],
            "max_events": room["max_events"],
            "owner": {"id": owner["id"], "nickname": display_name(owner)},
        }

    @router.post("/rooms/{room_id}/schedina/confirm")
    async def confirm_schedina(
        room_id: str, data: SchedinaConfirm, user: dict = Depends(current_user),
    ):
        """Confirm the OCR draft as the player's final bet slip.

        IMPORTANT — anti-cheat: this endpoint IGNORES any events sent by the
        client and always uses the OCR-parsed events stored server-side during
        :func:`upload_schedina`.
        """
        await _ensure_member(room_id, user)
        room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")
        if room.get("status") == "settled":
            raise HTTPException(status_code=400, detail="Stanza chiusa")
        await _ensure_submissions_open(room)

        owner = await _resolve_target_user(
            room_id, user, data.on_behalf_of if data else None,
        )
        membership = await _resolve_membership(
            room_id, owner, data.membership_id if data else None,
        )

        draft = await db.schedine.find_one(
            {"membership_id": membership["id"]}, {"_id": 0},
        )
        if not draft or not draft.get("events"):
            raise HTTPException(
                status_code=400,
                detail="Nessuna schedina caricata. Carica prima uno screenshot.",
            )

        ocr_events = draft["events"]
        if len(ocr_events) > room["max_events"]:
            ocr_events = ocr_events[: room["max_events"]]

        bad = [e for e in ocr_events if not e.get("prediction")]
        if bad:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"L'OCR non ha riconosciuto {len(bad)} pronostici. "
                    "Rifai lo screenshot con maggiore risoluzione."
                ),
            )
        for e in ocr_events:
            odd = e.get("odd") or 0
            if not (1.01 <= odd <= 999):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "L'OCR non ha letto correttamente le quote. "
                        "Rifai lo screenshot con maggiore risoluzione."
                    ),
                )
            # ---- Anti-tamper: reject odds above the sanity cap ------------
            if _odd_exceeds_cap(e["prediction"], float(odd)):
                cap = _max_odd_for_prediction(e["prediction"])
                logger.warning(
                    "Rejected schedina — quota fuori range (room=%s, actor=%s, "
                    "prediction=%s, odd=%.2f, cap=%.2f)",
                    room_id,
                    user.get("username") or user.get("email"),
                    e["prediction"], odd, cap,
                )
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Quota sospetta: {e['home_team']} vs {e['away_team']} — "
                        f"{e['prediction']} @ {odd:.2f} (max plausibile {cap:.2f}). "
                        "La schedina è stata bloccata perché la quota supera i limiti "
                        "attesi su staryes.it. Carica lo screenshot originale non modificato."
                    ),
                )
            # ---- Anti-tamper: Gemini AI has flagged pixel-level manipulation
            if e.get("quota_tampering_suspect"):
                logger.warning(
                    "Rejected schedina — Gemini flagged tampering (room=%s, actor=%s, "
                    "prediction=%s, odd=%.2f)",
                    room_id,
                    user.get("username") or user.get("email"),
                    e["prediction"], odd,
                )
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Possibile manipolazione grafica rilevata sulla quota di "
                        f"{e['home_team']} vs {e['away_team']} ({e['prediction']} @ {odd:.2f}). "
                        "Carica lo screenshot originale non modificato."
                    ),
                )

        await db.schedine.update_one(
            {"membership_id": membership["id"]},
            {"$set": {
                "membership_id": membership["id"],
                "room_id": room_id,
                "user_id": owner["id"],
                "nickname": display_name(owner),
                "events": ocr_events,
                "status": "confirmed",
                "confirmed_by": user["id"] if owner["id"] != user["id"] else None,
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        _ = data  # events in body intentionally ignored (anti-cheat)
        return {
            "ok": True,
            "events": ocr_events,
            "membership_id": membership["id"],
            "owner": {"id": owner["id"], "nickname": display_name(owner)},
        }

    @router.get("/rooms/{room_id}/schedina")
    async def my_schedina(
        room_id: str,
        user: dict = Depends(current_user),
        on_behalf_of: Optional[str] = None,
        membership_id: Optional[str] = None,
    ):
        """Return the caller's schedina in the given room. Admins of the room
        may fetch another player's schedina via ``?on_behalf_of=<user_id>``.

        For multi-slot users, pass ``?membership_id=<id>`` to select the
        specific slot; if omitted and the user has only 1 slot, that one is
        used automatically.
        """
        await _ensure_member(room_id, user)
        owner = await _resolve_target_user(room_id, user, on_behalf_of)
        try:
            membership = await _resolve_membership(room_id, owner, membership_id)
        except HTTPException:
            # Owner is not a member (or has 0 memberships) — return empty
            # instead of 400 so the UI can render "no schedina yet".
            return {"empty": True}
        s = await db.schedine.find_one(
            {"membership_id": membership["id"]},
            {"_id": 0, "screenshot_base64": 0, "raw_text": 0},
        )
        return s or {"empty": True, "membership_id": membership["id"]}

    @router.get("/rooms/{room_id}/schedine/all")
    async def list_all_schedine(
        room_id: str,
        user: dict = Depends(current_user),
    ):
        """Public listing of every member's schedina, gated by the global
        deadline (same rule as Survival & the other games).

        Before the deadline: the caller sees only their own schedine;
        every other user's entry is marked ``hidden: true`` without content.
        After the deadline: full content is exposed to every room member.
        """
        room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")

        # Must be either a room member (any slot) or a global admin
        is_admin = user["role"] == "admin" or user["id"] == room.get("admin_user_id")
        is_member = bool(await db.memberships.find_one(
            {"room_id": room_id, "user_id": user["id"]}, {"_id": 1}
        ))
        if not (is_admin or is_member):
            raise HTTPException(status_code=403, detail="Non fai parte di questa stanza")

        season = room.get("season") or "2026-27"
        md = room.get("matchday")
        deadline_passed = False
        if isinstance(md, int):
            deadline_passed = await _global_deadline_passed(db, season, md)

        # Gather all schedine of this room
        rows: List[dict] = []
        async for s in db.schedine.find(
            {"room_id": room_id}, {"_id": 0},
        ).sort("updated_at", -1):
            uid = s["user_id"]
            is_self = uid == user["id"]
            can_see = deadline_passed or is_self or is_admin
            row: Dict[str, Any] = {
                "user_id": uid,
                "membership_id": s.get("membership_id"),
                "nickname": s.get("nickname", "?"),
                "status": s.get("status", "draft"),
                "hidden": not can_see,
                "updated_at": s.get("updated_at"),
            }
            if can_see:
                row.update({
                    "events": s.get("events") or [],
                    "screenshot_base64": s.get("screenshot_base64", ""),
                    "raw_text": s.get("raw_text", ""),
                })
            rows.append(row)
        return {
            "room_id": room_id,
            "matchday": md,
            "deadline_passed": deadline_passed,
            "schedine": rows,
        }

    @router.get("/rooms/{room_id}/schedina-review/{user_id}")
    async def schedina_review(
        room_id: str,
        user_id: str,
        user: dict = Depends(current_user),
    ):
        """Admin-only review: return the FULL schedina of *user_id* including
        the raw screenshot (base64) and per-event tamper flags.

        Response shape::

            {
              "user_id": str, "nickname": str,
              "screenshot_base64": str, "raw_text": str,
              "status": "draft" | "confirmed",
              "events": [
                {
                  "home_team", "away_team", "market_raw", "prediction", "odd",
                  # anti-tamper metadata (added by this endpoint):
                  "odd_cap": float,               # max plausible odd for this market
                  "odd_exceeds_cap": bool,        # true if OCR odd is above cap
                  "quota_tampering_suspect": bool # flagged by Gemini AI
                }, ...
              ],
            }
        """
        # Only room admins (or global admins) can review someone else's slip.
        room = await db.rooms.find_one({"id": room_id}, {"admin_user_id": 1, "_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")
        is_room_admin = user["role"] == "admin" or user["id"] == room.get("admin_user_id")
        if not is_room_admin:
            raise HTTPException(status_code=403, detail="Solo admin possono ispezionare le schedine")

        s = await db.schedine.find_one(
            {"room_id": room_id, "user_id": user_id}, {"_id": 0},
        )
        if not s:
            raise HTTPException(status_code=404, detail="Schedina non trovata")

        # Enrich events with anti-tamper metadata for the admin UI.
        events_out = []
        for e in s.get("events") or []:
            pred = e.get("prediction") or ""
            odd = float(e.get("odd") or 0)
            events_out.append({
                **e,
                "odd_cap": _max_odd_for_prediction(pred),
                "odd_exceeds_cap": _odd_exceeds_cap(pred, odd),
                # Preserve any AI tamper flag saved at OCR time (default false).
                "quota_tampering_suspect": bool(e.get("quota_tampering_suspect")),
            })
        return {
            "user_id": s["user_id"],
            "nickname": s.get("nickname", "?"),
            "screenshot_base64": s.get("screenshot_base64", ""),
            "raw_text": s.get("raw_text", ""),
            "status": s.get("status", "draft"),
            "events": events_out,
            "uploaded_by": s.get("uploaded_by"),
            "confirmed_by": s.get("confirmed_by"),
            "confirmed_at": s.get("confirmed_at"),
            "updated_at": s.get("updated_at"),
        }

    # ==================================================================
    # Fixtures / Results
    # ==================================================================

    @router.post("/rooms/{room_id}/fixtures")
    async def set_fixtures(
        room_id: str, data: FixturesIn, user: dict = Depends(require_admin),
    ):
        await db.fixtures.delete_many({"room_id": room_id})
        docs = []
        for f in data.fixtures:
            both = f.both_scored if f.both_scored is not None else (f.home_score > 0 and f.away_score > 0)
            docs.append({
                "room_id": room_id,
                "home_team": f.home_team.strip(),
                "away_team": f.away_team.strip(),
                "home_score": f.home_score,
                "away_score": f.away_score,
                "both_scored": both,
                "postponed": bool(f.postponed),
            })
        if docs:
            await db.fixtures.insert_many(docs)
        return {"ok": True, "count": len(docs),
                "postponed_count": sum(1 for d in docs if d["postponed"])}

    @router.get("/rooms/{room_id}/fixtures")
    async def get_fixtures(room_id: str, user: dict = Depends(current_user)):
        await _ensure_member(room_id, user)
        cursor = db.fixtures.find({"room_id": room_id}, {"_id": 0})
        return [f async for f in cursor]

    @router.post("/rooms/{room_id}/fixtures/sync")
    async def sync_fixtures_from_api(*_a, **_kw):
        """DEPRECATED: API-Football sync was replaced by PDF Voti ingestion."""
        raise HTTPException(
            status_code=410,
            detail=(
                "API-Football è stata rimossa. Usa il PDF Voti dall'admin "
                "per aggiornare i risultati (auto-settlement)."
            ),
        )

    @router.post("/rooms/{room_id}/fixtures/compute-from-facts")
    async def compute_fixtures_from_facts(
        room_id: str,
        matchday: Optional[int] = None,
        user: dict = Depends(require_admin),
    ):
        """Auto-derive fixture scores from the ``matchday_facts`` collection.

        Flow:
        1. Aggregate goals per team from ``matchday_facts`` for the given
           matchday: ``own_goals[team] = Σ(gf+rf)``, ``autogoals[team] = Σ(au)``.
        2. Collect all unique (home, away) pairs from the room's confirmed
           schedine — those are the fixtures we need to settle.
        3. For every fixture, fuzzy-match the team names against the
           aggregation and compute the score:
               home_score = own_goals[home] + autogoals[away_opponent]
               away_score = own_goals[away] + autogoals[home_opponent]
        4. Persist the resulting fixtures into ``fixtures`` (replacing any
           previous entries for this room).

        Returns a preview with the number of fixtures settled and any pairs
        that could not be resolved (so the admin can spot mismatched names).
        """
        room = await db.rooms.find_one({"id": room_id}, {"_id": 0})
        if not room:
            raise HTTPException(status_code=404, detail="Stanza non trovata")

        md = matchday if matchday is not None else room.get("matchday")
        if not md or md < 1 or md > 38:
            raise HTTPException(status_code=400, detail="Giornata non valida (1..38)")

        # --- 1) Aggregate goals per team from matchday_facts ---
        facts_cur = db.matchday_facts.find(
            {"matchday": md},
            {"_id": 0, "team": 1, "gf": 1, "rf": 1, "au": 1},
        )
        own_goals: Dict[str, int] = {}
        autogoals: Dict[str, int] = {}
        facts_count = 0
        async for f in facts_cur:
            facts_count += 1
            team = f.get("team") or ""
            own_goals[team] = own_goals.get(team, 0) + int(f.get("gf") or 0) + int(f.get("rf") or 0)
            autogoals[team] = autogoals.get(team, 0) + int(f.get("au") or 0)

        if facts_count == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Nessun dato Voti per la giornata {md}. "
                    "Carica prima il PDF Voti dall'area Admin (Voti → Carica PDF)."
                ),
            )

        # --- 2) Unique fixture pairs from the room's confirmed schedine ---
        pairs: List[tuple[str, str]] = []
        seen = set()
        async for s in db.schedine.find(
            {"room_id": room_id, "status": "confirmed"},
            {"_id": 0, "events": 1},
        ):
            for e in s.get("events") or []:
                key = (e.get("home_team", "").strip(), e.get("away_team", "").strip())
                if not key[0] or not key[1] or key in seen:
                    continue
                seen.add(key)
                pairs.append(key)

        if not pairs:
            raise HTTPException(
                status_code=400,
                detail="Nessuna schedina confermata: non c'è nulla da calcolare.",
            )

        # --- 3) Match team names → build fixtures ---
        teams_in_facts = list(own_goals.keys())

        def _resolve(name: str) -> Optional[str]:
            """Return the canonical facts-team name matching *name* (fuzzy)."""
            for t in teams_in_facts:
                if _team_match(name, t):
                    return t
            return None

        settled: List[dict] = []
        unresolved: List[dict] = []
        for home, away in pairs:
            h_team = _resolve(home)
            a_team = _resolve(away)
            if not h_team or not a_team:
                unresolved.append({
                    "home_team": home,
                    "away_team": away,
                    "home_resolved": h_team,
                    "away_resolved": a_team,
                })
                continue
            home_score = own_goals.get(h_team, 0) + autogoals.get(a_team, 0)
            away_score = own_goals.get(a_team, 0) + autogoals.get(h_team, 0)
            settled.append({
                "room_id": room_id,
                "home_team": home,
                "away_team": away,
                "home_score": home_score,
                "away_score": away_score,
                "both_scored": home_score > 0 and away_score > 0,
                "source": f"voti_pdf_md{md}",
                "resolved_home": h_team,
                "resolved_away": a_team,
            })

        # --- 4) Persist ---
        await db.fixtures.delete_many({"room_id": room_id})
        if settled:
            # Strip helper fields before insert (keep only fixture schema).
            docs = [
                {k: v for k, v in f.items() if k not in ("source", "resolved_home", "resolved_away")}
                for f in settled
            ]
            await db.fixtures.insert_many(docs)

        return {
            "matchday": md,
            "facts_count": facts_count,
            "teams_found": len(teams_in_facts),
            "fixtures_settled": len(settled),
            "fixtures_unresolved": len(unresolved),
            "unresolved": unresolved,
            "settled": [
                {
                    "home_team": f["home_team"],
                    "away_team": f["away_team"],
                    "home_score": f["home_score"],
                    "away_score": f["away_score"],
                }
                for f in settled
            ],
        }

    # ==================================================================
    # Leaderboard
    # ==================================================================

    @router.get("/rooms/{room_id}/leaderboard")
    async def leaderboard(room_id: str, user: dict = Depends(current_user)):
        await _ensure_member(room_id, user)
        fixtures = [f async for f in db.fixtures.find({"room_id": room_id}, {"_id": 0})]
        has_results = len(fixtures) > 0

        schedine_cur = db.schedine.find(
            {"room_id": room_id, "status": "confirmed"}, {"_id": 0},
        )
        entries = []
        async for s in schedine_cur:
            events = s.get("events", [])
            breakdown = []
            product = 1.0
            won_count = 0
            for e in events:
                info = {
                    "home_team": e["home_team"],
                    "away_team": e["away_team"],
                    "prediction": e["prediction"],
                    "odd": e["odd"],
                    "won": False,
                    "postponed": False,
                    "matched_fixture": None,
                    "score": None,
                }
                if has_results:
                    fx = _match_prediction_to_fixture(e, fixtures)
                    if fx:
                        info["matched_fixture"] = f"{fx['home_team']} vs {fx['away_team']}"
                        info["score"] = f"{fx['home_score']}-{fx['away_score']}"
                        if fx.get("postponed"):
                            # Postponed match → prediction is auto-won with
                            # quota 1.00 (neutral contribution to the schedina).
                            info["won"] = True
                            info["postponed"] = True
                            info["score"] = "RINV."
                            product *= 1.0
                            won_count += 1
                        elif _evaluate_prediction(e["prediction"], fx):
                            info["won"] = True
                            product *= e["odd"]
                            won_count += 1
                breakdown.append(info)
            total = round(product, 2) if won_count > 0 else 0.0
            entries.append({
                "user_id": s["user_id"],
                "nickname": s.get("nickname", "?"),
                "total": total,
                "won_count": won_count,
                "events_count": len(events),
                "breakdown": breakdown,
            })
        entries.sort(key=lambda x: (-x["total"], x["nickname"]))
        for i, r in enumerate(entries):
            r["rank"] = i + 1
        return {
            "has_results": has_results,
            "settled": has_results and len(entries) > 0,
            "leaderboard": entries,
        }

    return router


__all__ = [
    "GAMES",
    "DEFAULT_GAME",
    "ROOM_COLORS",
    "_evaluate_prediction",
    "_classify_bet",
    "_validate_prediction_code",
    "_parse_staryes_slip",
    "ocr_screenshot",
    "ensure_indexes",
    "backfill_legacy",
    "build_router",
]
