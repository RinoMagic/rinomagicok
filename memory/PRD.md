# RinoMagic (Schedina Bar) — PRD

## Original Problem
Rebuild RinoMagic/RinoMagic as a standard React WEB PWA (NOT Expo/Mobile). Port the original FastAPI backend as-is (same routes/rules, incl. web_push.py) and rebuild the frontend in React web replicating the same look & feel and the games (Tiket, Survival, ScoreAndLive, FantaGiornata) + Bonus/Big Match. Connect to existing MongoDB Atlas `schedinabar` in read/write WITHOUT touching existing data (1479 sal_calendar incl. 380 for 2026-27, 497 sal_players, 9 real users). PWA installable, VAPID web push. Never show Expo/QR.

## Fix — Classifica Survival + dettaglio giocata (come originale) (2026-06)
- Ripristinata la riga classifica Survival identica all'originale: `#rank`, nickname, stato ("Giocata inserita"/"In attesa di giocata"/"Eliminato · G{n}") e colonna badge — **cuore grigio** `pick_lives` (vite da pronostici), **regalo** `+bonus_wins`, **cuore rosso** `lives_left` (vite totali). Riga cliccabile.
- `SurvivalDetail.js` ora carica la classifica da `/sv/tournaments/{id}/leaderboard` (campi rank/pick_lives/bonus_wins/lives_left/has_submitted_current/eliminated).
- Nuovo `components/SurvivaPicksModal.js` (porting web del modal Expo): al click su un giocatore apre `/sv/tournaments/{id}/participants/{uid}/picks`, mostra le giocate per giornata con 🎁 se big match bonus vinto, badge stato (Calcolata/Chiusa/Aperta), scelte con segno colorato (verde ok / rosso HeartCrack ko / clock na) o "nascosti". Vista sintetizzata: mostra solo giornate liquidate/chiuse/con giocate.
- Verified via screenshot: classifica badge OK; modal G1 con 3 pick verdi + 🎁, G2 "Chiusa · Nessuna scelta". Backend non modificato.


Recuperato il repo originale RinoMagic/RinoMagic (backend Python IDENTICO al nostro → problemi tutti frontend). Corretti 4 punti:
1. **Calcolo Betting** (`TiketRoom.js`): la classifica leggeva `board.entries` invece di `board.leaderboard` → mostrava sempre "in attesa". Ora mostra le righe con punteggio/trofeo/birra.
2. **Riepilogo giornata** (`SurvivalDetail.js`, `ScoreAndLiveDetail.js`): layout ripulito come l'originale — intestazione "Riassunto Giornata", avviso privacy/stato, card per partita (conteggi 1/X/2 o candidati marcatore + chip nickname).
3. **Giocate singole dalla classifica** (`TiketRoom.js`): ogni riga classifica è cliccabile ed espande la schedina del giocatore (breakdown) con esito verde/rosso 'PERSA'/grigio 'RINV.'.
4. **Sezione bonus G1** (`Bonus.js`): il bonus liquidato spariva (`/bonus/available` filtra `settled_at=None`). Aggiunte sezioni "Pronostici Giornata" (`/bonus/current-locked-picks`) e "Storico bonus" (`/bonus/history/full`) che mostrano tutte le giocate con chi ha vinto/perso.
Verified: testing agent iteration_11 frontend 5/5 (100%). Backend non modificato.


## Feature — Archivia Torneo/Stanza/Lega (2026-06)
- L'admin può ARCHIVIARE (invece di eliminare) un elemento CONCLUSO: lo storico resta consultabile ma esce dalla lista attiva → sezione collassabile "Archiviati" (Ripristina + Elimina). Solo admin.
- Backend: campo `archived` nei dict + endpoint `POST /archive?archived=bool` per ogni gioco. Gate "concluso": Survival/SAL `status=='finished'`, Tiket `status=='settled'`, Fanta `current_matchday!=null`. Unarchive sempre consentito. (`surviva.py`, `scoreandlive.py`, `thebesttiket.py`, `fantagiornata.py`).
- Frontend (Survival/ScoreAndLive/Tiket/FantaGiornata): split active vs archived su campo `archived`; pulsante Archivia (`{sv|sal|tk|fg}-archive-{id}`) solo su elementi conclusi; toggle `*-archived-toggle`, Ripristina `*-unarchive-{id}`. Survival carica `/sv/tournaments?include_finished=true`.
- Verified: testing agent iteration_10 backend 8/8 + frontend 7/7, permessi player OK, click non naviga (stopPropagation), 0 dati produzione toccati. Regressione: /app/backend/tests/test_archive_buttons.py.


## Feature — Elimina Tornei/Stanze/Leghe (2026-06)
- Pulsante Elimina (icona cestino rosso, **solo admin**) su ogni card nelle 4 liste: Survival (`sv-delete-{id}` → DELETE /sv/tournaments/{id}), ScoreAndLive (`sal-delete-{id}` → DELETE /sal/tournaments/{id}?force, con doppia conferma se ci sono giocate storiche/409), Tiket stanze (`tk-delete-{id}` → DELETE /rooms/{id}), FantaGiornata leghe (`fg-delete-{id}` → DELETE /fg/leagues/{id}).
- Card convertite da `<button>` a `<div>` con button interno per navigazione + button cestino separato (stopPropagation). Endpoint delete già esistenti nel backend (cascade). Verified: testing agent iteration_9 backend 8/8, frontend 100%, permessi player OK, regressione navigazione OK. Test regressione riutilizzabile: /app/backend/tests/test_delete_buttons.py.


## Implemented — iteration 8 (2026-06) — Ripristino STRUMENTI ADMIN
- Pannello Admin ricostruito come menu a card "STRUMENTI ADMIN" (come l'originale) con 9 strumenti in `/app/frontend/src/pages/Admin.js` (stato `tool`, componenti in `/app/frontend/src/components/admin/`):
  1. **Calcola Giornata** — upload Excel voti (`/admin/voti/upload-xlsx` dry_run→commit), anteprima risultati modificabile + rinvii, conferma → `POST /admin/settle-matchday/preview|commit` (liquida tutti i giochi in un colpo, con `fixture_overrides`, `postponed_matches`, primo marcatore).
  2. **Escludi Partite** — `GET /sal/calendar` + `PATCH /sal/calendar/fixture/{id}/exclude` (+ delete rinvii).
  3. **Calendario Serie A** — upload PDF (`/sal/calendar/import-pdf`) o **Excel (nuovo** `/sal/calendar/import-xlsx` + `parse_calendar_xlsx` in excel_parser.py: colonne Giornata/Casa/Trasferta/Data), anteprima+conferma, inserimento/eliminazione manuale.
  4. **Deadline Giornate** — `PUT /deadlines/{md}?season=` con datetime picker (calendario+orologio) + lista scadenze.
  5. **Lista Calciatori** — upload Listone PDF/XLSX (`/sal/players/import-pdf|import-xlsx`) con anteprima+conferma.
  6. **Gestione Giochi Bonus** — `GET/POST /bonus/configs`, Big Match da dropdown calendario, settle exact/scorer, delete.
  7. **Gestione Admin** — `POST /auth/admin/promote`, `DELETE /auth/users/{id}`.
  8. **Notifiche** — broadcast + promemoria automatici.
  9. **Gestione Utenti** — blocca/sblocca/reset/elimina.
- **Banner Bonus** (`/app/frontend/src/components/BonusBanner.js`) in Survival/Tiket/ScoreAndLive/FantaGiornata: se idoneo e bonus attivo non giocato mostra CTA verde → `/bonus`.
- Costante stagione centralizzata: `/app/frontend/src/lib/constants.js` (SEASON=2026-27).
- Verified: testing agent iteration_8 — backend 10/10, tutti e 9 gli strumenti raggiungibili e dati caricati; banner su 3/4 giochi (survival non idoneo per il player QA = corretto). Nessun bug funzionale (solo warning dev innocuo su `<span>` in `<option>`).

## Login bugfix (2026-06)
- Corretto bug puntini password invisibili (`bg-white/8` renderizzato bianco pieno → testo bianco su bianco): valori `rgba()` espliciti + safeguard `-webkit-autofill`. Aggiunto toggle mostra/nascondi password (icona occhio).


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

## Implemented — iteration 6 (2026-06)
- Notifiche Personalizzate: l'admin sceglie gli intervalli di promemoria (24h/12h/6h/3h/1h/30min) via GET/PUT `/api/settings/reminders` (validati 5–10080 min, dedupe, sort desc, salvati in `app_settings`); la loop auto-notify invia un promemoria per ogni offset e traccia `reminded_offsets` per giornata. UI: card "Promemoria automatici" nel Pannello Admin con chip toggle + salva.
- Verified: testing agent iteration_7 — backend 100%, frontend 100%, nessun bug.

## Implemented — iteration 5 (2026-06)
- Import Voti Guidato: upload PDF/Excel in `dry_run` → anteprima tabellare giocatore→voto (rows aggiunte alla risposta dry_run) → "Conferma e salva" (`dry_run=false&replace=true`).
- Notifiche Automatiche: task asyncio di startup che scandisce `matchday_deadlines` → broadcast "Nuova giornata aperta" per scadenze future non ancora notificate e promemoria "Ultimi minuti" entro 60' (le scadenze passate vengono solo marcate, niente spam).
- Esporta Storico PDF: modulo `exports.py` (`POST /api/export/pdf`, reportlab, admin) + helper frontend `apiDownload`; pulsanti in Survival, ScoreAndLive e FantaGiornata scaricano riepilogo + classifica di giornata.
- Verified: testing agent iteration_6 — backend 18/18, frontend ~95% (nessun bug; unico limite di test su URL FG errato, endpoint export verificato 200 application/pdf).
- Storico Giornate: ScoreAndLive (`/sal/.../history`), Survival (selettore giornata + `/matchdays/{id}/summary`), FantaGiornata (scheda Punteggi con `/results/{md}`). Tiket: la classifica stanza è il risultato di giornata.
- Punteggi FantaGiornata: scheda "Punteggi" con `total_fantavoto` per membro + breakdown; admin "Calcola punti" (`POST /fg/leagues/{id}/settle`) dai voti caricati.
- Notifiche Mirate: `BroadcastIn.user_ids` + `broadcast_push` filtrato; componente NotifyBox (admin) in Tiket room, Survival, ScoreAndLive e FantaGiornata invia push solo ai partecipanti dell'entità.
- Verified: testing agent iteration_5 — backend 11/11, frontend 100% (1 bug critico entries→participants corretto).
