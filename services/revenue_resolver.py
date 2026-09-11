from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from db_v2 import money
from repositories.mini_sales_repository import MiniSalesRepository


@dataclass(frozen=True)
class RevenueResult:
    amount: Decimal | None
    source: str
    coverage: str
    warnings: list[str]


class RevenueResolver:
    """Resolve one non-overlapping sales total without using settlements/payments as revenue."""

    def __init__(self, db_path: str | Path):
        self.repo = MiniSalesRepository(db_path)

    def resolve(self, store_id: int, business_date: str) -> RevenueResult:
        store_total = self.repo.get_store_total(store_id, business_date)
        if store_total is not None:
            warnings: list[str] = []
            channel_rows = self.repo.list_channel_sales(store_id, business_date)
            if channel_rows:
                warnings.append("存在渠道明细；整店总额已作为收入总口径，渠道数据仅用于结构分析，不重复相加。")
            return RevenueResult(
                amount=money(store_total["gross_sales"]),
                source="ACTUAL_STORE_TOTAL",
                coverage="WHOLE_STORE_TOTAL",
                warnings=warnings,
            )

        channel_rows = self.repo.list_channel_sales(store_id, business_date)
        if not channel_rows:
            return RevenueResult(
                amount=None,
                source="NONE",
                coverage="NO_SALES_DATA",
                warnings=[],
            )

        ambiguous = [
            row for row in channel_rows
            if str(row.get("source") or "").upper() in {"OVERLAP_POSSIBLE", "AMBIGUOUS_SCOPE"}
        ]
        if ambiguous:
            return RevenueResult(
                amount=None,
                source="CHANNELS",
                coverage="AMBIGUOUS_CHANNEL_SCOPE",
                warnings=["部分渠道数据的覆盖范围不明确，为避免重复计算，暂不合并营业额。"],
            )

        total = sum((money(row["gross_sales"]) for row in channel_rows), Decimal("0.00"))
        return RevenueResult(
            amount=total,
            source="ACTUAL_CHANNEL_SUM",
            coverage="CHANNEL_SUM",
            warnings=[],
        )
