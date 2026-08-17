"""Web Push Notifications module for PWA.

Uses VAPID keys (self-generated) to send push messages via the browser's
Push service (FCM for Chrome/Edge/Firefox on Android/Desktop, Apple Push
for iOS/macOS Safari when the PWA is installed to home screen).

Storage:
  * ``push_subscriptions`` collection:
    {
      id: uuid,
      user_id: str,
      endpoint: str,          # unique per browser install
      keys: {p256dh, auth},
      user_agent: Optional[str],
      created_at: iso,
      last_used_at: iso,
    }

Endpoints (all under ``/api/push`` prefix registered in server.py):
  * GET  /api/push/vapid-public-key           -> {publicKey}
  * POST /api/push/subscribe    (auth)         -> save/update subscription
  * POST /api/push/unsubscribe  (auth)         -> remove subscription
  * POST /api/push/test         (auth)         -> send a test push to caller
  * POST /api/push/broadcast    (admin)        -> send to ALL subscriptions
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from pywebpush import WebPushException, webpush


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class SubscribeIn(BaseModel):
    endpoint: str
    keys: PushKeys
    user_agent: Optional[str] = None


class UnsubscribeIn(BaseModel):
    endpoint: str


class BroadcastIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    body: str = Field(..., min_length=1, max_length=300)
    url: Optional[str] = Field(default=None, max_length=500)
    icon: Optional[str] = Field(default=None, max_length=500)


def _vapid_claims() -> Dict[str, str]:
    return {"sub": os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")}


def _vapid_private_key() -> str:
    """Return the VAPID private key in the format ``pywebpush`` accepts.

    ``pywebpush`` accepts either a raw base64url-encoded 32-byte private key
    OR a PEM string. We ship the raw base64url form in ``.env`` since it's
    shorter and less error-prone across deploys.
    """
    key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    if not key:
        raise RuntimeError("VAPID_PRIVATE_KEY not set in environment")
    return key


async def send_push(
    subscription: dict,
    payload: dict,
    ttl: int = 60 * 60 * 24,
) -> Optional[str]:
    """Send a single push. Returns None on success, error string on failure."""
    try:
        webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                "keys": subscription["keys"],
            },
            data=json.dumps(payload),
            vapid_private_key=_vapid_private_key(),
            vapid_claims=_vapid_claims(),
            ttl=ttl,
        )
        return None
    except WebPushException as exc:  # noqa: BLE001
        # 404/410 → subscription is dead (user removed permission or
        # uninstalled). Signal to caller so it can be evicted.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            return "expired"
        logger.warning("WebPush failed: status=%s err=%s", status, exc)
        return f"error:{status or 'unknown'}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("WebPush unexpected error")
        return f"exception:{exc}"


async def broadcast_push(
    db,
    payload: dict,
    user_ids: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Broadcast a push to every stored subscription (optionally filtered by
    ``user_ids``). Automatically evicts subscriptions that came back as
    ``expired``.

    Returns a summary dict:  {sent, expired_removed, failed, total_targeted}.
    """
    query: Dict = {}
    if user_ids is not None:
        query["user_id"] = {"$in": list(user_ids)}
    subs = [s async for s in db.push_subscriptions.find(query, {"_id": 0})]

    sent, expired_removed, failed = 0, 0, 0
    for sub in subs:
        err = await send_push(sub, payload)
        if err is None:
            sent += 1
            await db.push_subscriptions.update_one(
                {"endpoint": sub["endpoint"]},
                {"$set": {"last_used_at": _now_iso()}},
            )
        elif err == "expired":
            expired_removed += 1
            await db.push_subscriptions.delete_one({"endpoint": sub["endpoint"]})
        else:
            failed += 1
    return {
        "sent": sent,
        "expired_removed": expired_removed,
        "failed": failed,
        "total_targeted": len(subs),
    }


def create_router(db, current_user, current_admin) -> APIRouter:
    router = APIRouter(prefix="/push", tags=["push"])

    @router.get("/vapid-public-key")
    async def vapid_public_key():
        key = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
        if not key:
            raise HTTPException(status_code=500, detail="VAPID public key not configured")
        return {"publicKey": key}

    @router.post("/subscribe")
    async def subscribe(body: SubscribeIn, user: dict = Depends(current_user)):
        now = _now_iso()
        doc = {
            "user_id": user["id"],
            "endpoint": body.endpoint,
            "keys": {"p256dh": body.keys.p256dh, "auth": body.keys.auth},
            "user_agent": body.user_agent,
            "last_used_at": now,
        }
        existing = await db.push_subscriptions.find_one({"endpoint": body.endpoint})
        if existing:
            await db.push_subscriptions.update_one(
                {"endpoint": body.endpoint}, {"$set": doc},
            )
        else:
            doc["id"] = str(uuid.uuid4())
            doc["created_at"] = now
            await db.push_subscriptions.insert_one(doc)
        return {"ok": True}

    @router.post("/unsubscribe")
    async def unsubscribe(body: UnsubscribeIn, user: dict = Depends(current_user)):
        res = await db.push_subscriptions.delete_one({
            "endpoint": body.endpoint, "user_id": user["id"],
        })
        return {"ok": True, "removed": res.deleted_count}

    @router.post("/test")
    async def test_push(user: dict = Depends(current_user)):
        summary = await broadcast_push(
            db,
            payload={
                "title": "🍺 RinoMagic",
                "body": f"Test riuscito, {user.get('username') or user.get('email')}! Le notifiche sono attive.",
                "url": "/hub",
            },
            user_ids=[user["id"]],
        )
        if summary["total_targeted"] == 0:
            raise HTTPException(
                status_code=404,
                detail="Nessuna subscription trovata per l'utente. Attiva le notifiche prima.",
            )
        return summary

    @router.post("/broadcast")
    async def broadcast(body: BroadcastIn, user: dict = Depends(current_admin)):
        summary = await broadcast_push(
            db,
            payload={
                "title": body.title,
                "body": body.body,
                "url": body.url or "/hub",
                "icon": body.icon,
            },
        )
        # Log admin action
        await db.admin_actions.insert_one({
            "id": str(uuid.uuid4()),
            "action": "push_broadcast",
            "actor_id": user["id"],
            "actor_email": user.get("email"),
            "meta": {
                "title": body.title,
                "body": body.body,
                "url": body.url,
                **summary,
            },
            "at": _now_iso(),
        })
        return summary

    @router.get("/stats")
    async def push_stats(user: dict = Depends(current_admin)):
        total = await db.push_subscriptions.count_documents({})
        # Unique user ids
        pipeline = [{"$group": {"_id": "$user_id"}}]
        distinct_users = 0
        async for _ in db.push_subscriptions.aggregate(pipeline):
            distinct_users += 1
        return {"subscriptions_total": total, "distinct_users": distinct_users}

    return router


__all__ = ["create_router", "broadcast_push", "send_push"]
