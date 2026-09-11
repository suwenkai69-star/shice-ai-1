from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from persistence.database import DatabaseTarget, coerce_database

from repositories.mini_sales_repository import MiniSalesRepository
from services.anomaly_engine import AnomalyEngine
from services.profit_engine import ProfitEngine
from services.revenue_resolver import RevenueResolver


@dataclass(frozen=True)
class TrendSummary:
    end_date: str
    days: int
    observed_days: int
    missing_days: int
    daily_sales: tuple[dict, ...]
    latest_sales: str | None
    previous_sales_average: str | None
    sales_change_percent: str | None
    latest_aov: str | None
    aov_change_percent: str | None
    profit_status: str
    profit_low: str | None
    profit_high: str | None
    notable_issue: dict | None


class TrendService:
    ALLOWED_WINDOWS = {7, 14, 30}

    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.revenue = RevenueResolver(self.db)
        self.sales_repo = MiniSalesRepository(self.db)

    @staticmethod
    def _pct(current: Decimal, previous: Decimal | None) -> str | None:
        if previous is None or previous == 0:
            return None
        value = ((current - previous) / previous * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return f"{value:.2f}"

    def get(self, store_id: int, end_date: str, days: int = 7) -> TrendSummary:
        if days not in self.ALLOWED_WINDOWS:
            raise ValueError("days must be one of 7, 14, 30")
        end = date.fromisoformat(end_date)
        dates = [(end - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]

        daily: list[dict] = []
        values: list[tuple[str, Decimal]] = []
        for day in dates:
            result = self.revenue.resolve(store_id, day)
            if result.amount is None:
                continue
            amount = result.amount.quantize(Decimal("0.01"))
            values.append((day, amount))
            daily.append({
                "date": day,
                "sales": f"{amount:.2f}",
                "source": result.source,
                "coverage": result.coverage,
            })

        latest_sales: Decimal | None = values[-1][1] if values else None
        prior_sales = [v for _, v in values[:-1]]
        prior_avg = (sum(prior_sales, Decimal("0")) / Decimal(len(prior_sales))) if prior_sales else None

        aovs: list[tuple[str, Decimal]] = []
        for day in dates:
            total = self.sales_repo.get_store_total(store_id, day)
            if not total or total.get("order_count") is None or int(total["order_count"]) <= 0:
                continue
            aov = Decimal(str(total["gross_sales"])) / Decimal(int(total["order_count"]))
            aovs.append((day, aov))
        latest_aov = aovs[-1][1] if aovs else None
        prior_aovs = [v for _, v in aovs[:-1]]
        prior_aov_avg = (sum(prior_aovs, Decimal("0")) / Decimal(len(prior_aovs))) if prior_aovs else None

        profit = ProfitEngine(self.db).calculate(store_id, end_date)
        issue = AnomalyEngine(self.db).top_issues(store_id, end_date, 1)
        notable = None
        if issue:
            notable = {
                "id": issue[0].id,
                "anomaly_type": issue[0].anomaly_type,
                "title": issue[0].user_title,
                "message": issue[0].user_message,
                "confidence": issue[0].confidence,
            }

        return TrendSummary(
            end_date=end_date,
            days=days,
            observed_days=len(values),
            missing_days=days - len(values),
            daily_sales=tuple(daily),
            latest_sales=f"{latest_sales:.2f}" if latest_sales is not None else None,
            previous_sales_average=f"{prior_avg:.2f}" if prior_avg is not None else None,
            sales_change_percent=self._pct(latest_sales, prior_avg) if latest_sales is not None else None,
            latest_aov=f"{latest_aov:.2f}" if latest_aov is not None else None,
            aov_change_percent=self._pct(latest_aov, prior_aov_avg) if latest_aov is not None else None,
            profit_status=profit.status,
            profit_low=f"{profit.profit_low:.2f}" if profit.profit_low is not None else None,
            profit_high=f"{profit.profit_high:.2f}" if profit.profit_high is not None else None,
            notable_issue=notable,
        )
