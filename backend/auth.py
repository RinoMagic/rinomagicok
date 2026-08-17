import os
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, Header
from typing import Optional

from database import get_db

JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")


def get_jwt_secret() -> str:
    return os.environ["JWT_SECRET"]


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=30),
        "type": "access",
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def _public_user(user: dict) -> dict:
    return {
        "id": user.get("id"),
        "role": user.get("role", "player"),
        "email": user.get("email"),
        "username": user.get("username"),
        "nickname": user.get("nickname") or user.get("username") or user.get("email"),
        "must_change_password": user.get("must_change_password", False),
        "blocked": user.get("blocked", False),
    }


async def authenticate(identifier: str, password: str) -> dict:
    db = get_db()
    ident = (identifier or "").strip()
    ident_lower = ident.lower()
    user = await db.users.find_one(
        {
            "$or": [
                {"username": ident},
                {"email": ident_lower},
                {"email": ident},
                {"username": ident_lower},
            ]
        }
    )
    if not user:
        raise HTTPException(status_code=401, detail="Credenziali non valide")
    if user.get("blocked"):
        raise HTTPException(status_code=403, detail="Account bloccato")
    pwd_hash = user.get("password_hash")
    if not pwd_hash or not verify_password(password, pwd_hash):
        raise HTTPException(status_code=401, detail="Credenziali non valide")
    token = create_access_token(user["id"], user.get("role", "player"))
    return {"token": token, "user": _public_user(user)}


async def get_current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Non autenticato")
    token = authorization[7:]
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Token non valido")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Sessione scaduta")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token non valido")
    db = get_db()
    user = await db.users.find_one({"id": payload["sub"]})
    if not user:
        raise HTTPException(status_code=401, detail="Utente non trovato")
    if user.get("blocked"):
        raise HTTPException(status_code=403, detail="Account bloccato")
    return _public_user(user)


async def require_admin(authorization: Optional[str] = Header(default=None)) -> dict:
    user = await get_current_user(authorization)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Accesso riservato agli amministratori")
    return user
