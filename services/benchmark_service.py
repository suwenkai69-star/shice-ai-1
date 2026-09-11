from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from persistence.database import DatabaseTarget, coerce_database


class BenchmarkSeedError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkRange:
    low: Decimal
    mid: Decimal | None
    high: Decimal
    source_name: str
    source_url: str
    source_date: str
    confidence_level: str
    benchmark_id: int
    region_level: str
    region_code: str
    metric_code: str


_REQUIRED_SEED_FIELDS = {
    "category_code",
    "region_level",
    "region_code",
    "metric_code",
    "low_value",
    "mid_value",
    "high_value",
    "unit",
    "source_name",
    "source_url",
    "source_date",
    "confidence_level",
    "review_status",
    "valid_from",
}
_ALLOWED_REGION_LEVELS = {"CITY", "PROVINCE", "REGION", "COUNTRY"}
_ALLOWED_CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}


class BenchmarkService:
    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path

    def _connect(self):
        return self.db.compat_connect()

    @staticmethod
    def _decimal(value: Any, field: str) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise BenchmarkSeedError(f"invalid decimal for {field}") from exc

    @classmethod
    def _validate_seed_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        missing = [key for key in _REQUIRED_SEED_FIELDS if key not in row]
        if missing:
            raise BenchmarkSeedError(f"benchmark row missing fields: {sorted(missing)}")
        if row.get("review_status") != "APPROVED":
            raise BenchmarkSeedError("production benchmark seed only accepts APPROVED rows")
        if row.get("region_level") not in _ALLOWED_REGION_LEVELS:
            raise BenchmarkSeedError("invalid region_level")
        if row.get("confidence_level") not in _ALLOWED_CONFIDENCE:
            raise BenchmarkSeedError("invalid confidence_level")
        if not str(row.get("source_name") or "").strip():
            raise BenchmarkSeedError("benchmark source_name is required")
        source_url = str(row.get("source_url") or "").strip()
        if not source_url.startswith(("http://", "https://")):
            raise BenchmarkSeedError("benchmark source_url must be auditable http(s)")
        for field in ("source_date", "valid_from"):
            try:
                date.fromisoformat(str(row.get(field) or ""))
            except ValueError as exc:
                raise BenchmarkSeedError(f"invalid {field}") from exc
        if row.get("valid_to"):
            try:
                date.fromisoformat(str(row["valid_to"]))
            except ValueError as exc:
                raise BenchmarkSeedError("invalid valid_to") from exc

        low = cls._decimal(row["low_value"], "low_value")
        mid = cls._decimal(row["mid_value"], "mid_value") if row.get("mid_value") is not None else None
        high = cls._decimal(row["high_value"], "high_value")
        if low > high or (mid is not None and not (low <= mid <= high)):
            raise BenchmarkSeedError("benchmark range must satisfy low <= mid <= high")
        return row

    def load_seed_file(self, path: str | Path) -> int:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            raise BenchmarkSeedError("benchmark seed is not valid JSON") from exc
        if not isinstance(payload, list) or not payload:
            raise BenchmarkSeedError("benchmark seed must be a non-empty list")
        rows = [self._validate_seed_row(dict(row)) for row in payload]
        now = int(time.time() * 1000)
        inserted = 0
        with self._connect() as con:
            for row in rows:
                cur = con.execute(
                    """INSERT OR IGNORE INTO industry_benchmarks(
                        category_code,region_level,region_code,metric_code,low_value,mid_value,high_value,unit,
                        source_name,source_url,source_date,confidence_level,review_status,valid_from,valid_to,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row["category_code"], row["region_level"], row.get("region_code") or "", row["metric_code"],
                        str(row["low_value"]), None if row.get("mid_value") is None else str(row["mid_value"]),
                        str(row["high_value"]), row["unit"], row["source_name"], row["source_url"], row["source_date"],
                        row["confidence_level"], "APPROVED", row["valid_from"], row.get("valid_to"), now, now,
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
            con.commit()
        return inserted

    @staticmethod
    def _province_candidate(city_code: str | None) -> str | None:
        if not city_code:
            return None
        code = city_code.strip()
        if len(code) == 6 and code.isdigit():
            return f"{code[:2]}0000"
        # Named codes can optionally encode a parent as PROVINCE:CITY.
        if ":" in code:
            return code.split(":", 1)[0]
        return None

    def find_range(
        self,
        category_code: str,
        metric_code: str,
        city_code: str | None = None,
    ) -> BenchmarkRange | None:
        today = date.today().isoformat()
        province_code = self._province_candidate(city_code)
        candidates: list[tuple[str, str]] = []
        if city_code:
            candidates.append(("CITY", city_code))
        if province_code:
            candidates.append(("PROVINCE", province_code))
        candidates.append(("COUNTRY", "CN"))

        with self._connect() as con:
            for level, code in candidates:
                row = con.execute(
                    """SELECT * FROM industry_benchmarks
                       WHERE category_code=? AND metric_code=?
                         AND region_level=? AND region_code=?
                         AND review_status='APPROVED'
                         AND valid_from<=?
                         AND (valid_to IS NULL OR valid_to='' OR valid_to>=?)
                       ORDER BY valid_from DESC,id DESC
                       LIMIT 1""",
                    (category_code, metric_code, level, code, today, today),
                ).fetchone()
                if row is not None:
                    return BenchmarkRange(
                        low=Decimal(row["low_value"]),
                        mid=Decimal(row["mid_value"]) if row["mid_value"] is not None else None,
                        high=Decimal(row["high_value"]),
                        source_name=row["source_name"],
                        source_url=row["source_url"],
                        source_date=row["source_date"],
                        confidence_level=row["confidence_level"],
                        benchmark_id=int(row["id"]),
                        region_level=row["region_level"],
                        region_code=row["region_code"],
                        metric_code=row["metric_code"],
                    )
        return None
