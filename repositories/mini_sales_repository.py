from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from persistence.database import DatabaseTarget, coerce_database

from db_v2 import money_text


class MiniSalesRepository:
    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path

    def _connect(self):
        return self.db.compat_connect()

    def upsert_store_total(
        self,
        store_id: int,
        business_date: str,
        gross_sales: str,
        source_kind: str,
        source_ref: str | None,
        order_count: int | None = None,
        source_document_id: int | None = None,
        import_batch_id: int | None = None,
    ) -> dict[str, Any]:
        now = int(time.time() * 1000)
        amount = money_text(gross_sales)
        with self._connect() as con:
            con.execute(
                """INSERT INTO daily_store_sales_totals(
                       store_id,business_date,gross_sales,order_count,source_document_id,import_batch_id,
                       source_scope,source_kind,source_ref,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(store_id,business_date) DO UPDATE SET
                       gross_sales=excluded.gross_sales,
                       order_count=excluded.order_count,
                       source_document_id=excluded.source_document_id,
                       import_batch_id=excluded.import_batch_id,
                       source_kind=excluded.source_kind,
                       source_ref=excluded.source_ref,
                       updated_at=excluded.updated_at""",
                (
                    store_id, business_date, amount, order_count, source_document_id, import_batch_id,
                    "STORE_TOTAL", source_kind, source_ref, now, now,
                ),
            )
            con.commit()
        row = self.get_store_total(store_id, business_date)
        assert row is not None
        return row

    def get_store_total(self, store_id: int, business_date: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM daily_store_sales_totals WHERE store_id=? AND business_date=?",
                (store_id, business_date),
            ).fetchone()
        return dict(row) if row else None

    def list_channel_sales(self, store_id: int, business_date: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT d.*, c.code AS channel_code, c.name AS channel_name, c.category AS channel_category
                   FROM daily_channel_sales d
                   JOIN channels c ON c.id=d.channel_id
                   WHERE d.store_id=? AND d.business_date=?
                   ORDER BY c.id""",
                (store_id, business_date),
            ).fetchall()
        return [dict(row) for row in rows]
