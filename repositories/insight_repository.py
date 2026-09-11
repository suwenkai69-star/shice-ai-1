from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from persistence.database import DatabaseTarget, coerce_database



class InsightRepository:
    """Read/write boundary for Mini V1 insight calculations.

    Every business read is explicitly scoped by ``store_id``.  This repository
    intentionally exposes normalized, calculation-friendly rows rather than
    leaking SQL details into Baseline/Anomaly services.
    """

    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path

    def _connect(self):
        return self.db.compat_connect()

    def get_store_profile(self, store_id: int) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM store_profiles WHERE store_id=?",
                (store_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_metric_history(
        self,
        store_id: int,
        metric_code: str,
        business_date: str,
        days: int,
    ) -> list[tuple[str, Decimal]]:
        target = date.fromisoformat(business_date)
        start = (target - timedelta(days=days)).isoformat()
        end = (target - timedelta(days=1)).isoformat()
        metric = metric_code.upper()

        with self._connect() as con:
            rows = con.execute(
                """SELECT business_date,gross_sales,order_count
                   FROM daily_store_sales_totals
                   WHERE store_id=? AND business_date BETWEEN ? AND ?
                   ORDER BY business_date""",
                (store_id, start, end),
            ).fetchall()

        out: list[tuple[str, Decimal]] = []
        for row in rows:
            sales = Decimal(str(row["gross_sales"]))
            if metric in {"SALES", "GROSS_SALES", "REVENUE"}:
                out.append((row["business_date"], sales))
                continue
            if metric == "AOV":
                orders = row["order_count"]
                if orders is None or int(orders) <= 0:
                    continue
                out.append((row["business_date"], sales / Decimal(int(orders))))
                continue
        return out

    def replace_anomalies(
        self,
        store_id: int,
        business_date: str,
        rows: list[dict[str, Any]],
    ) -> None:
        """Refresh calculated anomalies without breaking action foreign keys.

        Anomaly identity must stay stable after an action links to it.  Current
        detections are upserted in place; previously-open identities that are no
        longer detected are marked RESOLVED rather than deleted.
        """
        import time

        now = int(time.time() * 1000)
        identities = {
            (str(row["anomaly_type"]), str(row["metric_code"]), str(row.get("root_cause_group") or ""))
            for row in rows
        }
        with self._connect() as con:
            existing = con.execute(
                """SELECT id,anomaly_type,metric_code,COALESCE(root_cause_group,'') AS root_group
                   FROM anomaly_events WHERE store_id=? AND business_date=? AND status='OPEN'""",
                (store_id, business_date),
            ).fetchall()
            for item in existing:
                key = (item["anomaly_type"], item["metric_code"], item["root_group"])
                if key not in identities:
                    con.execute(
                        "UPDATE anomaly_events SET status='RESOLVED',updated_at=? WHERE id=?",
                        (now, item["id"]),
                    )

            for row in rows:
                root = str(row.get("root_cause_group") or "")
                existing_row = con.execute(
                    """SELECT id FROM anomaly_events
                       WHERE store_id=? AND business_date=? AND anomaly_type=? AND metric_code=?
                         AND COALESCE(root_cause_group,'')=?""",
                    (store_id, business_date, row["anomaly_type"], row["metric_code"], root),
                ).fetchone()
                values = (
                    None if row.get("observed_value") is None else str(row["observed_value"]),
                    None if row.get("baseline_low") is None else str(row["baseline_low"]),
                    None if row.get("baseline_high") is None else str(row["baseline_high"]),
                    row.get("baseline_source"),
                    None if row.get("estimated_impact_low") is None else str(row["estimated_impact_low"]),
                    None if row.get("estimated_impact_high") is None else str(row["estimated_impact_high"]),
                    row["severity"], row["confidence"], int(row["actionability"]), int(row["persistence"]),
                    row.get("root_cause_group"), row.get("status", "OPEN"), now,
                )
                if existing_row is None:
                    con.execute(
                        """INSERT INTO anomaly_events(
                            store_id,business_date,anomaly_type,metric_code,observed_value,
                            baseline_low,baseline_high,baseline_source,estimated_impact_low,estimated_impact_high,
                            severity,confidence,actionability,persistence,root_cause_group,status,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            store_id, business_date, row["anomaly_type"], row["metric_code"],
                            *values[:-1], now, now,
                        ),
                    )
                else:
                    con.execute(
                        """UPDATE anomaly_events SET
                            observed_value=?,baseline_low=?,baseline_high=?,baseline_source=?,
                            estimated_impact_low=?,estimated_impact_high=?,severity=?,confidence=?,
                            actionability=?,persistence=?,root_cause_group=?,status=?,updated_at=?
                           WHERE id=?""",
                        (*values, existing_row["id"]),
                    )
            con.commit()

    def list_anomalies(self, store_id: int, business_date: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM anomaly_events
                   WHERE store_id=? AND business_date=?
                   ORDER BY id""",
                (store_id, business_date),
            ).fetchall()
        return [dict(row) for row in rows]
    def get_anomaly(self, anomaly_id: int, store_id: int) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM anomaly_events WHERE id=? AND store_id=?",
                (anomaly_id, store_id),
            ).fetchone()
        return dict(row) if row else None

