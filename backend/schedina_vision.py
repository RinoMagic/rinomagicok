"""Schedina Vision — screenshot understanding via multimodal LLMs.

Replaces the fragile Tesseract-OCR + regex pipeline with a Gemini 3 Flash
call that "reads" the bet-slip screenshot and returns a strict JSON list of
events. Tesseract remains available as a last-resort fallback.

Contract (per event, exactly like the legacy OCR output):
    {
      "home_team":  str,             # e.g. "Juventus"
      "away_team":  str,             # e.g. "Milan"
      "market_raw": str,             # verbatim market label from the slip
      "prediction": str,             # canonical code, see below
      "odd":        float,           # decimal odd, e.g. 1.85
    }

Prediction canonicalisation (must match ``_SIMPLE_ATOM_RE`` in server.py):
    1 X 2 1X X2 12
    GOL NOGOL
    OVER-<t> / UNDER-<t>            # t in {0.5,1.5,2.5,3.5,4.5}
    MG-<a>-<b>[-NO] / MGH- / MGA-   # multigol total/home/away
Combos join atoms with ``+`` (e.g. ``1X+GOL``, ``2+OVER-2.5``).

The module never raises — on any failure it returns an empty list and a
descriptive ``error`` so the caller can decide whether to fall back to
Tesseract or surface the message to the user.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("schedina_vision")

_EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "").strip()
_MODEL_PROVIDER = "gemini"
_MODEL_NAME = "gemini-3-flash-preview"

# Max image size (bytes) we forward to the model. Larger images are downscaled
# by the provider anyway, but keeping payloads reasonable saves bandwidth.
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB


# =========================================================================
# Prompt
# =========================================================================
SYSTEM_PROMPT = """Sei un OCR specializzato per SCHEDINE di scommesse sportive italiane (Serie A/Europa).
Devi estrarre, per ogni evento della schedina, i seguenti campi in JSON:

- "home_team": nome squadra CASA (in italiano, come scritto sulla schedina).
- "away_team": nome squadra TRASFERTA.
- "market_raw": la stringa esatta del mercato come compare (es. "1X2", "GG/NG", "Under/Over 2.5", "Doppia Chance", "Risultato Esatto", "Multigol Casa 1-2", ...).
- "prediction": il codice CANONICO del pronostico (regole più sotto).
- "odd": quota decimale (float, es. 1.85).
- "quota_tampering_suspect": true SOLO se la cella della QUOTA (odd) mostra chiari segni di manipolazione grafica. Analizza attentamente:
    * font e dimensione dei caratteri della quota DIVERSI rispetto alle altre quote/testi sulla schedina (weight, altezza, larghezza)
    * anti-aliasing incoerente (bordi delle cifre sfocati o troppo netti rispetto al resto)
    * colore o luminosità della quota diversi dal resto della riga
    * pixel/artefatti compressione JPEG concentrati attorno alla quota
    * baseline testo disallineata o kerning atipico rispetto ad altri numeri
    * sfondo dietro alla quota alterato (macchie, aloni)
  Se l'immagine è pulita e coerente restituisci quota_tampering_suspect: false. IMPORTANTE:
  in caso di dubbio metti false — segnala true solo se l'evidenza è chiara.

DEVI ANCHE IDENTIFICARE IL BOOKMAKER (sito di scommesse) da cui proviene lo screenshot,
osservando logo, header, footer, colori dominanti, layout, watermark, testi visibili tipo
"STARYES", "SNAI", "SISAL", "BETFLAG", "BET365", "WILLIAM HILL", "GOLDBET", "PLANETWIN",
"LOTTOMATICA", "BETFAIR", "888", ecc. Restituisci il nome del sito in minuscolo nel
campo "bookmaker" (es. "staryes.it", "snai", "sisal", "bet365"). Se non riesci a
identificarlo con ragionevole certezza usa "unknown".

CODICI CANONICI DEL PRONOSTICO (obbligatori — nessun altro formato è accettato):
  Risultato finale 1X2:        "1", "X", "2"
  Doppia Chance:               "1X", "X2", "12"
  Gol/No Gol (GG/NG):          "GOL"    (Both Teams Score)
                               "NOGOL"  (No Both Teams Score)
  Over/Under (soglia esplicita):
                               "OVER-0.5"  "OVER-1.5"  "OVER-2.5"  "OVER-3.5"  "OVER-4.5"
                               "UNDER-0.5" "UNDER-1.5" "UNDER-2.5" "UNDER-3.5" "UNDER-4.5"
                               (se la schedina scrive "Over" o "Under" senza numero → usa 2.5)
  Multigol totale:             "MG-<a>-<b>"      es. "MG-1-3", "MG-2-4"
  Multigol totale NO:          "MG-<a>-<b>-NO"
  Multigol Casa:               "MGH-<a>-<b>[-NO]"
  Multigol Ospite:             "MGA-<a>-<b>[-NO]"
  Risultato Esatto:            "RE-<home>-<away>"   es. "RE-2-1", "RE-0-0"
  COMBO: unire gli atomi con "+" (es. "1X+GOL", "2+OVER-2.5", "1+MG-1-3").

RIFIUTI (imposta prediction="" se non traducibile in questi codici):
  - Marcatore
  - Handicap
  - Draw No Bet
  - Qualsiasi mercato di serie B/live/piazzato non nella lista sopra.

REGOLE DI OUTPUT:
1. Rispondi SOLO con JSON puro, senza markdown, senza ``` ``` , senza commenti.
2. Formato JSON:
   {"bookmaker": "<sito>", "events": [ {home_team, away_team, market_raw, prediction, odd, quota_tampering_suspect}, ... ], "note": "opzionale"}
3. Ordine degli eventi = ordine sulla schedina (dall'alto in basso).
4. Nomi squadre come li leggi (rispettando maiuscole/minuscole originali; niente sigle inventate).
5. Quote come float con "." (es. 1.85). Se la quota non è leggibile, usa 0.
6. Se la schedina è già un ticket giocato e mostra il totale finale, IGNORALO — estrai solo i singoli eventi.
7. Se una partita ha un mercato non traducibile (vedi RIFIUTI), lascia prediction="" ma includi comunque l'evento con market_raw compilato.
8. NON inventare eventi che non ci sono. Meglio 3 eventi certi che 5 di cui alcuni inventati.
9. Se lo screenshot non è una schedina o è illeggibile, restituisci {"bookmaker": "unknown", "events": [], "note": "motivazione breve"}."""


USER_PROMPT_TEXT = (
    "Estrai TUTTI gli eventi di questa schedina in JSON secondo le regole del sistema. "
    "Ricordati: solo JSON, niente markdown, prediction in codice canonico."
)


# =========================================================================
# Public API
# =========================================================================
def is_available() -> bool:
    """True if we have a valid Emergent LLM key configured."""
    return bool(_EMERGENT_LLM_KEY and _EMERGENT_LLM_KEY.lower() != "placeholder")


async def extract_events_from_image(image_bytes: bytes) -> Dict[str, Any]:
    """Extract structured events from a bet-slip screenshot using Gemini Vision.

    Returns a dict::

        {
          "events":   List[dict]   # each with home_team/away_team/market_raw/prediction/odd
          "raw_text": str          # the model's raw JSON reply (for debugging)
          "note":     Optional[str],
          "error":    Optional[str],  # non-null on hard failure (empty events)
          "provider": "gemini-3-flash-preview" | ...,
        }

    Never raises — callers decide whether to fall back.
    """
    if not is_available():
        return {
            "events": [], "raw_text": "", "note": None,
            "error": "EMERGENT_LLM_KEY non configurata",
            "provider": None,
        }

    if len(image_bytes) > MAX_IMAGE_BYTES:
        return {
            "events": [], "raw_text": "", "note": None,
            "error": f"Immagine troppo grande ({len(image_bytes)//1024} KB, max {MAX_IMAGE_BYTES//1024} KB)",
            "provider": None,
        }

    # Detect mime + optionally shrink oversized images to keep payload small.
    mime, safe_bytes = _sanitize_image(image_bytes)

    try:
        # Local import so that missing dependency never breaks server bootstrap.
        from emergentintegrations.llm.chat import (
            LlmChat, UserMessage, ImageContent,
        )
    except Exception as e:
        logger.exception("emergentintegrations import failed")
        return {
            "events": [], "raw_text": "", "note": None,
            "error": f"Libreria LLM non disponibile: {e}",
            "provider": None,
        }

    b64 = base64.b64encode(safe_bytes).decode("ascii")
    image = ImageContent(image_base64=b64)

    session_id = f"schedina-{uuid.uuid4().hex[:12]}"
    chat = (
        LlmChat(
            api_key=_EMERGENT_LLM_KEY,
            session_id=session_id,
            system_message=SYSTEM_PROMPT,
        )
        .with_model(_MODEL_PROVIDER, _MODEL_NAME)
    )

    try:
        # Non-streaming — we need the whole JSON before parsing.
        reply = await chat.send_message(
            UserMessage(text=USER_PROMPT_TEXT, file_contents=[image])
        )
    except Exception as e:
        logger.exception("Gemini vision call failed")
        return {
            "events": [], "raw_text": "", "note": None,
            "error": f"Errore chiamata AI Vision: {type(e).__name__}: {e}",
            "provider": _MODEL_NAME,
        }

    raw_text = _stringify_reply(reply)
    parsed = _parse_json_reply(raw_text)
    events = _sanitize_events(parsed.get("events", []))
    note = parsed.get("note")
    bookmaker = (parsed.get("bookmaker") or "").strip().lower()

    logger.info(
        "Vision extraction: %d events (provider=%s, bookmaker=%s, chars=%d, note=%s)",
        len(events), _MODEL_NAME, bookmaker or "?", len(raw_text), (note or "")[:80],
    )

    return {
        "events": events,
        "raw_text": raw_text,
        "note": note,
        "bookmaker": bookmaker,
        "error": None if events or note else "Nessun evento rilevato",
        "provider": _MODEL_NAME,
    }


# =========================================================================
# Helpers
# =========================================================================
def _sanitize_image(image_bytes: bytes) -> Tuple[str, bytes]:
    """Return (mime, bytes). Ensure PNG/JPEG only; downscale to sane size.

    We rely on PIL (already used elsewhere in the app) — if it isn't
    available we just pass the bytes through with a best-effort mime.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        img_format = (img.format or "").upper()
        # Convert unusual formats (WEBP is OK, GIF/BMP -> PNG, first frame).
        if img_format in {"JPEG", "JPG"}:
            mime = "image/jpeg"
            out_format = "JPEG"
        elif img_format == "WEBP":
            mime = "image/webp"
            out_format = "WEBP"
        else:
            mime = "image/png"
            out_format = "PNG"
        # Flatten transparency for JPEG; convert everything to RGB.
        if out_format == "JPEG" and img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        # Downscale huge shots (> 2000px on the longest edge)
        w, h = img.size
        LONG_MAX = 2000
        if max(w, h) > LONG_MAX:
            ratio = LONG_MAX / float(max(w, h))
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        save_kwargs = {"format": out_format}
        if out_format == "JPEG":
            save_kwargs["quality"] = 85
        img.save(buf, **save_kwargs)
        return mime, buf.getvalue()
    except Exception:
        # PIL missing or image unreadable — best-effort passthrough.
        return "image/png", image_bytes


def _stringify_reply(reply: Any) -> str:
    """emergentintegrations returns either a string or an object with
    ``.content`` / ``.text``; normalize to string."""
    if isinstance(reply, str):
        return reply
    for attr in ("content", "text", "message", "response"):
        v = getattr(reply, attr, None)
        if isinstance(v, str) and v:
            return v
    # Fallback: repr — better than nothing for debugging.
    return str(reply)


_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _parse_json_reply(text: str) -> Dict[str, Any]:
    """Attempt to load JSON from an LLM reply, stripping markdown fences."""
    if not text:
        return {}
    cleaned = text.strip()
    # Strip common markdown fences
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned)
    # Try full parse first
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # Fallback: find first {...} block
    m = _JSON_RE.search(cleaned)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    logger.warning("Vision reply is not valid JSON. Raw: %r", cleaned[:400])
    return {}


# Canonical atoms — must stay in sync with server._SIMPLE_ATOM_RE
_ATOM_RE = re.compile(
    r"^(?:1X|X2|12|1|X|2|GOL|NOGOL"
    r"|(?:OVER|UNDER)-\d(?:\.\d)?"
    r"|MG[HA]?-\d-\d(?:-NO)?"
    r"|RE-\d+-\d+"
    r")$"
)


def _normalize_prediction(pred: Any) -> str:
    """Best-effort normalization of the model's prediction string.

    Returns "" if the code can't be reduced to a valid combo.
    """
    if not pred:
        return ""
    s = str(pred).strip().upper().replace(" ", "").replace(",", ".")
    if not s:
        return ""

    def norm_atom(a: str) -> str:
        # OVER / UNDER with optional threshold
        m = re.match(r"^(OVER|UNDER)[-]?(\d(?:\.\d)?)?$", a)
        if m:
            side = m.group(1)
            thr = m.group(2) or "2.5"
            if "." not in thr:
                thr = f"{thr}.5"
            return f"{side}-{thr}"
        # Aliases occasionally emitted by models
        if a in {"GG", "BTTS"}:
            return "GOL"
        if a in {"NG", "NOBTTS"}:
            return "NOGOL"
        return a

    atoms = [norm_atom(a) for a in s.split("+") if a]
    if not atoms:
        return ""
    for a in atoms:
        if not _ATOM_RE.match(a):
            return ""
    return "+".join(atoms)


def _sanitize_events(items: Any) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        home = str(it.get("home_team") or "").strip()
        away = str(it.get("away_team") or "").strip()
        if not home or not away:
            continue
        market_raw = str(it.get("market_raw") or it.get("market") or "").strip()
        prediction = _normalize_prediction(it.get("prediction"))
        # Odd may come as float / int / string
        odd_raw = it.get("odd")
        try:
            odd = float(str(odd_raw).replace(",", "."))
        except Exception:
            odd = 0.0
        # Zero or negative is invalid → mark as unrecognised (empty prediction)
        if odd <= 0 or odd > 1000:
            odd = 0.0
        # AI tamper flag: only trust explicit boolean true; anything else is false.
        tampering_suspect = bool(it.get("quota_tampering_suspect")) is True
        out.append({
            "home_team": home,
            "away_team": away,
            "market_raw": market_raw,
            "prediction": prediction,
            "odd": odd,
            "quota_tampering_suspect": tampering_suspect,
        })
    return out
