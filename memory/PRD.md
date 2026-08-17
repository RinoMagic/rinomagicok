# RinoMagic (Schedina Bar) — PRD

## Original Problem
Rebuild RinoMagic/RinoMagic as a standard React WEB PWA (NOT Expo/Mobile). Port the original FastAPI backend as-is (same routes/rules, incl. web_push.py) and rebuild the frontend in React web replicating the same look & feel and the games (Tiket, Survival, ScoreAndLive, FantaGiornata) + Bonus/Big Match. Connect to existing MongoDB Atlas `schedinabar` in read/write WITHOUT touching existing data (1479 sal_calendar incl. 380 for 2026-27, 497 sal_players, 9 real users). PWA installable, VAPID web push. Never show Expo/QR.

## Approach (this iteration)
- The GitHub repo was made public; the ORIGINAL FastAPI backend (server.py + auth.py + thebesttiket.py + surviva.py + scoreandlive.py + fantagiornata.py + bonus.py + deadlines.py + matchday_facts.py + matchday_settle.py + web_push.py + schedina_vision.py + excel_parser.py + email_service.py) was copied into /app/backend and runs as-is against Atlas.
- Startup index creation made best-effort (existing prod indexes differ) to avoid crashes; NO data modified.
- Frontend fully rewritten in React web (Expo removed): amber "bar" theme, stadium background, RinoMagic/BARSLOT branding.

## Architecture
- Backend: FastAPI, all routes under /api. Auth = JWT (admin by email, player by username), bcrypt. Games: Tiket at /api/rooms + /api/games; Survival at /api/sv/*; ScoreAndLive /api/sal/*; FantaGiornata /api/fg/*; Bonus /api/bonus/*; Deadlines /api/deadlines/*; Push /api/push/*.
- Frontend: React 19 (CRACO) PWA. Routes: /login, / (Hub), /survival, /survival/:tid, /tiket, /tiket/:roomId, /settings. Token in localStorage `schedinabar_token`.
- DB: MongoDB Atlas `schedinabar`, season 2026-27.

## Implemented (2026-06)
- Login (admin email / player username / register / forgot) — faithful.
- Hub via /api/games with color-coded cards; ScoreAndLive + FantaGiornata show PROSSIMAMENTE; Giochi Bonus card (info).
- Survival 2.0: list, create (admin), join by code, detail with current-matchday 1/X/2 picks (dynamic count = lives), locked teams, participants/leaderboard.
- TheBestTiket: rooms list, create (admin), join by code, room detail with leaderboard + members + invite code.
- Settings: enable/test web push, admin change-password, logout.
- PWA: manifest + sw.js + icons; VAPID push wired to real backend.
- Verified: testing agent iteration_2 — backend 25/25, all frontend flows pass, no bugs.

## Backlog / Not yet on web
- Tiket schedina submission via OCR screenshot upload (thebesttiket schedina/ocr + confirm) — backend present, web UI pending.
- ScoreAndLive and FantaGiornata screens (backend routers present; Hub marks PROSSIMAMENTE).
- Bonus/Big Match player screens; admin panels (deadlines, players, notifications broadcast, settle-matchday, PDF/Excel ingestion).
- Invites management UI, room admin, kick, history views.

## Implemented — iteration 2 (2026-06)
- Schedina OCR (TheBestTiket): upload foto giocata → POST /rooms/{id}/schedina/ocr (Gemini vision, EMERGENT_LLM_KEY) → anteprima → confirm; view my schedina. (hardened: corrupt image → 400)
- Pannello Admin (/admin, admin-only via Impostazioni): scadenze giornata (PUT /deadlines/{md}), notifiche broadcast (/push/broadcast), import Voti PDF/Excel (/admin/voti/upload-pdf|xlsx), liquidazione giornata (/admin/settle-matchday/state|commit).
- ScoreAndLive ATTIVO (GAMES enabled=true): lista/crea/iscrizione, dettaglio con pick marcatori (select per fixture, /sal/players?team), classifica sopravvivenza.
- Giochi Bonus (/bonus): 4 tab (tiket/survival = risultato esatto; score/fanta = primo marcatore), config Big Match + invio pronostici per iscrizione.
- Verified: testing agent iteration_3 — backend 24/24, tutti i flussi UI, nessun bug (1 minore OCR-500 su immagine corrotta → risolto).

## Notes update
- FantaGiornata resta PROSSIMAMENTE (backend router presente, UI non ancora costruita).
- Regression suites: test_rinomagic_flows.py (25) + test_new_features.py (24).

## Implemented — iteration 3 (2026-06)
- FantaGiornata ATTIVO (GAMES enabled=true): crea lega, iscrizione con codice, **builder formazione** (modulo 3-4-3…5-4-1, 11 titolari 1P + modulo + 8 panchina 2P/2D/2C/2A, listone reale via /sal/players), classifica e vista Formazioni.
- Gestione Inviti (componente riutilizzabile InvitesManager) per admin su Tiket room, Survival, ScoreAndLive e FantaGiornata: genera/copia/revoca codici (GET/POST/DELETE `${base}/invites`).
- Riepilogo Giornata: Survival (`/sv/.../matchdays/{id}/summary`) e ScoreAndLive (`/sal/.../matchdays/{id}/summary`) — conteggi 1/X/2 e pronostici/candidati di tutti i giocatori.
- Gestione Utenti nel Pannello Admin: lista utenti, blocca/sblocca (`/auth/users/{id}/block|unblock`), reset password (`/auth/users/reset-password`).
- Tutti e 4 i giochi ora ATTIVI nell'hub (nessun "prossimamente").
- Verified: testing agent iteration_4 — backend 26/26, tutti i flussi UI, nessun bug funzionale.

## Status
- Tutti i 4 giochi (Tiket, Survival, ScoreAndLive, FantaGiornata) + Bonus attivi sul web.

## Implemented — iteration 4 (2026-06)
- Storico Giornate: ScoreAndLive (`/sal/.../history`), Survival (selettore giornata + `/matchdays/{id}/summary`), FantaGiornata (scheda Punteggi con `/results/{md}`). Tiket: la classifica stanza è il risultato di giornata.
- Punteggi FantaGiornata: scheda "Punteggi" con `total_fantavoto` per membro + breakdown; admin "Calcola punti" (`POST /fg/leagues/{id}/settle`) dai voti caricati.
- Notifiche Mirate: `BroadcastIn.user_ids` + `broadcast_push` filtrato; componente NotifyBox (admin) in Tiket room, Survival, ScoreAndLive e FantaGiornata invia push solo ai partecipanti dell'entità.
- Verified: testing agent iteration_5 — backend 11/11, frontend 100% (1 bug critico entries→participants corretto).
