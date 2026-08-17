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

## Notes
- QA accounts (safe to delete): player `e1_qa_player`, admin `e1qa.admin@gmail.com`, password `Test1234!`.
- Real users' plaintext passwords unknown (bcrypt) — reused untouched.
- Backend regression suite: /app/backend/tests/test_rinomagic_flows.py.
