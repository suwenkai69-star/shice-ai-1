from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Header, HTTPException, Query

from mini.deps import current_store, get_database
from services.trend_service import TrendService

router = APIRouter()


@router.get("/trends")
def trends(
    days: int = Query(default=7),
    end_date: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
):
    store = current_store(authorization)
    day = end_date or date.today().isoformat()
    try:
        date.fromisoformat(day)
        trend = TrendService(get_database()).get(int(store["id"]), day, days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "trend": {
            "end_date": trend.end_date,
            "days": trend.days,
            "observed_days": trend.observed_days,
            "missing_days": trend.missing_days,
            "daily_sales": list(trend.daily_sales),
            "latest_sales": trend.latest_sales,
            "previous_sales_average": trend.previous_sales_average,
            "sales_change_percent": trend.sales_change_percent,
            "latest_aov": trend.latest_aov,
            "aov_change_percent": trend.aov_change_percent,
            "profit_status": trend.profit_status,
            "profit_low": trend.profit_low,
            "profit_high": trend.profit_high,
            "notable_issue": trend.notable_issue,
        }
    }
