from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query

from mini.deps import current_store, get_database
from services.today_aggregator import TodayAggregator

router = APIRouter()


@router.get("/today")
def today(
    business_date: str | None = Query(default=None, alias="date"),
    authorization: str | None = Header(default=None),
):
    store = current_store(authorization)
    day = business_date or date.today().isoformat()
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc
    timezone = store.get("timezone") or "Asia/Shanghai"
    try:
        now = datetime.now(ZoneInfo(timezone))
    except Exception:
        now = datetime.now().astimezone()
    return TodayAggregator(get_database()).build(int(store["id"]), day, now)
