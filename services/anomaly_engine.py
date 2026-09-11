from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from persistence.database import DatabaseTarget, coerce_database
from typing import Any

from repositories.insight_repository import InsightRepository
from repositories.mini_sales_repository import MiniSalesRepository
from services.baseline_service import BaselineService
from services.reconciliation_summary import ReconciliationSummaryService
from services.metric_resolver import DailyMetricResolver


_SEVERITY_FACTOR = {"LOW": 2, "MEDIUM": 3, "HIGH": 4, "CRITICAL": 5}
_CONFIDENCE_FACTOR = {"LOW": Decimal("0.25"), "MEDIUM": Decimal("0.5"), "HIGH": Decimal("1.0")}


@dataclass(frozen=True)
class Anomaly:
    id: int | None
    anomaly_type: str
    metric_code: str
    observed_value: str | None
    baseline_low: str | None
    baseline_high: str | None
    baseline_source: str | None
    estimated_impact_low: str | None
    estimated_impact_high: str | None
    severity: str
    confidence: str
    actionability: int
    persistence: int
    root_cause_group: str | None
    status: str
    priority_score: Decimal
    user_title: str
    user_message: str


class AnomalyEngine:
    VERSION = "ANOMALY_V1"

    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.repo = InsightRepository(self.db)
        self.sales = MiniSalesRepository(self.db)
        self.baselines = BaselineService(self.db)
        self.metrics = DailyMetricResolver(self.db)

    @staticmethod
    def _money_factor(low: str | None, high: str | None) -> int:
        values = []
        for value in (low, high):
            if value is not None:
                try:
                    values.append(abs(Decimal(str(value))))
                except Exception:
                    pass
        impact = max(values) if values else Decimal("0")
        if impact >= Decimal("1000"):
            return 5
        if impact >= Decimal("500"):
            return 4
        if impact >= Decimal("200"):
            return 3
        if impact >= Decimal("50"):
            return 2
        return 1

    @classmethod
    def _priority_score(cls, row: dict[str, Any]) -> Decimal:
        impact = cls._money_factor(row.get("estimated_impact_low"), row.get("estimated_impact_high"))
        severity = _SEVERITY_FACTOR.get(str(row.get("severity") or "LOW"), 2)
        confidence = _CONFIDENCE_FACTOR.get(str(row.get("confidence") or "LOW"), Decimal("0.25"))
        persistence = max(1, min(3, int(row.get("persistence") or 1)))
        actionability = max(1, min(3, int(row.get("actionability") or 1)))
        score = Decimal(impact * severity * persistence * actionability) * confidence
        # Evidence-backed cash differences are a hard-priority class in V1.  This
        # does not assert blame; it only makes a concrete amount-to-check visible.
        if row.get("anomaly_type") == "RECONCILIATION_DIFFERENCE" and confidence >= Decimal("0.75"):
            score *= Decimal("2")
        return score

    @staticmethod
    def _copy(row: dict[str, Any]) -> tuple[str, str]:
        kind = str(row.get("anomaly_type") or "")
        confidence = str(row.get("confidence") or "LOW")
        uncertain = "可能" if confidence == "LOW" else ""
        if kind == "AOV_LOW":
            return "客单价比平时低", f"今天客单价{uncertain}低于参考范围，建议先看看低价订单是否变多。"
        if kind == "RECONCILIATION_DIFFERENCE":
            amount = row.get("estimated_impact_high") or row.get("estimated_impact_low")
            return "有一笔钱暂时没对上", f"目前约 ¥{amount} 暂时没对上，建议核对结算和到账记录。"
        if kind == "SALES_LOW":
            return "今天营业额偏低", f"今天营业额{uncertain}低于参考范围，可以继续看看订单和时段变化。"
        if kind == "LABOR_HIGH":
            return "人工压力偏高", f"今天人工成本{uncertain}偏高，建议检查排班和低效时段。"
        return "发现一个经营变化", f"这项指标{uncertain}偏离了参考范围，建议查看详情。"

    @classmethod
    def _from_row(cls, row: dict[str, Any]) -> Anomaly:
        title, message = cls._copy(row)
        return Anomaly(
            id=int(row["id"]) if row.get("id") is not None else None,
            anomaly_type=str(row["anomaly_type"]),
            metric_code=str(row["metric_code"]),
            observed_value=row.get("observed_value"),
            baseline_low=row.get("baseline_low"),
            baseline_high=row.get("baseline_high"),
            baseline_source=row.get("baseline_source"),
            estimated_impact_low=row.get("estimated_impact_low"),
            estimated_impact_high=row.get("estimated_impact_high"),
            severity=str(row["severity"]),
            confidence=str(row["confidence"]),
            actionability=int(row["actionability"]),
            persistence=int(row["persistence"]),
            root_cause_group=row.get("root_cause_group"),
            status=str(row.get("status") or "OPEN"),
            priority_score=cls._priority_score(row),
            user_title=title,
            user_message=message,
        )

    @staticmethod
    def _severity_for_ratio(ratio: Decimal) -> str:
        ratio = abs(ratio)
        if ratio >= Decimal("0.30"):
            return "CRITICAL"
        if ratio >= Decimal("0.15"):
            return "HIGH"
        if ratio >= Decimal("0.08"):
            return "MEDIUM"
        return "LOW"

    def _detect_aov(self, store_id: int, business_date: str) -> list[dict[str, Any]]:
        observed = self.metrics.aov(store_id, business_date)
        if observed is None:
            return []
        baseline = self.baselines.get("AOV", store_id, business_date)
        if baseline is None or baseline.mid <= 0:
            return []
        if baseline.low <= observed <= baseline.high:
            return []
        anomaly_type = "AOV_LOW" if observed < baseline.low else "AOV_HIGH"
        if anomaly_type == "AOV_HIGH":
            # Higher AOV is not considered a problem in V1 without supporting evidence.
            return []
        revenue = self.metrics.sales_amount(store_id, business_date)
        order_count = int((revenue / observed).to_integral_value()) if revenue is not None and observed > 0 else 0
        gap_per_order = max(Decimal("0"), baseline.mid - observed)
        impact = gap_per_order * Decimal(order_count)
        ratio = (baseline.mid - observed) / baseline.mid
        confidence = "HIGH" if baseline.source.startswith("STORE_") and baseline.confidence == "HIGH" else baseline.confidence
        return [{
            "anomaly_type": anomaly_type,
            "metric_code": "AOV",
            "observed_value": f"{observed:.2f}",
            "baseline_low": str(baseline.low),
            "baseline_high": str(baseline.high),
            "baseline_source": baseline.source,
            "estimated_impact_low": f"{impact:.2f}",
            "estimated_impact_high": f"{impact:.2f}",
            "severity": self._severity_for_ratio(ratio),
            "confidence": confidence if confidence in _CONFIDENCE_FACTOR else "LOW",
            "actionability": 3,
            "persistence": 1,
            "root_cause_group": "LOW_PRICE_MIX",
            "status": "OPEN",
        }]


    def _detect_reconciliation(self, store_id: int, business_date: str) -> list[dict[str, Any]]:
        summary = ReconciliationSummaryService(self.db).today(store_id, business_date)
        if summary.status != "DIFFERENCE" or summary.difference is None:
            return []
        amount = Decimal(summary.difference)
        if amount <= 0:
            return []
        severity = "CRITICAL" if amount >= Decimal("2000") else "HIGH" if amount >= Decimal("500") else "MEDIUM"
        return [{
            "anomaly_type": "RECONCILIATION_DIFFERENCE",
            "metric_code": "RECONCILIATION_DIFF",
            "observed_value": summary.difference,
            "baseline_low": "0.00",
            "baseline_high": "0.00",
            "baseline_source": "RECONCILIATION_EVIDENCE",
            "estimated_impact_low": summary.difference,
            "estimated_impact_high": summary.difference,
            "severity": severity,
            "confidence": "HIGH",
            "actionability": 3,
            "persistence": 1,
            "root_cause_group": "CASH_DIFF",
            "status": "OPEN",
        }]

    def detect(self, store_id: int, business_date: str) -> list[Anomaly]:
        rows = self._detect_aov(store_id, business_date) + self._detect_reconciliation(store_id, business_date)
        self.repo.replace_anomalies(store_id, business_date, rows)
        return [self._from_row(row) for row in self.repo.list_anomalies(store_id, business_date)]

    def top_issues(self, store_id: int, business_date: str, limit: int = 3) -> list[Anomaly]:
        limit = max(0, min(3, int(limit)))
        issues = [self._from_row(row) for row in self.repo.list_anomalies(store_id, business_date) if row.get("status") == "OPEN"]
        def ranking_key(item: Anomaly) -> tuple[int, Decimal, int]:
            cash_priority = int(
                item.anomaly_type == "RECONCILIATION_DIFFERENCE"
                and item.confidence == "HIGH"
                and (item.estimated_impact_high is not None or item.estimated_impact_low is not None)
            )
            return cash_priority, item.priority_score, item.id or 0

        issues.sort(key=ranking_key, reverse=True)
        selected: list[Anomaly] = []
        seen_groups: set[str] = set()
        for issue in issues:
            group = issue.root_cause_group or f"{issue.anomaly_type}:{issue.id}"
            if group in seen_groups:
                continue
            seen_groups.add(group)
            selected.append(issue)
            if len(selected) >= limit:
                break
        return selected
