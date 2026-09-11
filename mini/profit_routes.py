from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from mini.deps import current_store, get_database
from repositories.mini_cost_repository import MiniCostRepository
from services.profit_engine import ProfitEngine, ProfitResult

router = APIRouter()

_SOURCE_LABELS = {
    "ACTUAL": "实际数据",
    "STORE_HISTORY": "根据本店历史估算",
    "LOCAL_BENCHMARK": "根据当地同类店参考",
    "INDUSTRY_BENCHMARK": "根据同类店参考",
    "USER_ESTIMATE": "你提供的估算",
}


class CostProfilePatchRequest(BaseModel):
    food_cost_mode: str | None = None
    food_cost_value: str | None = None
    labor_cost_mode: str | None = None
    monthly_labor_actual: str | None = None
    full_time_count: int | None = None
    part_time_hours_month: str | None = None
    rent_mode: str | None = None
    monthly_rent: str | None = None
    utilities_mode: str | None = None
    utilities_value: str | None = None
    owner_work_mode: str | None = None
    operating_days_per_month: int | None = None
    allocation_basis: str | None = None


def _business_date(store: dict, requested: str | None) -> str:
    if requested:
        try:
            return date.fromisoformat(requested).isoformat()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc
    timezone = store.get("timezone") or "Asia/Shanghai"
    try:
        return datetime.now(ZoneInfo(timezone)).date().isoformat()
    except Exception:
        return date.today().isoformat()


def _money_text(value: Decimal | None) -> str | None:
    return format(value, ".2f") if value is not None else None


def _serialize(result: ProfitResult) -> dict:
    return {
        "status": result.status,
        "revenue": _money_text(result.revenue),
        "profit_low": _money_text(result.profit_low),
        "profit_high": _money_text(result.profit_high),
        "confidence_level": result.confidence_level,
        "calculation_version": result.calculation_version,
        "missing_data": list(result.missing_data),
        "components": [
            {
                "code": c.code,
                "low": _money_text(c.low),
                "high": _money_text(c.high),
                "source": c.source,
                "source_label": _SOURCE_LABELS.get(c.source, "参考数据"),
                "source_ref": c.source_ref,
                "confidence": c.confidence,
            }
            for c in result.components
        ],
    }


@router.get("/profit/today")
def profit_today(
    business_date: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
):
    store = current_store(authorization)
    day = _business_date(store, business_date)
    result = ProfitEngine(get_database()).calculate(int(store["id"]), day)
    return {"business_date": day, "profit": _serialize(result)}


@router.get("/profit/profile")
def get_profit_profile(authorization: str | None = Header(default=None)):
    store = current_store(authorization)
    profile = MiniCostRepository(get_database()).get_profile(int(store["id"]))
    return {"profile": profile or {"store_id": int(store["id"])}}


@router.patch("/profit/profile")
def patch_profit_profile(payload: CostProfilePatchRequest, authorization: str | None = Header(default=None)):
    store = current_store(authorization)
    values = payload.model_dump(exclude_none=True)
    if "operating_days_per_month" in values and not 1 <= values["operating_days_per_month"] <= 31:
        raise HTTPException(status_code=400, detail="每月营业天数应在1到31之间")
    try:
        profile = MiniCostRepository(get_database()).save_profile(int(store["id"]), values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"profile": profile}
