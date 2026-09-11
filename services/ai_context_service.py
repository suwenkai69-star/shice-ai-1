from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from persistence.database import DatabaseTarget, coerce_database
from typing import Sequence

from repositories.action_repository import ActionRepository
from services.anomaly_engine import AnomalyEngine
from services.profit_engine import ProfitEngine
from services.reconciliation_summary import ReconciliationSummaryService
from services.revenue_resolver import RevenueResolver
from repositories.mini_sales_repository import MiniSalesRepository


@dataclass(frozen=True)
class AIContext:
    facts: dict
    estimates: dict
    missing_data: Sequence[str]
    anomalies: Sequence[dict]
    reconciliation: dict
    recent_actions: Sequence[dict]
    recent_verifications: Sequence[dict]
    benchmarks_used: Sequence[dict]


class AIContextService:
    """Build an AI-safe context from structured business records only."""

    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.revenue = RevenueResolver(self.db)
        self.sales = MiniSalesRepository(self.db)
        self.profit = ProfitEngine(self.db)
        self.anomalies = AnomalyEngine(self.db)
        self.actions = ActionRepository(self.db)
        self.reconciliation = ReconciliationSummaryService(self.db)

    def build(self, store_id: int, question: str, business_date: str) -> AIContext:
        facts: dict = {"business_date": business_date}
        estimates: dict = {}
        missing: list[str] = []

        revenue = self.revenue.resolve(store_id, business_date)
        sales_fact = {
            "amount": f"{revenue.amount:.2f}" if revenue.amount is not None else None,
            "source": revenue.source,
            "coverage": revenue.coverage,
        }
        store_total = self.sales.get_store_total(store_id, business_date)
        if store_total is not None:
            orders = store_total.get("order_count")
            sales_fact["orders"] = int(orders) if orders is not None else None
            if revenue.amount is not None and orders is not None and int(orders) > 0:
                sales_fact["avg_order_value"] = f"{(revenue.amount / int(orders)):.2f}"
        if revenue.amount is not None:
            facts["sales"] = sales_fact
        else:
            missing.append("营业额")

        profit = self.profit.calculate(store_id, business_date)
        component_facts: dict[str, dict] = {}
        component_estimates: dict[str, dict] = {}
        benchmarks_used: list[dict] = []
        for item in profit.components:
            payload = {
                "low": f"{item.low:.2f}",
                "high": f"{item.high:.2f}",
                "source": item.source,
                "confidence": item.confidence,
            }
            if item.source == "ACTUAL" and item.low == item.high:
                component_facts[item.code] = payload
            else:
                component_estimates[item.code] = payload
            if item.source_ref and str(item.source_ref).startswith("industry_benchmarks:"):
                benchmarks_used.append({
                    "component": item.code,
                    "source": item.source,
                    "source_ref": item.source_ref,
                    "confidence": item.confidence,
                })
        if component_facts:
            facts["costs"] = component_facts
        if component_estimates:
            estimates["costs"] = component_estimates

        profit_payload = {
            "status": profit.status,
            "low": f"{profit.profit_low:.2f}" if profit.profit_low is not None else None,
            "high": f"{profit.profit_high:.2f}" if profit.profit_high is not None else None,
            "confidence": profit.confidence_level,
            "calculation_version": profit.calculation_version,
        }
        if profit.status == "ACTUAL_BASED":
            facts["profit"] = profit_payload
        else:
            estimates["profit"] = profit_payload
        missing.extend(str(item) for item in profit.missing_data)

        recon = self.reconciliation.today(store_id, business_date)
        if recon.status == "INSUFFICIENT_DATA":
            missing.append("平台结算/到账数据")
        else:
            facts["reconciliation"] = {
                "status": recon.status,
                "difference": recon.difference,
                "message": recon.user_message,
            }

        detected = self.anomalies.detect(store_id, business_date)
        anomaly_rows = tuple({
            "id": item.id,
            "type": item.anomaly_type,
            "metric": item.metric_code,
            "severity": item.severity,
            "confidence": item.confidence,
            "title": item.user_title,
            "message": item.user_message,
        } for item in self.anomalies.top_issues(store_id, business_date, 3))
        recent = tuple({
            "id": row["id"],
            "date": row["business_date"],
            "title": row["title"],
            "status": row["status"],
            "expected_metric": row["expected_metric"],
        } for row in self.actions.list_recent_actions(store_id, 5))
        recent_verifications = tuple({
            "action_id": row["action_id"],
            "verification_date": row["verification_date"],
            "title": row["title"],
            "expected_metric": row["expected_metric"],
            "result": row["result"],
            "confidence": row["confidence"],
            "explanation": row["explanation"],
        } for row in self.actions.list_recent_verifications(store_id, 5))
        reconciliation_payload = {
            "status": recon.status,
            "difference": recon.difference,
            "message": recon.user_message,
            "evidence_count": recon.evidence_count,
        }

        # De-duplicate labels while preserving user-facing order.
        missing_tuple = tuple(dict.fromkeys(missing))
        return AIContext(
            facts=facts,
            estimates=estimates,
            missing_data=missing_tuple,
            anomalies=anomaly_rows,
            reconciliation=reconciliation_payload,
            recent_actions=recent,
            recent_verifications=recent_verifications,
            benchmarks_used=tuple(benchmarks_used),
        )
