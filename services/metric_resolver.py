from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from repositories.mini_sales_repository import MiniSalesRepository
from services.revenue_resolver import RevenueResolver


class DailyMetricResolver:
    """Resolve owner-facing daily metrics using the same non-overlap revenue coverage rules."""

    def __init__(self, db_path: str | Path):
        self.sales = MiniSalesRepository(db_path)
        self.revenue = RevenueResolver(db_path)

    def sales_amount(self, store_id: int, business_date: str) -> Decimal | None:
        return self.revenue.resolve(store_id, business_date).amount

    def aov(self, store_id: int, business_date: str) -> Decimal | None:
        revenue = self.revenue.resolve(store_id, business_date)
        if revenue.amount is None:
            return None

        orders: int | None = None
        if revenue.coverage == "WHOLE_STORE_TOTAL":
            total = self.sales.get_store_total(store_id, business_date)
            if total is not None and total.get("order_count") is not None:
                orders = int(total["order_count"])
        elif revenue.coverage == "CHANNEL_SUM":
            rows = self.sales.list_channel_sales(store_id, business_date)
            if rows and all(row.get("order_count") is not None for row in rows):
                orders = sum(int(row["order_count"]) for row in rows)

        if orders is None or orders <= 0:
            return None
        return revenue.amount / Decimal(orders)
