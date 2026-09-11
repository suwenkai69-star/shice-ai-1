from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from persistence.database import DatabaseTarget, coerce_database
from statistics import median

from services.benchmark_service import BenchmarkService
from repositories.insight_repository import InsightRepository


@dataclass(frozen=True)
class Baseline:
    metric_code: str
    low: Decimal
    mid: Decimal
    high: Decimal
    source: str
    sample_size: int
    confidence: str
    benchmark_id: int | None = None


class BaselineService:
    VERSION = "BASELINE_V1"
    # A recent window should win as soon as it represents roughly a working week.
    WINDOW_RULES = ((7, 5, "STORE_7D"), (14, 8, "STORE_14D"), (30, 15, "STORE_30D"))

    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.repo = InsightRepository(self.db)
        self.benchmarks = BenchmarkService(self.db)

    @staticmethod
    def _robust_range(values: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
        """Median ± 3*MAD, clipped to observed range.

        MAD keeps a single holiday/promotion spike from turning the normal range
        into a misleadingly wide band.  When MAD is zero, fall back to the
        central observed cluster around the median rather than a prior-day value.
        """
        ordered = sorted(values)
        mid = Decimal(str(median(ordered)))
        deviations = sorted(abs(v - mid) for v in ordered)
        mad = Decimal(str(median(deviations)))
        if mad == 0:
            nonzero = [d for d in deviations if d > 0]
            mad = min(nonzero) if nonzero else Decimal("0")
        if mad == 0:
            return mid, mid, mid
        low = max(min(ordered), mid - mad * Decimal("3"))
        high = min(max(ordered), mid + mad * Decimal("3"))
        return low, mid, high

    def get(self, metric_code: str, store_id: int, business_date: str) -> Baseline | None:
        metric = metric_code.upper()
        for days, minimum, source in self.WINDOW_RULES:
            history = self.repo.list_metric_history(store_id, metric, business_date, days)
            if len(history) >= minimum:
                values = [value for _, value in history]
                low, mid, high = self._robust_range(values)
                return Baseline(
                    metric_code=metric,
                    low=low,
                    mid=mid,
                    high=high,
                    source=source,
                    sample_size=len(values),
                    confidence="HIGH" if source == "STORE_7D" else "MEDIUM",
                )

        profile = self.repo.get_store_profile(store_id)
        if not profile:
            return None
        benchmark = self.benchmarks.find_range(
            category_code=profile["category_code"],
            metric_code=metric,
            city_code=profile.get("city_code"),
        )
        if benchmark is None:
            return None
        source = "LOCAL_CATEGORY" if benchmark.region_level != "COUNTRY" else "NATIONAL_CATEGORY"
        return Baseline(
            metric_code=metric,
            low=benchmark.low,
            mid=benchmark.mid if benchmark.mid is not None else (benchmark.low + benchmark.high) / Decimal("2"),
            high=benchmark.high,
            source=source,
            sample_size=0,
            confidence=benchmark.confidence_level,
            benchmark_id=benchmark.benchmark_id,
        )
