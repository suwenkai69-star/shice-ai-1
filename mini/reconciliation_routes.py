from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Header, HTTPException, Query

from mini.deps import current_store, get_database
from services.reconciliation_summary import ReconciliationSummaryService

router = APIRouter()


def _serialize(summary):
    return {
        "status": summary.status,
        "difference": summary.difference,
        "user_message": summary.user_message,
        "evidence_count": summary.evidence_count,
        "details": list(summary.details),
    }


@router.get("/reconciliation/today")
def reconciliation_today(
    business_date: str | None = Query(default=None, alias="date"),
    authorization: str | None = Header(default=None),
):
    store = current_store(authorization)
    day = business_date or date.today().isoformat()
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc
    summary = ReconciliationSummaryService(get_database()).today(int(store["id"]), day)
    return {"date": day, "reconciliation": _serialize(summary)}
