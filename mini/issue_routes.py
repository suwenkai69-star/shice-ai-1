from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Header, HTTPException, Query

from mini.deps import current_store, get_database
from repositories.insight_repository import InsightRepository
from services.anomaly_engine import Anomaly, AnomalyEngine

router = APIRouter()


def _serialize(issue: Anomaly) -> dict:
    return {
        "id": issue.id,
        "anomaly_type": issue.anomaly_type,
        "metric_code": issue.metric_code,
        "observed_value": issue.observed_value,
        "baseline_low": issue.baseline_low,
        "baseline_high": issue.baseline_high,
        "baseline_source": issue.baseline_source,
        "estimated_impact_low": issue.estimated_impact_low,
        "estimated_impact_high": issue.estimated_impact_high,
        "severity": issue.severity,
        "confidence": issue.confidence,
        "priority_score": str(issue.priority_score),
        "title": issue.user_title,
        "message": issue.user_message,
    }


@router.get("/issues/today")
def issues_today(
    business_date: str | None = Query(default=None, alias="date"),
    authorization: str | None = Header(default=None),
):
    store = current_store(authorization)
    day = business_date or date.today().isoformat()
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc
    issues = AnomalyEngine(get_database()).top_issues(int(store["id"]), day, 3)
    return {"date": day, "issues": [_serialize(item) for item in issues]}


@router.get("/issues/{issue_id}")
def issue_detail(issue_id: int, authorization: str | None = Header(default=None)):
    store = current_store(authorization)
    row = InsightRepository(get_database()).get_anomaly(issue_id, int(store["id"]))
    if row is None:
        raise HTTPException(status_code=404, detail="没有找到这个问题")
    issue = AnomalyEngine._from_row(row)
    return {"issue": _serialize(issue)}
