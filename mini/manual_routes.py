from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from mini.deps import current_store, get_database, user_id_from_authorization
from services.manual_ingestion import ManualIngestionError, ManualIngestionService

router = APIRouter()


class ManualSalesRequest(BaseModel):
    business_date: str
    scope: str
    gross_sales: str
    channel_code: str | None = None
    customer_paid: str | None = None
    order_count: int | None = None
    refund_amount: str | None = None
    refund_count: int | None = None
    merchant_discount: str | None = None
    platform_subsidy: str | None = None


class ManualCostRequest(BaseModel):
    business_date: str
    cost_type: str
    amount: str
    basis: str | None = "ACTUAL"


class ManualPaymentRequest(BaseModel):
    payment_date: str
    channel_code: str
    amount: str
    payment_method: str | None = None
    bank_reference: str | None = None
    settlement_reference: str | None = None


def _service() -> ManualIngestionService:
    return ManualIngestionService(get_database())


@router.post("/manual/sales")
def manual_sales(payload: ManualSalesRequest, authorization: str | None = Header(default=None)):
    user_id = user_id_from_authorization(authorization)
    store = current_store(authorization)
    values = payload.model_dump(exclude_none=True)
    if str(values.get("scope") or "").upper() == "CHANNEL" and not values.get("channel_code"):
        raise HTTPException(status_code=422, detail="请选择销售渠道")
    try:
        record = _service().add_sales(int(store["id"]), user_id, values)
    except ManualIngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"record": record}


@router.post("/manual/cost")
def manual_cost(payload: ManualCostRequest, authorization: str | None = Header(default=None)):
    store = current_store(authorization)
    try:
        record = _service().add_cost(int(store["id"]), payload.model_dump(exclude_none=True))
    except (ManualIngestionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"record": record}


@router.post("/manual/payment")
def manual_payment(payload: ManualPaymentRequest, authorization: str | None = Header(default=None)):
    store = current_store(authorization)
    try:
        record = _service().add_payment(int(store["id"]), payload.model_dump(exclude_none=True))
    except ManualIngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"record": record}
