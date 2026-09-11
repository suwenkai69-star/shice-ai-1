from __future__ import annotations

import sqlite3
import time
import shutil
import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
MONEY_QUANT = Decimal("0.01")

STANDARD_CHANNELS = (
    ("WECHAT_PAY", "微信支付", "OFFLINE"),
    ("ALIPAY", "支付宝", "OFFLINE"),
    ("CASH", "现金", "OFFLINE"),
    ("MEITUAN_DELIVERY", "美团外卖", "DELIVERY"),
    ("MEITUAN_FLASH", "美团闪购", "DELIVERY"),
    ("JD_DELIVERY", "京东外卖", "DELIVERY"),
    ("DOUYIN_LOCAL", "抖音生活服务", "LOCAL_LIFE"),
    ("MEITUAN_DEALS", "美团团购", "LOCAL_LIFE"),
    ("OTHER", "其他", "OTHER"),
)


def money(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        result = value
    elif value is None or value == "":
        result = Decimal("0")
    else:
        cleaned = str(value).replace("¥", "").replace("￥", "").replace(",", "").strip()
        try:
            result = Decimal(cleaned)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"invalid money value: {value!r}") from exc
    return result.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def money_text(value: Any) -> str:
    return format(money(value), ".2f")


def connect_v2(db_path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(con, table):
        return False
    return any(row[1] == column for row in con.execute(f"PRAGMA table_info({table})"))


def get_schema_version(db_path: str | Path) -> int:
    path = Path(db_path)
    if not path.exists():
        return 0
    con = sqlite3.connect(str(path))
    try:
        if _table_exists(con, "schema_meta"):
            row = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            if row:
                try:
                    return int(row[0])
                except (TypeError, ValueError):
                    return 0
        if _table_exists(con, "stores") or _table_exists(con, "app_state"):
            return 1
        return 0
    finally:
        con.close()


def migrate_database(db_path: str | Path, fail_after_statement: int | None = None) -> int:
    """Upgrade a V0.4 database to additive Schema V2 in one transaction.

    `fail_after_statement` exists only to prove rollback behavior in tests.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    current_version = get_schema_version(path)
    if current_version > SCHEMA_VERSION:
        raise RuntimeError(f"database has newer schema version {current_version}; refusing downgrade to {SCHEMA_VERSION}")
    if current_version == 1 and path.exists():
        backup_path = path.with_name(f"{path.stem}_v04_backup{path.suffix}")
        stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000_000:09d}"
        timestamped_backup = path.with_name(f"{path.stem}_v04_backup_{stamp}{path.suffix}")
        # The canonical backup must describe this exact upgrade attempt, not an older file
        # left behind by a previous test/run. Write through a temp file before replace.
        tmp_backup = backup_path.with_suffix(backup_path.suffix + ".tmp")
        shutil.copy2(path, tmp_backup)
        os.replace(tmp_backup, backup_path)
        shutil.copy2(path, timestamped_backup)
    con = sqlite3.connect(str(path), isolation_level=None)
    statement_count = 0

    def run(sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        nonlocal statement_count
        cur = con.execute(sql, params)
        statement_count += 1
        if fail_after_statement is not None and statement_count == fail_after_statement:
            raise RuntimeError("injected migration failure")
        return cur

    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("BEGIN IMMEDIATE")
        now = int(time.time() * 1000)

        run(
            "CREATE TABLE IF NOT EXISTS schema_meta ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)"
        )

        # V0.4 stores is preserved and extended in place.
        if _table_exists(con, "stores"):
            for column, ddl in (
                ("store_type", "TEXT"),
                ("timezone", "TEXT"),
                ("currency", "TEXT"),
                ("created_at", "INTEGER"),
                ("updated_at", "INTEGER"),
            ):
                if not _column_exists(con, "stores", column):
                    run(f"ALTER TABLE stores ADD COLUMN {column} {ddl}")
            if _column_exists(con, "stores", "type"):
                run("UPDATE stores SET store_type=COALESCE(store_type,type)")
            run("UPDATE stores SET timezone=COALESCE(timezone,'Asia/Shanghai')")
            run("UPDATE stores SET currency=COALESCE(currency,'CNY')")
            run("UPDATE stores SET created_at=COALESCE(created_at,?)", (now,))
            run("UPDATE stores SET updated_at=COALESCE(updated_at,?)", (now,))

        run(
            """CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                category TEXT NOT NULL CHECK(category IN ('OFFLINE','DELIVERY','LOCAL_LIFE','OTHER')),
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        for code, name, category in STANDARD_CHANNELS:
            run(
                "INSERT OR IGNORE INTO channels(code,name,category,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (code, name, category, 1, now, now),
            )

        run(
            """CREATE TABLE IF NOT EXISTS import_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                mime_type TEXT,
                created_at INTEGER NOT NULL,
                UNIQUE(store_id,file_hash),
                FOREIGN KEY(store_id) REFERENCES stores(id)
            )"""
        )
        run(
            """CREATE TABLE IF NOT EXISTS import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                source_file_id INTEGER,
                file_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                channel_id INTEGER,
                imported_at INTEGER NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                accepted_rows INTEGER NOT NULL DEFAULT 0,
                rejected_rows INTEGER NOT NULL DEFAULT 0,
                duplicate_rows INTEGER NOT NULL DEFAULT 0,
                updated_rows INTEGER NOT NULL DEFAULT 0,
                parser_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','COMPLETED','PARTIAL','FAILED')),
                error_message TEXT,
                file_hash TEXT NOT NULL,
                unknown_fields_json TEXT NOT NULL DEFAULT '[]',
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(source_file_id) REFERENCES import_files(id),
                FOREIGN KEY(channel_id) REFERENCES channels(id)
            )"""
        )
        run(
            """CREATE TABLE IF NOT EXISTS raw_import_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                import_batch_id INTEGER NOT NULL,
                row_number INTEGER NOT NULL,
                raw_payload_json TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                error_message TEXT,
                created_at INTEGER NOT NULL,
                UNIQUE(import_batch_id,row_number),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id)
            )"""
        )
        run(
            """CREATE TABLE IF NOT EXISTS daily_channel_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                business_date TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                gross_sales TEXT NOT NULL DEFAULT '0.00',
                customer_paid TEXT,
                order_count INTEGER,
                refund_amount TEXT,
                refund_count INTEGER,
                merchant_discount TEXT,
                platform_subsidy TEXT,
                source TEXT,
                source_file_id INTEGER,
                import_batch_id INTEGER,
                raw_import_row_id INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(channel_id) REFERENCES channels(id),
                FOREIGN KEY(source_file_id) REFERENCES import_files(id),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
                FOREIGN KEY(raw_import_row_id) REFERENCES raw_import_rows(id)
            )"""
        )
        run(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_channel_sales_store_date_channel "
            "ON daily_channel_sales(store_id,business_date,channel_id)"
        )
        run(
            "CREATE INDEX IF NOT EXISTS ix_daily_channel_sales_store_date "
            "ON daily_channel_sales(store_id,business_date)"
        )
        run(
            "CREATE INDEX IF NOT EXISTS ix_daily_channel_sales_store_channel_date "
            "ON daily_channel_sales(store_id,channel_id,business_date)"
        )

        run(
            """CREATE TABLE IF NOT EXISTS settlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                settlement_period_start TEXT,
                settlement_period_end TEXT,
                settlement_date TEXT,
                gross_sales TEXT,
                customer_paid TEXT,
                commission_fee TEXT,
                delivery_fee TEXT,
                technical_service_fee TEXT,
                promotion_fee TEXT,
                merchant_discount TEXT,
                refund_amount TEXT,
                other_fee TEXT,
                platform_subsidy TEXT,
                reported_expected_settlement TEXT,
                calculated_expected_settlement TEXT,
                calculation_profile TEXT,
                calculation_version TEXT,
                settlement_reference TEXT,
                settlement_fingerprint TEXT NOT NULL,
                source_file_id INTEGER,
                import_batch_id INTEGER,
                raw_import_row_id INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(channel_id) REFERENCES channels(id),
                FOREIGN KEY(source_file_id) REFERENCES import_files(id),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
                FOREIGN KEY(raw_import_row_id) REFERENCES raw_import_rows(id)
            )"""
        )
        run(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_settlements_fingerprint "
            "ON settlements(settlement_fingerprint)"
        )
        run(
            "CREATE INDEX IF NOT EXISTS ix_settlements_store_channel_date "
            "ON settlements(store_id,channel_id,settlement_date)"
        )

        run(
            """CREATE TABLE IF NOT EXISTS platform_fees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                business_date TEXT,
                settlement_id INTEGER,
                fee_type TEXT NOT NULL CHECK(fee_type IN ('COMMISSION','DELIVERY','TECH_SERVICE','MERCHANT_DISCOUNT','PROMOTION','REFUND','OTHER')),
                amount TEXT NOT NULL,
                description TEXT,
                source_file_id INTEGER,
                import_batch_id INTEGER,
                raw_import_row_id INTEGER,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(channel_id) REFERENCES channels(id),
                FOREIGN KEY(settlement_id) REFERENCES settlements(id),
                FOREIGN KEY(source_file_id) REFERENCES import_files(id),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
                FOREIGN KEY(raw_import_row_id) REFERENCES raw_import_rows(id)
            )"""
        )

        run(
            """CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                payment_date TEXT NOT NULL,
                amount TEXT NOT NULL,
                payment_method TEXT,
                bank_reference TEXT,
                settlement_reference TEXT,
                payment_fingerprint TEXT NOT NULL,
                source TEXT,
                source_file_id INTEGER,
                import_batch_id INTEGER,
                raw_import_row_id INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(channel_id) REFERENCES channels(id),
                FOREIGN KEY(source_file_id) REFERENCES import_files(id),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
                FOREIGN KEY(raw_import_row_id) REFERENCES raw_import_rows(id)
            )"""
        )
        run(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_fingerprint ON payments(payment_fingerprint)"
        )
        run(
            "CREATE INDEX IF NOT EXISTS ix_payments_store_channel_date "
            "ON payments(store_id,channel_id,payment_date)"
        )

        run(
            """CREATE TABLE IF NOT EXISTS reconciliation_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                settlement_id INTEGER NOT NULL,
                payment_id INTEGER NOT NULL,
                matched_amount TEXT NOT NULL,
                match_type TEXT NOT NULL,
                confidence TEXT,
                created_at INTEGER NOT NULL,
                UNIQUE(settlement_id,payment_id),
                FOREIGN KEY(settlement_id) REFERENCES settlements(id),
                FOREIGN KEY(payment_id) REFERENCES payments(id)
            )"""
        )

        run(
            "CREATE INDEX IF NOT EXISTS ix_import_batches_store_imported "
            "ON import_batches(store_id,imported_at)"
        )

        run(
            "INSERT INTO schema_meta(key,value,updated_at) VALUES('schema_version',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (str(SCHEMA_VERSION), now),
        )

        con.execute("COMMIT")
        return SCHEMA_VERSION
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()
