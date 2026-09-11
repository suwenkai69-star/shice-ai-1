from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal, Sequence

from persistence.database import DatabaseTarget, coerce_database
from db_v2 import money
from services.cost_resolver import CostRange, CostResolver
from services.revenue_resolver import RevenueResolver

CALCULATION_VERSION = "PROFIT_V1"


@dataclass(frozen=True)
class ProfitComponent:
    code: str
    low: Decimal
    high: Decimal
    source: str
    source_ref: str | None
    confidence: str = "MEDIUM"


@dataclass(frozen=True)
class ProfitResult:
    status: Literal["ACTUAL_BASED", "ESTIMATED_RANGE", "UNAVAILABLE"]
    revenue: Decimal | None
    profit_low: Decimal | None
    profit_high: Decimal | None
    components: Sequence[ProfitComponent]
    missing_data: Sequence[str]
    confidence_level: str = "LOW"
    calculation_version: str = CALCULATION_VERSION


_MISSING_LABELS = {
    "FOOD": "原料成本",
    "LABOR": "人工成本",
    "RENT": "房租",
    "UTILITY": "水电杂费",
}


class ProfitEngine:
    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.revenue_resolver = RevenueResolver(self.db)
        self.cost_resolver = CostResolver(self.db)

    def _connect(self):
        return self.db.compat_connect()

    def _platform_actual(self, store_id: int, business_date: str) -> ProfitComponent | None:
        # Only fees explicitly attributable to this business_date enter today's operating profit.
        with self._connect() as con:
            rows = con.execute(
                """SELECT id,fee_type,amount FROM platform_fees
                   WHERE store_id=? AND business_date=?
                     AND fee_type IN ('COMMISSION','DELIVERY','TECH_SERVICE','MERCHANT_DISCOUNT','PROMOTION','OTHER')
                   ORDER BY id""",
                (store_id, business_date),
            ).fetchall()
        if not rows:
            return None
        amount = sum((money(row["amount"]) for row in rows), Decimal("0.00"))
        refs = ",".join(str(row["id"]) for row in rows)
        return ProfitComponent("PLATFORM", amount, amount, "ACTUAL", f"platform_fees:{refs}", "HIGH")

    def _has_platform_sales(self, store_id: int, business_date: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                """SELECT 1 FROM daily_channel_sales d JOIN channels c ON c.id=d.channel_id
                   WHERE d.store_id=? AND d.business_date=? AND c.category IN ('DELIVERY','LOCAL_LIFE') LIMIT 1""",
                (store_id, business_date),
            ).fetchone()
        return row is not None

    @staticmethod
    def _component(item: CostRange) -> ProfitComponent:
        return ProfitComponent(item.code, item.low, item.high, item.source, item.source_ref, item.confidence)

    @staticmethod
    def _confidence(components: Sequence[ProfitComponent], missing: Sequence[str]) -> str:
        if missing:
            return "LOW"
        if components and all(x.source == "ACTUAL" for x in components):
            return "HIGH"
        if any(x.confidence == "LOW" for x in components):
            return "LOW"
        return "MEDIUM"

    def _save_snapshot(self, store_id: int, business_date: str, result: ProfitResult) -> None:
        if result.revenue is None:
            return
        payload = {
            "revenue": format(result.revenue, ".2f"),
            "components": [
                {
                    "code": c.code,
                    "low": format(c.low, ".2f"),
                    "high": format(c.high, ".2f"),
                    "source": c.source,
                    "source_ref": c.source_ref,
                }
                for c in result.components
            ],
            "missing": list(result.missing_data),
        }
        signature = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        now = int(time.time() * 1000)
        with self._connect() as con:
            con.execute(
                """INSERT OR IGNORE INTO profit_snapshots(
                    store_id,business_date,as_of_time,revenue_amount,profit_status,profit_value,profit_low,profit_high,
                    data_completeness,confidence_level,calculation_version,input_signature,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    store_id, business_date, now, format(result.revenue, ".2f"), result.status, None,
                    format(result.profit_low, ".2f") if result.profit_low is not None else None,
                    format(result.profit_high, ".2f") if result.profit_high is not None else None,
                    "PARTIAL" if result.missing_data else "CORE_COMPLETE",
                    result.confidence_level, result.calculation_version, signature, now,
                ),
            )
            con.commit()

    def calculate(self, store_id: int, business_date: str) -> ProfitResult:
        revenue_result = self.revenue_resolver.resolve(store_id, business_date)
        revenue = revenue_result.amount
        if revenue is None:
            return ProfitResult(
                status="UNAVAILABLE",
                revenue=None,
                profit_low=None,
                profit_high=None,
                components=(),
                missing_data=("营业额",),
                confidence_level="LOW",
            )

        components: list[ProfitComponent] = []
        missing: list[str] = []
        for code in ("FOOD", "LABOR", "RENT", "UTILITY"):
            resolved = self.cost_resolver.resolve(store_id, business_date, code)
            if resolved is None:
                missing.append(_MISSING_LABELS[code])
            else:
                components.append(self._component(resolved))

        for code in ("MARKETING", "PACKAGING", "OTHER"):
            resolved = self.cost_resolver.resolve(store_id, business_date, code)
            if resolved is not None:
                components.append(self._component(resolved))

        platform = self._platform_actual(store_id, business_date)
        if platform is not None:
            components.append(platform)
        elif self._has_platform_sales(store_id, business_date):
            missing.append("平台费用")

        # FOOD, LABOR and RENT are required before presenting an operating-profit
        # range. Omitting any one of these major cost families would systematically
        # overstate profit, even if other minor costs are available.
        required_core = {"FOOD", "LABOR", "RENT"}
        resolved_core = {c.code for c in components} & required_core
        if not required_core.issubset(resolved_core):
            result = ProfitResult(
                status="UNAVAILABLE",
                revenue=revenue,
                profit_low=None,
                profit_high=None,
                components=tuple(components),
                missing_data=tuple(dict.fromkeys(missing or ["核心成本"])),
                confidence_level="LOW",
            )
            self._save_snapshot(store_id, business_date, result)
            return result

        cost_low = sum((c.low for c in components), Decimal("0.00"))
        cost_high = sum((c.high for c in components), Decimal("0.00"))
        profit_low = (revenue - cost_high).quantize(Decimal("0.01"))
        profit_high = (revenue - cost_low).quantize(Decimal("0.01"))
        core_components = [c for c in components if c.code in {"FOOD", "LABOR", "RENT", "UTILITY"}]
        actual_based = (
            not missing
            and len(core_components) == 4
            and all(c.source == "ACTUAL" and c.low == c.high for c in core_components)
            and all(c.low == c.high for c in components)
        )
        status: Literal["ACTUAL_BASED", "ESTIMATED_RANGE"] = "ACTUAL_BASED" if actual_based else "ESTIMATED_RANGE"
        result = ProfitResult(
            status=status,
            revenue=revenue,
            profit_low=profit_low,
            profit_high=profit_high,
            components=tuple(components),
            missing_data=tuple(dict.fromkeys(missing)),
            confidence_level=self._confidence(components, missing),
        )
        self._save_snapshot(store_id, business_date, result)
        return result
