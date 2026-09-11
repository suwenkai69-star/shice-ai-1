from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median

from persistence.database import DatabaseTarget, coerce_database
from db_v2 import money
from repositories.mini_cost_repository import MiniCostRepository
from services.benchmark_service import BenchmarkRange, BenchmarkService
from services.revenue_resolver import RevenueResolver


@dataclass(frozen=True)
class CostRange:
    code: str
    low: Decimal
    high: Decimal
    source: str
    source_ref: str | None
    confidence: str


class CostResolver:
    """Resolve one operating-cost component with explicit provenance."""

    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.cost_repo = MiniCostRepository(self.db)
        self.benchmarks = BenchmarkService(self.db)
        self.revenue = RevenueResolver(self.db)

    def _connect(self):
        return self.db.compat_connect()

    def _store_context(self, store_id: int) -> tuple[str | None, str | None]:
        with self._connect() as con:
            row = con.execute(
                "SELECT p.category_code,p.city_code FROM store_profiles p WHERE p.store_id=?",
                (store_id,),
            ).fetchone()
        if row is None:
            return None, None
        return row["category_code"], row["city_code"]

    def _daily_actual(self, store_id: int, business_date: str, cost_type: str) -> CostRange | None:
        where = (
            "store_id=? AND business_date=? AND cost_type=? AND "
            "(source_type IN ('ACTUAL','USER_CONFIRMED') OR "
            "(source_type='MINI_MANUAL' AND UPPER(COALESCE(basis,'ACTUAL'))='ACTUAL'))"
        )
        params: list[object] = [store_id, business_date, cost_type]
        if cost_type == "FOOD":
            where += " AND COALESCE(UPPER(basis),'CONSUMED')<>'PURCHASE'"
        with self._connect() as con:
            rows = con.execute(
                f"SELECT id,amount FROM cost_entries WHERE {where} ORDER BY id",
                params,
            ).fetchall()
        if not rows:
            return None
        total = sum((money(row["amount"]) for row in rows), Decimal("0.00"))
        refs = ",".join(str(row["id"]) for row in rows)
        return CostRange(cost_type, total, total, "ACTUAL", f"cost_entries:{refs}", "HIGH")

    def _history_rate(
        self,
        store_id: int,
        business_date: str,
        cost_type: str,
        current_revenue: Decimal,
    ) -> CostRange | None:
        target = date.fromisoformat(business_date)
        start = (target - timedelta(days=30)).isoformat()
        end = (target - timedelta(days=1)).isoformat()
        where = (
            "store_id=? AND business_date BETWEEN ? AND ? AND cost_type=? AND "
            "(source_type IN ('ACTUAL','USER_CONFIRMED') OR "
            "(source_type='MINI_MANUAL' AND UPPER(COALESCE(basis,'ACTUAL'))='ACTUAL'))"
        )
        params: list[object] = [store_id, start, end, cost_type]
        if cost_type == "FOOD":
            where += " AND COALESCE(UPPER(basis),'CONSUMED')<>'PURCHASE'"
        with self._connect() as con:
            rows = con.execute(
                f"SELECT business_date,SUM(CAST(amount AS REAL)) AS amount FROM cost_entries WHERE {where} GROUP BY business_date ORDER BY business_date",
                params,
            ).fetchall()
        ratios: list[Decimal] = []
        for row in rows:
            rev = self.revenue.resolve(store_id, row["business_date"]).amount
            if rev is None or rev <= 0:
                continue
            ratios.append(money(row["amount"]) / rev)
        if len(ratios) < 3:
            return None
        low_rate = min(ratios)
        high_rate = max(ratios)
        # Identical historical observations intentionally remain a point estimate.
        low = (current_revenue * low_rate).quantize(Decimal("0.01"))
        high = (current_revenue * high_rate).quantize(Decimal("0.01"))
        return CostRange(
            cost_type,
            low,
            high,
            "STORE_HISTORY",
            f"history:{start}:{end}:{len(ratios)}d:median={median(ratios)}",
            "MEDIUM",
        )

    @staticmethod
    def _rate_to_cost(code: str, revenue: Decimal, benchmark: BenchmarkRange) -> CostRange:
        low = (revenue * benchmark.low).quantize(Decimal("0.01"))
        high = (revenue * benchmark.high).quantize(Decimal("0.01"))
        source = "LOCAL_BENCHMARK" if benchmark.region_level != "COUNTRY" else "INDUSTRY_BENCHMARK"
        return CostRange(
            code,
            low,
            high,
            source,
            f"industry_benchmarks:{benchmark.benchmark_id}",
            benchmark.confidence_level,
        )

    def _benchmark_rate(
        self,
        store_id: int,
        code: str,
        metric_code: str,
        revenue: Decimal,
    ) -> CostRange | None:
        category, city = self._store_context(store_id)
        if not category:
            return None
        benchmark = self.benchmarks.find_range(category, metric_code, city)
        if benchmark is None:
            return None
        return self._rate_to_cost(code, revenue, benchmark)

    def _monthly_allocation(self, business_date: str, monthly: Decimal, operating_days: int | None) -> Decimal:
        if operating_days and 1 <= int(operating_days) <= 31:
            divisor = Decimal(int(operating_days))
        else:
            d = date.fromisoformat(business_date)
            divisor = Decimal(calendar.monthrange(d.year, d.month)[1])
        return (monthly / divisor).quantize(Decimal("0.01"))

    def _food(self, store_id: int, business_date: str, revenue: Decimal) -> CostRange | None:
        actual = self._daily_actual(store_id, business_date, "FOOD")
        if actual:
            return actual
        history = self._history_rate(store_id, business_date, "FOOD", revenue)
        if history:
            return history
        profile = self.cost_repo.get_profile(store_id) or {}
        mode = str(profile.get("food_cost_mode") or "UNKNOWN")
        value = profile.get("food_cost_value")
        if value not in (None, ""):
            rate = Decimal(str(value))
            amount = (revenue * rate).quantize(Decimal("0.01"))
            if mode in {"ACTUAL_RATE", "ACTUAL"}:
                return CostRange("FOOD", amount, amount, "ACTUAL", "store_cost_profiles:food_cost_value", "HIGH")
            if mode in {"USER_ESTIMATE", "USER_RATE"}:
                return CostRange("FOOD", amount, amount, "USER_ESTIMATE", "store_cost_profiles:food_cost_value", "MEDIUM")
        return self._benchmark_rate(store_id, "FOOD", "FOOD_COST_RATE", revenue)

    def _labor(self, store_id: int, business_date: str, revenue: Decimal) -> CostRange | None:
        actual = self._daily_actual(store_id, business_date, "LABOR")
        if actual:
            return actual
        profile = self.cost_repo.get_profile(store_id) or {}
        monthly_actual = profile.get("monthly_labor_actual")
        if monthly_actual not in (None, "") and str(profile.get("labor_cost_mode") or "").startswith("ACTUAL"):
            amount = self._monthly_allocation(
                business_date,
                money(monthly_actual),
                profile.get("operating_days_per_month"),
            )
            return CostRange("LABOR", amount, amount, "ACTUAL", "store_cost_profiles:monthly_labor_actual", "HIGH")
        history = self._history_rate(store_id, business_date, "LABOR", revenue)
        if history:
            return history

        category, city = self._store_context(store_id)
        if category and (profile.get("full_time_count") or profile.get("part_time_hours_month")):
            monthly_b = self.benchmarks.find_range(category, "LABOR_MONTHLY_FULL_TIME", city)
            hourly_b = self.benchmarks.find_range(category, "LABOR_HOURLY", city)
            full_time = int(profile.get("full_time_count") or 0)
            part_hours = Decimal(str(profile.get("part_time_hours_month") or "0"))
            if (full_time == 0 or monthly_b is not None) and (part_hours == 0 or hourly_b is not None):
                low_month = Decimal("0")
                high_month = Decimal("0")
                refs: list[str] = []
                source = "LOCAL_BENCHMARK"
                confidence = "MEDIUM"
                if full_time and monthly_b:
                    low_month += monthly_b.low * full_time
                    high_month += monthly_b.high * full_time
                    refs.append(str(monthly_b.benchmark_id))
                    if monthly_b.region_level == "COUNTRY":
                        source = "INDUSTRY_BENCHMARK"
                    confidence = monthly_b.confidence_level
                if part_hours and hourly_b:
                    low_month += hourly_b.low * part_hours
                    high_month += hourly_b.high * part_hours
                    refs.append(str(hourly_b.benchmark_id))
                    if hourly_b.region_level == "COUNTRY":
                        source = "INDUSTRY_BENCHMARK"
                    if hourly_b.confidence_level == "LOW":
                        confidence = "LOW"
                low = self._monthly_allocation(business_date, low_month, profile.get("operating_days_per_month"))
                high = self._monthly_allocation(business_date, high_month, profile.get("operating_days_per_month"))
                return CostRange("LABOR", low, high, source, f"industry_benchmarks:{','.join(refs)}", confidence)

        # Labor estimation is intentionally fail-closed: without actual/history data,
        # headcount-based estimates require location-aware wage benchmarks. A generic
        # sales-rate benchmark must never be substituted for missing labor inputs.
        return None

    def _rent(self, store_id: int, business_date: str, revenue: Decimal) -> CostRange | None:
        actual = self._daily_actual(store_id, business_date, "RENT")
        if actual:
            return actual
        profile = self.cost_repo.get_profile(store_id) or {}
        monthly = profile.get("monthly_rent")
        mode = str(profile.get("rent_mode") or "UNKNOWN")
        if monthly not in (None, "") and mode.startswith("ACTUAL"):
            amount = self._monthly_allocation(business_date, money(monthly), profile.get("operating_days_per_month"))
            return CostRange("RENT", amount, amount, "ACTUAL", "store_cost_profiles:monthly_rent", "HIGH")
        if monthly not in (None, "") and mode.startswith("USER_ESTIMATE"):
            amount = self._monthly_allocation(business_date, money(monthly), profile.get("operating_days_per_month"))
            return CostRange("RENT", amount, amount, "USER_ESTIMATE", "store_cost_profiles:monthly_rent", "MEDIUM")
        return self._benchmark_rate(store_id, "RENT", "RENT_TO_SALES_RATE", revenue)

    def _utility(self, store_id: int, business_date: str, revenue: Decimal) -> CostRange | None:
        actual = self._daily_actual(store_id, business_date, "UTILITY")
        if actual:
            return actual
        profile = self.cost_repo.get_profile(store_id) or {}
        value = profile.get("utilities_value")
        mode = str(profile.get("utilities_mode") or "UNKNOWN")
        if value not in (None, ""):
            if mode in {"ACTUAL_MONTHLY", "USER_ESTIMATE_MONTHLY"}:
                amount = self._monthly_allocation(business_date, money(value), profile.get("operating_days_per_month"))
                source = "ACTUAL" if mode == "ACTUAL_MONTHLY" else "USER_ESTIMATE"
                confidence = "HIGH" if source == "ACTUAL" else "MEDIUM"
                return CostRange("UTILITY", amount, amount, source, "store_cost_profiles:utilities_value", confidence)
            if mode in {"ACTUAL_RATE", "USER_ESTIMATE_RATE"}:
                amount = (revenue * Decimal(str(value))).quantize(Decimal("0.01"))
                source = "ACTUAL" if mode == "ACTUAL_RATE" else "USER_ESTIMATE"
                confidence = "HIGH" if source == "ACTUAL" else "MEDIUM"
                return CostRange("UTILITY", amount, amount, source, "store_cost_profiles:utilities_value", confidence)
        return self._benchmark_rate(store_id, "UTILITY", "UTILITY_COST_RATE", revenue)

    def resolve(self, store_id: int, business_date: str, cost_type: str) -> CostRange | None:
        revenue = self.revenue.resolve(store_id, business_date).amount
        if revenue is None:
            return None
        cost_type = cost_type.upper()
        if cost_type == "FOOD":
            return self._food(store_id, business_date, revenue)
        if cost_type == "LABOR":
            return self._labor(store_id, business_date, revenue)
        if cost_type == "RENT":
            return self._rent(store_id, business_date, revenue)
        if cost_type == "UTILITY":
            return self._utility(store_id, business_date, revenue)
        if cost_type in {"MARKETING", "PACKAGING", "OTHER"}:
            return self._daily_actual(store_id, business_date, cost_type)
        raise ValueError(f"unsupported cost_type: {cost_type}")
