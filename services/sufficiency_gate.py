from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from services.ai_context_service import AIContext


@dataclass(frozen=True)
class SufficiencyResult:
    sufficient: bool
    missing_data: Sequence[str]
    reason: str | None = None


class SufficiencyGate:
    def check(self, question_type: str, context: AIContext) -> SufficiencyResult:
        q = question_type.upper()
        facts = context.facts
        estimates = context.estimates

        if q == "LABOR_DIAGNOSIS":
            labor = (facts.get("costs") or {}).get("LABOR") or (estimates.get("costs") or {}).get("LABOR")
            if labor is None:
                return SufficiencyResult(False, ("人工",), "缺少人工成本或可审计的人工估算")
            return SufficiencyResult(True, ())
        if q == "SALES_DIAGNOSIS":
            if not (facts.get("sales") or {}).get("amount"):
                return SufficiencyResult(False, ("营业额",), "缺少当天营业额")
            return SufficiencyResult(True, ())
        if q == "PROFIT_DIAGNOSIS":
            profit = facts.get("profit") or estimates.get("profit")
            if not profit or profit.get("status") == "UNAVAILABLE":
                missing = tuple(context.missing_data) or ("营业额",)
                return SufficiencyResult(False, missing, "利润数据不足")
            return SufficiencyResult(True, ())
        if q == "RECONCILIATION_DIAGNOSIS":
            if "reconciliation" not in facts:
                return SufficiencyResult(False, ("平台结算/到账数据",), "缺少对账事实")
            return SufficiencyResult(True, ())
        # General explanations are allowed when at least one structured fact or estimate exists.
        if len(facts) > 1 or estimates:
            return SufficiencyResult(True, ())
        return SufficiencyResult(False, tuple(context.missing_data), "当前可用经营数据不足")
