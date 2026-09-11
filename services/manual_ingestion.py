from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from data_foundation import import_daily_sales, import_payments
from db_v2 import money_text
from repositories.mini_cost_repository import MiniCostRepository
from repositories.mini_sales_repository import MiniSalesRepository
from persistence.database import DatabaseTarget, coerce_database


class ManualIngestionError(ValueError):
    pass


def _day(value: str, label: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except Exception as exc:
        raise ManualIngestionError(f"{label}格式应为 YYYY-MM-DD") from exc


def _money(value: Any, label: str, *, nonnegative: bool = True) -> str:
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise ManualIngestionError(f"{label}不是有效金额") from exc
    if not d.is_finite():
        raise ManualIngestionError(f"{label}不是有效金额")
    if nonnegative and d < 0:
        raise ManualIngestionError(f"{label}不能小于0")
    return money_text(d)


class ManualIngestionService:
    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path

    def add_sales(self, store_id: int, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        scope = str(payload.get("scope") or "").strip().upper()
        if scope not in {"WHOLE_STORE", "CHANNEL"}:
            raise ManualIngestionError("scope 必须是 WHOLE_STORE 或 CHANNEL")
        business_date = _day(str(payload.get("business_date") or ""), "营业日期")
        gross_sales = _money(payload.get("gross_sales"), "营业额")
        order_count = payload.get("order_count")
        if order_count is not None and (not isinstance(order_count, int) or order_count < 0):
            raise ManualIngestionError("订单数应为不小于0的整数")

        if scope == "WHOLE_STORE":
            row = MiniSalesRepository(self.db).upsert_store_total(
                int(store_id),
                business_date,
                gross_sales,
                "MINI_MANUAL",
                f"manual-user:{int(user_id)}",
                order_count=order_count,
            )
            return {"scope": "WHOLE_STORE", **row}

        channel_code = str(payload.get("channel_code") or "").strip()
        if not channel_code:
            raise ManualIngestionError("CHANNEL 模式必须选择渠道")
        row: dict[str, Any] = {
            "business_date": business_date,
            "channel_code": channel_code,
            "gross_sales": gross_sales,
        }
        for key, label in (
            ("customer_paid", "顾客实付"),
            ("refund_amount", "退款金额"),
            ("merchant_discount", "商家优惠"),
            ("platform_subsidy", "平台补贴"),
        ):
            if payload.get(key) not in (None, ""):
                row[key] = _money(payload[key], label)
        if order_count is not None:
            row["order_count"] = order_count
        if payload.get("refund_count") is not None:
            rc = payload["refund_count"]
            if not isinstance(rc, int) or rc < 0:
                raise ManualIngestionError("退款笔数应为不小于0的整数")
            row["refund_count"] = rc

        raw = json.dumps([row], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        result = import_daily_sales(
            self.db,
            int(store_id),
            f"mini_manual_sales_{business_date}_{channel_code}.json",
            raw,
            "MINI_MANUAL",
            channel_code=channel_code,
            mime_type="application/json",
        )
        if result.status not in {"COMPLETED", "PARTIAL"} or result.accepted_rows < 1:
            raise ManualIngestionError(result.error_message or "销售数据未能写入")
        record = next(
            (
                r for r in MiniSalesRepository(self.db).list_channel_sales(int(store_id), business_date)
                if r["channel_code"] == channel_code
            ),
            None,
        )
        if record is None:
            raise ManualIngestionError("销售数据写入后未找到记录")
        return {"scope": "CHANNEL", **record, "import_batch_id": result.import_batch_id}

    def add_cost(self, store_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        business_date = _day(str(payload.get("business_date") or ""), "成本日期")
        amount = _money(payload.get("amount"), "成本金额")
        return MiniCostRepository(self.db).add_cost_entry(
            int(store_id),
            business_date=business_date,
            cost_type=str(payload.get("cost_type") or "").upper(),
            amount=amount,
            source_type="MINI_MANUAL",
            basis=str(payload.get("basis") or "ACTUAL").upper(),
        )

    def add_payment(self, store_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        channel_code = str(payload.get("channel_code") or "").strip()
        if not channel_code:
            raise ManualIngestionError("请选择到账渠道")
        payment_date = _day(str(payload.get("payment_date") or ""), "到账日期")
        row: dict[str, Any] = {
            "channel_code": channel_code,
            "payment_date": payment_date,
            "amount": _money(payload.get("amount"), "到账金额"),
            "source": "MINI_MANUAL",
        }
        for key in ("payment_method", "bank_reference", "settlement_reference"):
            if payload.get(key) not in (None, ""):
                row[key] = str(payload[key]).strip()
        raw = json.dumps([row], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        result = import_payments(
            self.db,
            int(store_id),
            f"mini_manual_payment_{payment_date}_{channel_code}.json",
            raw,
            "MINI_MANUAL",
            channel_code=channel_code,
            mime_type="application/json",
        )
        if result.status not in {"COMPLETED", "PARTIAL"} or result.accepted_rows < 1:
            raise ManualIngestionError(result.error_message or "到账数据未能写入")
        with self.db.compat_connect() as con:
            rec = con.execute(
                """SELECT p.*,c.code AS channel_code FROM payments p JOIN channels c ON c.id=p.channel_id
                   WHERE p.store_id=? AND p.import_batch_id=? ORDER BY p.id DESC LIMIT 1""",
                (int(store_id), result.import_batch_id),
            ).fetchone()
        if rec is None:
            raise ManualIngestionError("到账数据写入后未找到记录")
        return {**dict(rec), "import_batch_id": result.import_batch_id}
