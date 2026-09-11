from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class ReportIdentity:
    store_id: int
    channel_code: str | None
    business_date: str
    template_code: str | None
    report_scope: str
    platform_reference: str | None
    period_start: str | None
    period_end: str | None
    cumulative: bool
    amount: str | None = None

class DuplicateDetectionService:
    def classify(self, candidate: ReportIdentity, existing: ReportIdentity) -> str:
        if candidate.store_id != existing.store_id or candidate.business_date != existing.business_date:
            return "NEW"
        if candidate.channel_code != existing.channel_code:
            return "NEW"
        if candidate.report_scope != existing.report_scope:
            return "CONFLICTING_SCOPE"
        if candidate.template_code and existing.template_code and candidate.template_code != existing.template_code:
            return "POSSIBLE_DUPLICATE"
        if candidate.platform_reference and existing.platform_reference:
            return "UPDATE" if candidate.platform_reference == existing.platform_reference else "NEW"
        if candidate.cumulative and existing.cumulative and candidate.period_end and existing.period_end:
            # ISO datetime or same-day HH:MM lexical order is sufficient only as a temporal signal;
            # amount is never used to prove update identity.
            if candidate.period_end > existing.period_end:
                return "UPDATE"
        return "POSSIBLE_DUPLICATE"

class DuplicateDecisionRequired(ValueError):
    def __init__(self, classification: str, choices: list[str], message: str = "这份数据可能和今天已上传的数据重复"):
        super().__init__(message)
        self.classification=classification; self.choices=choices; self.message=message
    def detail(self) -> dict:
        return {"classification":self.classification,"choices":self.choices,"message":self.message}
