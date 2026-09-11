from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from persistence.database import DatabaseTarget, coerce_database

from data_foundation import get_reconciliation


@dataclass(frozen=True)
class ReconciliationSummary:
    status: str
    difference: str | None
    user_message: str
    evidence_count: int
    details: tuple[dict, ...]


class ReconciliationSummaryService:
    """Translate R1 reconciliation facts into cautious boss-facing language."""

    def __init__(self, database: DatabaseTarget, tolerance: str = "10.00"):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.tolerance = tolerance

    def today(self, store_id: int, business_date: str) -> ReconciliationSummary:
        raw = get_reconciliation(self.db, store_id, tolerance=self.tolerance)
        settlements = [
            row for row in raw["settlements"]
            if (row.get("settlement_date") or row.get("settlement_period_end"))
            and (row.get("settlement_date") or row.get("settlement_period_end")) <= business_date
        ]
        unmatched_payments = [
            row for row in raw["unmatched_payments"]
            if row.get("payment_date") and row.get("payment_date") <= business_date
        ]
        if not settlements and not unmatched_payments:
            return ReconciliationSummary(
                status="INSUFFICIENT_DATA",
                difference=None,
                user_message="还缺平台结算/到账数据",
                evidence_count=0,
                details=(),
            )

        difference = Decimal("0")
        has_difference = False
        details: list[dict] = []
        for row in settlements:
            raw_diff = row.get("settlement_difference")
            diff = Decimal(str(raw_diff)) if raw_diff is not None else Decimal("0")
            if row.get("status") != "MATCHED" or diff != 0:
                has_difference = True
            difference += abs(diff)
            details.append({
                "kind": "SETTLEMENT",
                "channel_code": row.get("channel_code"),
                "channel_name": row.get("channel_name"),
                "expected_settlement": row.get("expected_settlement"),
                "actual_payment": row.get("actual_payment"),
                "difference": raw_diff,
                "status": row.get("status"),
                "settlement_reference": row.get("settlement_reference"),
            })

        for row in unmatched_payments:
            has_difference = True
            amount = abs(Decimal(str(row.get("amount") or "0")))
            difference += amount
            details.append({
                "kind": "PAYMENT",
                "channel_code": row.get("channel_code"),
                "channel_name": row.get("channel_name"),
                "amount": row.get("amount"),
                "status": row.get("status"),
                "bank_reference": row.get("bank_reference"),
            })

        amount_text = f"{difference:.2f}"
        if not has_difference:
            return ReconciliationSummary(
                status="OK",
                difference=amount_text,
                user_message="暂时没发现问题",
                evidence_count=len(details),
                details=tuple(details),
            )
        return ReconciliationSummary(
            status="DIFFERENCE",
            difference=amount_text,
            user_message=f"还有 ¥{amount_text} 暂时没对上",
            evidence_count=len(details),
            details=tuple(details),
        )
