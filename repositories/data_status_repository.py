from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from persistence.database import DatabaseTarget, coerce_database



class DataStatusRepository:
    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path

    def _connect(self):
        return self.db.compat_connect()

    def upsert_source(
        self,
        *,
        store_id: int,
        channel_code: str | None,
        data_type: str,
        report_scope: str = "",
        seen_at: int | None = None,
        appearance_days: int = 0,
        recent_30d_days: int = 0,
        is_expected: bool = False,
    ) -> None:
        now = int(seen_at if seen_at is not None else time.time() * 1000)
        with self._connect() as con:
            row = con.execute(
                """SELECT id,first_seen_at FROM store_data_sources
                   WHERE store_id=? AND COALESCE(channel_code,'')=COALESCE(?, '')
                     AND data_type=? AND report_scope=?""",
                (int(store_id), channel_code, data_type, report_scope),
            ).fetchone()
            if row is None:
                con.execute(
                    """INSERT INTO store_data_sources(
                           store_id,channel_code,data_type,report_scope,first_seen_at,last_seen_at,
                           appearance_days,recent_30d_days,is_expected,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        int(store_id), channel_code, data_type, report_scope, now, now,
                        int(appearance_days), int(recent_30d_days), int(is_expected), now,
                    ),
                )
            else:
                con.execute(
                    """UPDATE store_data_sources SET last_seen_at=?,appearance_days=?,recent_30d_days=?,
                           is_expected=?,updated_at=? WHERE id=?""",
                    (now, int(appearance_days), int(recent_30d_days), int(is_expected), now, int(row["id"])),
                )
            con.commit()

    def list_sources(self, store_id: int) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM store_data_sources WHERE store_id=? ORDER BY id",
                (int(store_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_daily_status(
        self,
        *,
        store_id: int,
        business_date: str,
        expected_source_count: int,
        received_source_count: int,
        user_declared_complete: bool,
        completeness_level: str,
        as_of_time: int,
        calculation_version: str = "COMPLETENESS_V1",
    ) -> None:
        now = int(time.time() * 1000)
        with self._connect() as con:
            con.execute(
                """INSERT INTO daily_data_status(
                       store_id,business_date,expected_source_count,received_source_count,
                       user_declared_complete,completeness_level,calculation_version,as_of_time,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(store_id,business_date) DO UPDATE SET
                       expected_source_count=excluded.expected_source_count,
                       received_source_count=excluded.received_source_count,
                       user_declared_complete=excluded.user_declared_complete,
                       completeness_level=excluded.completeness_level,
                       calculation_version=excluded.calculation_version,
                       as_of_time=excluded.as_of_time,
                       updated_at=excluded.updated_at""",
                (
                    int(store_id), business_date, int(expected_source_count), int(received_source_count),
                    int(user_declared_complete), completeness_level, calculation_version, int(as_of_time), now,
                ),
            )
            con.commit()

    def get_daily_status(self, store_id: int, business_date: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM daily_data_status WHERE store_id=? AND business_date=?",
                (int(store_id), business_date),
            ).fetchone()
        return dict(row) if row is not None else None
