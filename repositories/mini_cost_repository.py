from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from persistence.database import DatabaseTarget, coerce_database

from db_v2 import money_text

_PROFILE_FIELDS = {
    "food_cost_mode",
    "food_cost_value",
    "labor_cost_mode",
    "monthly_labor_actual",
    "full_time_count",
    "part_time_hours_month",
    "rent_mode",
    "monthly_rent",
    "utilities_mode",
    "utilities_value",
    "owner_work_mode",
    "operating_days_per_month",
    "allocation_basis",
}
_MONEY_FIELDS = {"monthly_labor_actual", "monthly_rent", "utilities_value"}


class MiniCostRepository:
    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path

    def _connect(self):
        return self.db.compat_connect()

    def get_profile(self, store_id: int) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM store_cost_profiles WHERE store_id=?", (store_id,)).fetchone()
        return dict(row) if row else None

    def save_profile(self, store_id: int, values: dict[str, Any]) -> dict[str, Any]:
        unknown = set(values) - _PROFILE_FIELDS
        if unknown:
            raise ValueError(f"unsupported cost profile fields: {sorted(unknown)}")
        now = int(time.time() * 1000)
        normalized = dict(values)
        for field in _MONEY_FIELDS:
            if field in normalized and normalized[field] not in (None, ""):
                normalized[field] = money_text(normalized[field])
        if "food_cost_value" in normalized and normalized["food_cost_value"] not in (None, ""):
            normalized["food_cost_value"] = str(normalized["food_cost_value"])
        if "part_time_hours_month" in normalized and normalized["part_time_hours_month"] not in (None, ""):
            normalized["part_time_hours_month"] = str(normalized["part_time_hours_month"])

        with self._connect() as con:
            row = con.execute("SELECT store_id FROM store_cost_profiles WHERE store_id=?", (store_id,)).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO store_cost_profiles(store_id,created_at,updated_at) VALUES(?,?,?)",
                    (store_id, now, now),
                )
            if normalized:
                assignments = [f"{key}=?" for key in normalized]
                params = list(normalized.values()) + [now, store_id]
                con.execute(
                    f"UPDATE store_cost_profiles SET {','.join(assignments)}, updated_at=? WHERE store_id=?",
                    params,
                )
            con.commit()
        profile = self.get_profile(store_id)
        assert profile is not None
        return profile


    def add_cost_entry(
        self,
        store_id: int,
        *,
        amount: str,
        cost_type: str,
        source_type: str,
        business_date: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        source_document_id: int | None = None,
        basis: str | None = None,
    ) -> dict[str, Any]:
        allowed = {"FOOD", "LABOR", "RENT", "UTILITY", "MARKETING", "PACKAGING", "OTHER"}
        if cost_type not in allowed:
            raise ValueError("invalid cost_type")
        normalized_amount = money_text(amount)
        now = int(time.time() * 1000)
        with self._connect() as con:
            cur = con.execute(
                """INSERT INTO cost_entries(
                       store_id,period_start,period_end,business_date,cost_type,amount,source_type,
                       source_document_id,basis,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(store_id), period_start, period_end, business_date, cost_type, normalized_amount,
                    source_type, source_document_id, basis, now,
                ),
            )
            entry_id = int(cur.lastrowid)
            row = con.execute("SELECT * FROM cost_entries WHERE id=? AND store_id=?", (entry_id, int(store_id))).fetchone()
            con.commit()
        assert row is not None
        return dict(row)

    def list_cost_entries(self, store_id: int, start: str, end: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM cost_entries
                   WHERE store_id=? AND business_date IS NOT NULL AND business_date BETWEEN ? AND ?
                   ORDER BY business_date,id""",
                (store_id, start, end),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_approved_benchmarks(
        self,
        category_code: str,
        metric_code: str,
        region_codes: list[str],
    ) -> list[dict[str, Any]]:
        if not region_codes:
            return []
        placeholders = ",".join("?" for _ in region_codes)
        with self._connect() as con:
            rows = con.execute(
                f"""SELECT * FROM industry_benchmarks
                    WHERE category_code=? AND metric_code=? AND review_status='APPROVED'
                      AND region_code IN ({placeholders})
                      AND (valid_to IS NULL OR valid_to='')
                    ORDER BY CASE region_level
                        WHEN 'CITY' THEN 1 WHEN 'PROVINCE' THEN 2 WHEN 'REGION' THEN 3 ELSE 4 END,
                        valid_from DESC,id DESC""",
                [category_code, metric_code, *region_codes],
            ).fetchall()
        return [dict(row) for row in rows]
