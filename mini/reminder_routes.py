from __future__ import annotations

import hmac
import os
import time

from fastapi import APIRouter, Header, HTTPException

from mini.deps import get_database
from notifications.wechat import WechatSubscriptionProvider
from services.reminder_service import ReminderService

router = APIRouter()


def _authorize_cron(authorization: str | None) -> None:
    secret = os.getenv("CRON_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=401, detail="Unauthorized")
    expected = f"Bearer {secret}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/internal/reminders/dispatch")
def dispatch_reminders(authorization: str | None = Header(default=None)):
    _authorize_cron(authorization)
    now = int(time.time() * 1000)
    results = ReminderService(get_database(), WechatSubscriptionProvider()).dispatch_due(now)
    counts = {"SENT": 0, "FAILED": 0, "SKIPPED": 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return {"processed": len(results), "counts": counts, "now": now}
