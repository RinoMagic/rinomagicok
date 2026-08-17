import os
import json
import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from pywebpush import webpush, WebPushException

from database import get_db
from auth import get_current_user, require_admin

VAPID_PUBLIC_KEY = os.environ["VAPID_PUBLIC_KEY"]
VAPID_PRIVATE_KEY = os.environ["VAPID_PRIVATE_KEY"]
VAPID_SUBJECT = os.environ["VAPID_SUBJECT"]

push_router = APIRouter(prefix="/api/push", tags=["push"])


class SubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscription(BaseModel):
    endpoint: str
    keys: SubscriptionKeys
    expirationTime: int | None = None


class NotificationPayload(BaseModel):
    title: str = "Schedina Bar"
    body: str
    url: str = "/"


def _send_one(subscription: dict, message: str):
    return webpush(
        subscription_info={
            "endpoint": subscription["endpoint"],
            "keys": subscription["keys"],
        },
        data=message,
        vapid_private_key=VAPID_PRIVATE_KEY,
        vapid_claims={"sub": VAPID_SUBJECT},
        ttl=60 * 60,
        timeout=10,
    )


async def send_push_to_all(title: str, body: str, url: str = "/"):
    db = get_db()
    message = json.dumps({"title": title, "body": body, "url": url})
    dead = []
    sent = 0
    async for sub in db.push_subscriptions.find({}):
        try:
            await asyncio.to_thread(_send_one, sub, message)
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                dead.append(sub["endpoint"])
        except Exception:
            pass
    if dead:
        await db.push_subscriptions.delete_many({"endpoint": {"$in": dead}})
    return {"sent": sent, "removed": len(dead)}


@push_router.get("/vapid-public-key")
async def get_vapid_public_key():
    return {"publicKey": VAPID_PUBLIC_KEY}


@push_router.post("/subscribe", status_code=201)
async def save_subscription(subscription: PushSubscription, user: dict = Depends(get_current_user)):
    db = get_db()
    doc = subscription.model_dump()
    doc["user_id"] = user["id"]
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.push_subscriptions.update_one(
        {"endpoint": subscription.endpoint},
        {"$set": doc, "$setOnInsert": {"id": str(uuid.uuid4())}},
        upsert=True,
    )
    return {"ok": True}


@push_router.post("/send")
async def send_notification(payload: NotificationPayload, admin: dict = Depends(require_admin)):
    return await send_push_to_all(payload.title, payload.body, payload.url)
