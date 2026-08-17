# Schedina Bar — PRD

## Original Problem
Rebuild the RinoMagic "Schedina Bar" app as a standard React WEB PWA (NOT Expo/Mobile), reusing the existing FastAPI logic and the existing MongoDB Atlas DB (`schedinabar`). Replicate the 4 games (Tiket, Survival, ScoreAndLive, FantaGiornata) + Bonus/Big Match. Installable PWA with manifest + service worker for Web Push (VAPID). Italian UI. Do not modify existing Atlas data (1479 calendar matches, 497 players, real users).

## Architecture
- Frontend: React 19 (CRACO) PWA — manifest.json, sw.js, app icons. Tailwind + shadcn. Dark "Performance Pro" theme (Bebas Neue + Manrope). Bearer-token auth (localStorage `sb_token`).
- Backend: FastAPI, all routes under `/api`. Modules: `server.py` (games), `auth.py` (JWT + bcrypt login by username/email), `web_push.py` (VAPID push), `database.py` (Motor client).
- DB: MongoDB Atlas `schedinabar`. Season used: `2026-27` (380 matches, 20 teams, 38 matchdays).

## User Personas
- Player (7 real + QA): plays Tiket/Survival, submits predictions, views standings.
- Admin (2 real + QA): creates Tiket rounds & Survival tournaments, enters results, sends push.

## Implemented (2026-06)
- Auth: login by username OR email + bcrypt against existing `password_hash`; `/api/auth/me`; role-based admin guard.
- Serie A reference: matchdays, teams, calendar by matchday, players (filter role/team/search + pagination).
- Tiket (complete): admin create round (matchday + deadline + Big Match), player submit schedina (1/X/2, all fixtures required), admin set results → scoring (normal=1, big=2, big+bonus=3), general standings.
- Survival (complete): admin create tournament, join, pick a team per matchday (no reuse), admin resolve (winners survive / others + non-pickers eliminated), matchday advance, participants list.
- ScoreAndLive & FantaGiornata: shown on Home with "Prossimamente" badge (inactive).
- PWA + Web Push: manifest, service worker, VAPID public key endpoint, subscribe (auth), admin send; push fired on round/tournament creation & result events.
- Pages: Login, Home, Tiket, Survival, Calendario, Giocatori, Profilo. Italian UI, bottom nav.
- Verified: testing agent iteration_1 — backend 19/19, all frontend flows pass.

## Backlog
- P1: ScoreAndLive game (uses sal_* collections).
- P1: FantaGiornata game (fg_* collections: leagues, lineups, results).
- P2: Tiket private rooms/invites; per-matchday standings; deadline countdown UI.
- P2: Survival invites/private tournaments; auto-resolve from live results.
- P2: Admin dashboard for user management & push composer.

## Notes
- QA accounts (safe to delete): `e1_qa_player` / `e1_qa_admin`, password `Test1234!`.
- Real users' plaintext passwords unknown (bcrypt only) — reused as-is, untouched.
- Backend regression suite: `/app/backend/tests/test_backend.py`.
