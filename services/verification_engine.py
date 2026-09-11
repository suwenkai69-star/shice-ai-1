from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from persistence.database import DatabaseTarget, coerce_database

from repositories.action_repository import ActionRepository
from repositories.mini_sales_repository import MiniSalesRepository
from services.reconciliation_summary import ReconciliationSummaryService
from services.revenue_resolver import RevenueResolver
from services.metric_resolver import DailyMetricResolver


@dataclass(frozen=True)
class VerificationResult:
    id: int
    action_id: int
    verification_date: str
    before_value: str | None
    after_value: str | None
    change_rate: str | None
    result: str
    confidence: str
    explanation: str


class VerificationEngine:
    VERSION = "VERIFICATION_V1"
    RELATIVE_TOLERANCE = Decimal("0.05")

    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.actions = ActionRepository(self.db)
        self.sales = MiniSalesRepository(self.db)
        self.metrics = DailyMetricResolver(self.db)

    def _metric_value(self, store_id: int, metric_code: str, business_date: str) -> Decimal | None:
        if metric_code == "AOV":
            return self.metrics.aov(store_id, business_date)
        if metric_code == "SALES":
            return self.metrics.sales_amount(store_id, business_date)
        if metric_code == "RECONCILIATION_DIFF":
            summary = ReconciliationSummaryService(self.db).today(store_id, business_date)
            if summary.status == "INSUFFICIENT_DATA" or summary.difference is None:
                return None
            return Decimal(summary.difference)
        # V1 has no reliable daily labor fact table yet. Do not invent it.
        if metric_code == "LABOR_COST_RATE":
            return None
        return None

    @staticmethod
    def _format(value: Decimal | None) -> str | None:
        return None if value is None else f"{value:.2f}"

    @classmethod
    def _classify(cls, before: Decimal, after: Decimal, direction: str) -> tuple[str, Decimal | None]:
        if before == 0:
            if after == 0:
                return "NO_CLEAR_CHANGE", Decimal("0")
            if direction == "DOWN":
                return "WORSENED", None
            if direction == "UP":
                return "IMPROVED", None
            return "WORSENED", None

        change = (after - before) / abs(before)
        if direction == "UP":
            if change > cls.RELATIVE_TOLERANCE:
                return "IMPROVED", change
            if change < -cls.RELATIVE_TOLERANCE:
                return "WORSENED", change
            return "NO_CLEAR_CHANGE", change
        if direction == "DOWN":
            if change < -cls.RELATIVE_TOLERANCE:
                return "IMPROVED", change
            if change > cls.RELATIVE_TOLERANCE:
                return "WORSENED", change
            return "NO_CLEAR_CHANGE", change
        # STABLE means movement outside tolerance is undesirable in either direction.
        return ("NO_CLEAR_CHANGE" if abs(change) <= cls.RELATIVE_TOLERANCE else "WORSENED"), change

    @staticmethod
    def _explanation(result: str, metric_code: str, before: str | None, after: str | None) -> str:
        if result == "INSUFFICIENT_DATA":
            return f"执行后相关指标（{metric_code}）的数据还不够，暂时无法判断效果。"
        labels = {
            "IMPROVED": "有改善",
            "NO_CLEAR_CHANGE": "暂时没有明显变化",
            "WORSENED": "变差了",
        }
        return f"执行后相关指标（{metric_code}）从 {before} 变为 {after}，{labels[result]}。这只能说明指标变化，不能证明因果。"

    def verify(self, action_id: int, verification_date: str) -> VerificationResult:
        action = self.actions.get_action(action_id, store_id=self._store_id_for_action(action_id))
        if self.actions.get_execution(action_id) is None:
            raise ValueError("action has not been executed")
        before = self._metric_value(int(action["store_id"]), str(action["expected_metric"]), str(action["business_date"]))
        after = self._metric_value(int(action["store_id"]), str(action["expected_metric"]), verification_date)

        if before is None or after is None:
            result = "INSUFFICIENT_DATA"
            change = None
            confidence = "LOW"
        else:
            result, change = self._classify(before, after, str(action["expected_direction"]))
            confidence = "HIGH"

        before_text = self._format(before)
        after_text = self._format(after)
        change_text = None if change is None else f"{change:.4f}"
        explanation = self._explanation(result, str(action["expected_metric"]), before_text, after_text)
        row = self.actions.save_verification(
            action_id=action_id,
            verification_date=verification_date,
            before_value=before_text,
            after_value=after_text,
            change_rate=change_text,
            result=result,
            confidence=confidence,
            explanation=explanation,
        )
        return VerificationResult(
            id=int(row["id"]), action_id=int(row["action_id"]), verification_date=str(row["verification_date"]),
            before_value=row.get("before_value"), after_value=row.get("after_value"), change_rate=row.get("change_rate"),
            result=str(row["result"]), confidence=str(row["confidence"]), explanation=str(row["explanation"]),
        )

    def _store_id_for_action(self, action_id: int) -> int:
        store_id = self.actions.get_store_id_for_action(action_id)
        if store_id is None:
            raise KeyError("action not found")
        return store_id
