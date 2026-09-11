from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from persistence.database import DatabaseTarget, coerce_database

from repositories.mini_sales_repository import MiniSalesRepository
from repositories.action_repository import ActionRepository
from services.anomaly_engine import AnomalyEngine
from services.completeness_service import CompletenessService
from services.profit_engine import ProfitEngine
from services.reconciliation_summary import ReconciliationSummaryService
from services.revenue_resolver import RevenueResolver
from services.verification_engine import VerificationEngine


class TodayAggregator:
    """Single-call homepage composition over already-separated business services."""

    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.sales_repo = MiniSalesRepository(self.db)
        self.revenue = RevenueResolver(self.db)
        self.profit = ProfitEngine(self.db)
        self.completeness = CompletenessService(self.db)
        self.anomalies = AnomalyEngine(self.db)
        self.reconciliation = ReconciliationSummaryService(self.db)
        self.actions = ActionRepository(self.db)

    def _sales(self, store_id: int, business_date: str) -> dict:
        revenue = self.revenue.resolve(store_id, business_date)
        orders: int | None = None
        if revenue.amount is not None:
            whole = self.sales_repo.get_store_total(store_id, business_date)
            if revenue.coverage == "WHOLE_STORE_TOTAL" and whole is not None:
                if whole.get("order_count") is not None:
                    orders = int(whole["order_count"])
            elif revenue.coverage == "CHANNEL_SUM":
                rows = self.sales_repo.list_channel_sales(store_id, business_date)
                if rows and all(row.get("order_count") is not None for row in rows):
                    orders = sum(int(row["order_count"]) for row in rows)
        aov = None
        if revenue.amount is not None and orders is not None and orders > 0:
            aov = (revenue.amount / Decimal(orders)).quantize(Decimal("0.01"))
        return {
            "amount": f"{revenue.amount:.2f}" if revenue.amount is not None else None,
            "orders": orders,
            "avg_order_value": f"{aov:.2f}" if aov is not None else None,
            "source": revenue.source,
            "coverage": revenue.coverage,
            "warnings": list(revenue.warnings),
        }

    @staticmethod
    def _profit_dict(result) -> dict:
        return {
            "status": result.status,
            "revenue": f"{result.revenue:.2f}" if result.revenue is not None else None,
            "profit_low": f"{result.profit_low:.2f}" if result.profit_low is not None else None,
            "profit_high": f"{result.profit_high:.2f}" if result.profit_high is not None else None,
            "confidence_level": result.confidence_level,
            "missing_data": list(result.missing_data),
            "calculation_version": result.calculation_version,
        }

    @staticmethod
    def _issue_dict(item) -> dict:
        return {
            "id": item.id,
            "anomaly_type": item.anomaly_type,
            "metric_code": item.metric_code,
            "severity": item.severity,
            "confidence": item.confidence,
            "estimated_impact_low": item.estimated_impact_low,
            "estimated_impact_high": item.estimated_impact_high,
            "title": item.user_title,
            "message": item.user_message,
            "priority_score": str(item.priority_score),
        }

    @staticmethod
    def _reconciliation_dict(summary) -> dict:
        return {
            "status": summary.status,
            "difference": summary.difference,
            "user_message": summary.user_message,
            "evidence_count": summary.evidence_count,
        }


    @staticmethod
    def _verification_dict(row: dict | None) -> dict | None:
        if row is None:
            return None
        return {
            "action_id": int(row["action_id"]),
            "action_title": row.get("title"),
            "verification_date": row.get("verification_date"),
            "expected_metric": row.get("expected_metric"),
            "before_value": row.get("before_value"),
            "after_value": row.get("after_value"),
            "change_rate": row.get("change_rate"),
            "result": row.get("result"),
            "confidence": row.get("confidence"),
            "explanation": row.get("explanation"),
        }

    def build(self, store_id: int, business_date: str, now: datetime) -> dict:
        # Opening a later business day is the natural V1 checkpoint to re-evaluate
        # user-confirmed actions. Verification stays deterministic and may still
        # return INSUFFICIENT_DATA when the required metric is unavailable.
        verifier = VerificationEngine(self.db)
        for action in self.actions.list_verification_candidates(store_id, business_date):
            verifier.verify(int(action["id"]), business_date)

        sales = self._sales(store_id, business_date)
        profit = self.profit.calculate(store_id, business_date)
        data_status = self.completeness.get_status(store_id, business_date)
        # Detection is the only writer for today's calculated anomalies; ranking is deterministic afterwards.
        self.anomalies.detect(store_id, business_date)
        top = self.anomalies.top_issues(store_id, business_date, 3)
        reconciliation = self.reconciliation.today(store_id, business_date)
        complete = data_status.completeness_level in {"COMPLETE", "DECLARED_COMPLETE"}

        return {
            "date": business_date,
            "as_of": now.isoformat(),
            "sales": sales,
            "profit": self._profit_dict(profit),
            "data_status": {
                "expected_sources": list(data_status.expected_sources),
                "received_sources": list(data_status.received_sources),
                "missing_sources": list(data_status.missing_sources),
                "completeness_level": data_status.completeness_level,
                "complete": complete,
                "user_declared_complete": data_status.user_declared_complete,
                "as_of_time": data_status.as_of_time,
            },
            "top_issues": [self._issue_dict(item) for item in top],
            "reconciliation": self._reconciliation_dict(reconciliation),
            "pending_actions": self.actions.count_pending(store_id, business_date),
            "yesterday_verification": self._verification_dict(
                self.actions.latest_verification(store_id, business_date)
            ),
        }
