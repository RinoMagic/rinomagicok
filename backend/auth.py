"""Authentication module for SchedinaBar.

Two user roles:
    admin  - identified by email + password. Can reset own password via email.
             Can promote other admins, reset player passwords, block/delete
             players, manage rooms.
    player - identified by username + password (no email). If they forget the
             password they ask an admin to reset it.

All authenticated endpoints receive a `current_user` dependency that returns
the user document. Additional dependencies enforce role checks.
"""
import os
import re
import uuid
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import bcrypt
from fastapi import HTTPException, Depends, APIRouter
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field, field_validator
from motor.motor_asyncio import AsyncIOMotorDatabase

from email_service import send_email, build_reset_email_html

logger = logging.getLogger("schedinabar.auth")

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_DAYS = int(os.environ.get("ACCESS_TOKEN_DAYS", "7"))
APP_BASE_URL = os.environ.get(
    "APP_BASE_URL", "https://fantasy-calcio-15.preview.emergentagent.com"
).rstrip("/")

security = HTTPBearer(auto_error=False)

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{2,20}$")


# ============ Models ============
class AdminLoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class AdminForgotPasswordIn(BaseModel):
    email: EmailStr


class AdminResetPasswordIn(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=64)


class AdminChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=64)


class AdminPromoteIn(BaseModel):
    email: EmailStr
    temp_password: str = Field(min_length=8, max_length=64)


class PlayerRegisterIn(BaseModel):
    username: str
    password: str = Field(min_length=6, max_length=64)

    @field_validator("username")
    @classmethod
    def _norm_username(cls, v: str) -> str:
        v = v.strip()
        if not USERNAME_RE.match(v):
            raise ValueError("Username: 2-20 caratteri (lettere, numeri, . _ -)")
        return v


class PlayerLoginIn(BaseModel):
    username: str
    password: str


class ResetPlayerPasswordIn(BaseModel):
    user_id: str
    new_password: str = Field(min_length=6, max_length=64)


# ============ Helpers ============
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()


def check_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_user_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _public_user(u: dict) -> dict:
    """Return a user dict safe to expose to the frontend."""
    return {
        "id": u["id"],
        "role": u["role"],
        "username": u.get("username"),
        "email": u.get("email"),
        "blocked": u.get("blocked", False),
        "must_change_password": u.get("must_change_password", False),
        "created_at": u.get("created_at"),
    }


def _login_response(user: dict) -> dict:
    return {"token": create_user_token(user["id"], user["role"]), "user": _public_user(user)}


# ============ Router factory ============
def build_auth_router(db: AsyncIOMotorDatabase) -> APIRouter:
    """Create the /api/auth router bound to the given Motor database."""
    router = APIRouter(prefix="/auth", tags=["auth"])

    async def _get_user_by_id(user_id: str) -> Optional[dict]:
        return await db.users.find_one({"id": user_id}, {"_id": 0})

    async def _current_user_common(
        cred: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ) -> dict:
        if not cred:
            raise HTTPException(status_code=401, detail="Non autenticato")
        try:
            payload = jwt.decode(cred.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except jwt.PyJWTError:
            raise HTTPException(status_code=401, detail="Sessione non valida")
        user = await _get_user_by_id(payload.get("sub", ""))
        if not user:
            raise HTTPException(status_code=401, detail="Utente non trovato")
        if user.get("blocked"):
            raise HTTPException(status_code=403, detail="Account bloccato dall'admin")
        return user

    # ---------- ADMIN ROUTES ----------
    @router.post("/admin/login")
    async def admin_login(data: AdminLoginIn):
        email = data.email.lower().strip()
        user = await db.users.find_one({"email": email, "role": "admin"}, {"_id": 0})
        if not user or not check_password(data.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Credenziali non valide")
        if user.get("blocked"):
            raise HTTPException(status_code=403, detail="Account bloccato")
        return _login_response(user)

    @router.post("/admin/forgot-password")
    async def admin_forgot_password(data: AdminForgotPasswordIn):
        email = data.email.lower().strip()
        user = await db.users.find_one({"email": email, "role": "admin"}, {"_id": 0})
        # Always respond 200 to avoid disclosing which emails are registered
        if user:
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(hours=1)
            await db.reset_tokens.insert_one({
                "token": token,
                "user_id": user["id"],
                "expires_at": expires.isoformat(),
                "used": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            reset_url = f"{APP_BASE_URL}/reset-password?token={token}"
            await send_email(
                email,
                "RinoMagic — Reset password",
                build_reset_email_html(reset_url, expires_minutes=60),
            )
        return {"ok": True, "message": "Se l'email è registrata, riceverai le istruzioni per il reset."}

    @router.post("/admin/reset-password")
    async def admin_reset_password(data: AdminResetPasswordIn):
        rt = await db.reset_tokens.find_one({"token": data.token}, {"_id": 0})
        if not rt or rt.get("used"):
            raise HTTPException(status_code=400, detail="Token non valido o già usato")
        expires_at = datetime.fromisoformat(rt["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=400, detail="Token scaduto")
        await db.users.update_one(
            {"id": rt["user_id"]},
            {"$set": {
                "password_hash": hash_password(data.new_password),
                "must_change_password": False,
            }},
        )
        await db.reset_tokens.update_one({"token": data.token}, {"$set": {"used": True}})
        return {"ok": True, "message": "Password aggiornata. Accedi con la nuova password."}

    @router.post("/admin/change-password")
    async def admin_change_password(
        data: AdminChangePasswordIn,
        user: dict = Depends(_current_user_common),
    ):
        if user["role"] != "admin":
            raise HTTPException(status_code=403, detail="Solo admin")
        if not check_password(data.old_password, user["password_hash"]):
            raise HTTPException(status_code=400, detail="Password attuale errata")
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {
                "password_hash": hash_password(data.new_password),
                "must_change_password": False,
            }},
        )
        return {"ok": True}

    async def _enroll_admin_everywhere(admin_user: dict) -> dict:
        """When a user becomes an admin, auto-enroll them into every open
        room/tournament/league across all games. Idempotent — existing
        enrollments are preserved. Returns counters for reporting.
        """
        now = datetime.now(timezone.utc).isoformat()
        uid = admin_user["id"]
        # Use username OR email as display name
        nickname = (admin_user.get("username")
                    or (admin_user.get("email") or "").split("@")[0]
                    or "admin")
        counters = {"tiket": 0, "surviva": 0, "scoreandlive": 0, "fantagiornata": 0}
        # TheBestTiket rooms
        async for r in db.rooms.find(
            {"status": {"$ne": "closed"}}, {"_id": 0, "id": 1},
        ):
            if not await db.memberships.find_one({"room_id": r["id"], "user_id": uid}):
                # Assign next available slot (unique per room)
                existing_slots = [m.get("slot", 0) async for m in db.memberships.find(
                    {"room_id": r["id"]}, {"_id": 0, "slot": 1},
                )]
                next_slot = (max(existing_slots) if existing_slots else 0) + 1
                await db.memberships.insert_one({
                    "id": str(uuid.uuid4()),
                    "room_id": r["id"], "user_id": uid,
                    "slot": next_slot, "display_name": nickname, "joined_at": now,
                })
                counters["tiket"] += 1
        # Survival tournaments
        async for t in db.sv_tournaments.find(
            {"status": {"$ne": "finished"}},
            {"_id": 0, "id": 1, "initial_lives": 1},
        ):
            if not await db.sv_participants.find_one({
                "tournament_id": t["id"], "user_id": uid,
            }):
                await db.sv_participants.insert_one({
                    "tournament_id": t["id"], "user_id": uid,
                    "nickname": nickname,
                    "lives_left": t.get("initial_lives", 10),
                    "locked_teams": [], "blocked_signs": [],
                    "eliminated_at": None, "joined_at": now,
                })
                counters["surviva"] += 1
        # ScoreAndLive tournaments
        async for t in db.sal_tournaments.find(
            {"status": {"$ne": "finished"}},
            {"_id": 0, "id": 1, "initial_lives": 1},
        ):
            if not await db.sal_participants.find_one({
                "tournament_id": t["id"], "user_id": uid,
            }):
                await db.sal_participants.insert_one({
                    "tournament_id": t["id"], "user_id": uid,
                    "nickname": nickname,
                    "lives_remaining": t.get("initial_lives", 10),
                    "eliminated_at_matchday": None, "joined_at": now,
                })
                counters["scoreandlive"] += 1
        # FantaGiornata leagues
        async for lg in db.fg_leagues.find(
            {"status": {"$ne": "closed"}}, {"_id": 0, "id": 1},
        ):
            if not await db.fg_memberships.find_one({
                "league_id": lg["id"], "user_id": uid,
            }):
                await db.fg_memberships.insert_one({
                    "league_id": lg["id"], "user_id": uid,
                    "nickname": nickname, "joined_at": now,
                })
                counters["fantagiornata"] += 1
        return counters

    @router.post("/admin/promote")
    async def admin_promote(
        data: AdminPromoteIn,
        user: dict = Depends(_current_user_common),
    ):
        if user["role"] != "admin":
            raise HTTPException(status_code=403, detail="Solo admin")
        email = data.email.lower().strip()
        existing = await db.users.find_one({"email": email}, {"_id": 0})
        if existing:
            if existing["role"] == "admin":
                raise HTTPException(status_code=400, detail="Utente già admin")
            # Promote existing player to admin (keeps their username)
            await db.users.update_one(
                {"id": existing["id"]},
                {"$set": {"role": "admin", "email": email,
                          "password_hash": hash_password(data.temp_password),
                          "must_change_password": True}},
            )
            promoted = await db.users.find_one({"id": existing["id"]}, {"_id": 0})
            enrolled = await _enroll_admin_everywhere(promoted)
            return {"ok": True, "user_id": existing["id"], "enrolled": enrolled}
        # Create brand-new admin account
        new_user = {
            "id": str(uuid.uuid4()),
            "role": "admin",
            "email": email,
            "password_hash": hash_password(data.temp_password),
            "blocked": False,
            "must_change_password": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.users.insert_one(new_user)
        enrolled = await _enroll_admin_everywhere(new_user)
        return {"ok": True, "user_id": new_user["id"], "enrolled": enrolled}

    # ---------- PLAYER ROUTES ----------
    @router.post("/player/register")
    async def player_register(data: PlayerRegisterIn):
        existing = await db.users.find_one({"username": data.username}, {"_id": 0})
        if existing:
            raise HTTPException(status_code=400, detail="Username già usato")
        # NOTE: We intentionally OMIT the `email` field (instead of setting it to None)
        # so the unique+sparse index on `email` skips this document. Storing
        # `email: null` on multiple player docs would violate the unique constraint
        # on some MongoDB versions.
        new_user = {
            "id": str(uuid.uuid4()),
            "role": "player",
            "username": data.username,
            "password_hash": hash_password(data.password),
            "blocked": False,
            "must_change_password": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.users.insert_one(new_user)
        return _login_response(new_user)

    @router.post("/player/login")
    async def player_login(data: PlayerLoginIn):
        user = await db.users.find_one({"username": data.username, "role": "player"}, {"_id": 0})
        if not user or not check_password(data.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Credenziali non valide")
        if user.get("blocked"):
            raise HTTPException(status_code=403, detail="Account bloccato dall'admin")
        return _login_response(user)

    # ---------- SESSION ----------
    @router.get("/me")
    async def me(user: dict = Depends(_current_user_common)):
        return _public_user(user)

    # ---------- ADMIN — GESTIONE UTENTI ----------
    @router.get("/users")
    async def list_users(user: dict = Depends(_current_user_common)):
        if user["role"] != "admin":
            raise HTTPException(status_code=403, detail="Solo admin")
        docs = await db.users.find({}, {"_id": 0, "password_hash": 0}).to_list(length=500)
        return docs

    @router.post("/users/{user_id}/block")
    async def block_user(user_id: str, user: dict = Depends(_current_user_common)):
        if user["role"] != "admin":
            raise HTTPException(status_code=403, detail="Solo admin")
        if user_id == user["id"]:
            raise HTTPException(status_code=400, detail="Non puoi bloccare te stesso")
        result = await db.users.update_one({"id": user_id}, {"$set": {"blocked": True}})
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Utente non trovato")
        return {"ok": True}

    @router.post("/users/{user_id}/unblock")
    async def unblock_user(user_id: str, user: dict = Depends(_current_user_common)):
        if user["role"] != "admin":
            raise HTTPException(status_code=403, detail="Solo admin")
        result = await db.users.update_one({"id": user_id}, {"$set": {"blocked": False}})
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Utente non trovato")
        return {"ok": True}

    @router.delete("/users/{user_id}")
    async def delete_user(user_id: str, user: dict = Depends(_current_user_common)):
        if user["role"] != "admin":
            raise HTTPException(status_code=403, detail="Solo admin")
        if user_id == user["id"]:
            raise HTTPException(status_code=400, detail="Non puoi eliminare te stesso")
        target = await db.users.find_one({"id": user_id}, {"_id": 0})
        if not target:
            raise HTTPException(status_code=404, detail="Utente non trovato")
        # Cannot delete the last admin (must always be at least one).
        if target.get("role") == "admin":
            admin_count = await db.users.count_documents({"role": "admin"})
            if admin_count <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Impossibile eliminare l'unico admin rimasto",
                )
        # Cascade delete: remove memberships & schedine (TheBestTiket)
        await db.memberships.delete_many({"user_id": user_id})
        await db.schedine.delete_many({"user_id": user_id})
        # Survival
        await db.sv_participants.delete_many({"user_id": user_id})
        await db.sv_picks.delete_many({"user_id": user_id})
        # ScoreAndLive
        await db.sal_participants.delete_many({"user_id": user_id})
        await db.sal_picks.delete_many({"user_id": user_id})
        # FantaGiornata
        await db.fg_memberships.delete_many({"user_id": user_id})
        await db.fg_lineups.delete_many({"user_id": user_id})
        await db.fg_matchday_results.delete_many({"user_id": user_id})
        # Bonus
        await db.bonus_picks.delete_many({"user_id": user_id})
        await db.users.delete_one({"id": user_id})
        return {"ok": True}

    @router.post("/users/reset-password")
    async def admin_reset_player_password(
        data: ResetPlayerPasswordIn,
        user: dict = Depends(_current_user_common),
    ):
        if user["role"] != "admin":
            raise HTTPException(status_code=403, detail="Solo admin")
        target = await db.users.find_one({"id": data.user_id}, {"_id": 0})
        if not target:
            raise HTTPException(status_code=404, detail="Utente non trovato")
        await db.users.update_one(
            {"id": data.user_id},
            {"$set": {
                "password_hash": hash_password(data.new_password),
                "must_change_password": True,
            }},
        )
        return {"ok": True}

    return router, _current_user_common


# ============ Seed admin ============
async def seed_admin_if_missing(db: AsyncIOMotorDatabase) -> None:
    seed_email = os.environ.get("ADMIN_SEED_EMAIL", "").lower().strip()
    seed_pw = os.environ.get("ADMIN_SEED_PASSWORD", "").strip()
    if not seed_email or not seed_pw:
        return
    exists = await db.users.find_one({"email": seed_email}, {"_id": 0})
    if exists:
        return
    admin = {
        "id": str(uuid.uuid4()),
        "role": "admin",
        "email": seed_email,
        "password_hash": hash_password(seed_pw),
        "blocked": False,
        "must_change_password": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(admin)
    logger.info("Seeded admin account %s (must change password on first login)", seed_email)
