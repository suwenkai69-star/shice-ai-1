from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from db_v2 import get_schema_version

SCHEMA_VERSION = 3

CATEGORY_CODES = (
    "MILK_TEA",
    "COFFEE",
    "FAST_FOOD_SNACK",
    "CHINESE_DINING",
    "HOTPOT",
    "BBQ",
    "BAKERY_DESSERT",
    "BAR_LEISURE",
    "OTHER_FOOD",
)


def migrate_v2_to_v3(
    db_path: str | Path,
    fail_after_statement: int | None = None,
) -> int:
    """Add Mini V1 core tables to an existing Schema V2 database atomically."""
    path = Path(db_path)
    current = get_schema_version(path)
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"database has newer schema version {current}; refusing downgrade to {SCHEMA_VERSION}"
        )
    if current == SCHEMA_VERSION:
        return SCHEMA_VERSION
    if current != 2:
        raise RuntimeError(f"schema v3 migration requires schema version 2, got {current}")

    con = sqlite3.connect(str(path), isolation_level=None)
    con.execute("PRAGMA foreign_keys=ON")
    statement_count = 0

    def run(sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        nonlocal statement_count
        cur = con.execute(sql, params)
        statement_count += 1
        if fail_after_statement is not None and statement_count == fail_after_statement:
            raise RuntimeError("injected v3 migration failure")
        return cur

    now = int(time.time() * 1000)
    try:
        con.execute("BEGIN IMMEDIATE")

        run(
            """CREATE TABLE mini_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                wechat_openid TEXT NOT NULL UNIQUE,
                wechat_unionid TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','DISABLED')),
                created_at INTEGER NOT NULL,
                last_login_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )"""
        )
        category_sql = ",".join(f"'{code}'" for code in CATEGORY_CODES)
        run(
            f"""CREATE TABLE store_profiles (
                store_id INTEGER PRIMARY KEY,
                category_code TEXT NOT NULL CHECK(category_code IN ({category_sql})),
                city_code TEXT,
                owner_work_mode TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            )"""
        )
        run(
            """CREATE TABLE daily_store_sales_totals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                business_date TEXT NOT NULL,
                gross_sales TEXT NOT NULL,
                order_count INTEGER,
                source_document_id INTEGER,
                import_batch_id INTEGER,
                source_scope TEXT NOT NULL DEFAULT 'STORE_TOTAL' CHECK(source_scope='STORE_TOTAL'),
                source_kind TEXT NOT NULL,
                source_ref TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(store_id,business_date),
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id)
            )"""
        )
        run(
            """CREATE TABLE store_cost_profiles (
                store_id INTEGER PRIMARY KEY,
                food_cost_mode TEXT NOT NULL DEFAULT 'UNKNOWN',
                food_cost_value TEXT,
                labor_cost_mode TEXT NOT NULL DEFAULT 'UNKNOWN',
                monthly_labor_actual TEXT,
                full_time_count INTEGER,
                part_time_hours_month TEXT,
                rent_mode TEXT NOT NULL DEFAULT 'UNKNOWN',
                monthly_rent TEXT,
                utilities_mode TEXT NOT NULL DEFAULT 'UNKNOWN',
                utilities_value TEXT,
                owner_work_mode TEXT,
                operating_days_per_month INTEGER,
                allocation_basis TEXT NOT NULL DEFAULT 'CALENDAR_DAY',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            )"""
        )
        run(
            """CREATE TABLE cost_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                period_start TEXT,
                period_end TEXT,
                business_date TEXT,
                cost_type TEXT NOT NULL CHECK(cost_type IN ('FOOD','LABOR','RENT','UTILITY','MARKETING','PACKAGING','OTHER')),
                amount TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_document_id INTEGER,
                basis TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            )"""
        )
        run(
            """CREATE INDEX ix_cost_entries_store_date_type
               ON cost_entries(store_id,business_date,cost_type)"""
        )
        run(
            """CREATE TABLE industry_benchmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_code TEXT NOT NULL,
                region_level TEXT NOT NULL CHECK(region_level IN ('CITY','PROVINCE','REGION','COUNTRY')),
                region_code TEXT NOT NULL DEFAULT '',
                metric_code TEXT NOT NULL,
                low_value TEXT NOT NULL,
                mid_value TEXT,
                high_value TEXT NOT NULL,
                unit TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_date TEXT NOT NULL,
                confidence_level TEXT NOT NULL CHECK(confidence_level IN ('LOW','MEDIUM','HIGH')),
                review_status TEXT NOT NULL CHECK(review_status IN ('DRAFT','APPROVED','RETIRED')),
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(category_code,region_level,region_code,metric_code,valid_from)
            )"""
        )
        run(
            """CREATE INDEX ix_industry_benchmarks_lookup
               ON industry_benchmarks(category_code,metric_code,review_status,region_level,region_code,valid_from)"""
        )
        run(
            """CREATE TABLE profit_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                business_date TEXT NOT NULL,
                as_of_time INTEGER NOT NULL,
                revenue_amount TEXT,
                profit_status TEXT NOT NULL,
                profit_value TEXT,
                profit_low TEXT,
                profit_high TEXT,
                data_completeness TEXT,
                confidence_level TEXT,
                calculation_version TEXT NOT NULL,
                input_signature TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            )"""
        )
        run(
            """CREATE UNIQUE INDEX uq_profit_snapshots_input
               ON profit_snapshots(store_id,business_date,calculation_version,input_signature)"""
        )
        run(
            """CREATE TABLE upload_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                document_type TEXT NOT NULL CHECK(document_type IN ('IMAGE','FILE','MANUAL')),
                original_filename TEXT,
                storage_key TEXT,
                mime_type TEXT,
                business_date TEXT,
                report_scope TEXT,
                status TEXT NOT NULL CHECK(status IN ('UPLOADED','PROCESSING','NEEDS_CONFIRMATION','CONFIRMED','IMPORTED','FAILED','REJECTED')),
                error_code TEXT,
                error_message TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )"""
        )
        run(
            """CREATE INDEX ix_upload_documents_store_created
               ON upload_documents(store_id,created_at)"""
        )
        run(
            """CREATE TABLE extraction_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                detected_platform TEXT,
                detected_page_type TEXT,
                template_code TEXT,
                status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','NEEDS_CONFIRMATION','COMPLETED','FAILED')),
                provider TEXT NOT NULL,
                model_version TEXT,
                started_at INTEGER,
                completed_at INTEGER,
                error_code TEXT,
                error_message TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(document_id) REFERENCES upload_documents(id)
            )"""
        )
        run(
            """CREATE INDEX ix_extraction_jobs_document
               ON extraction_jobs(document_id,created_at)"""
        )
        run(
            """CREATE TABLE extracted_fields (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                extraction_job_id INTEGER NOT NULL,
                field_code TEXT NOT NULL,
                raw_text TEXT,
                normalized_value TEXT,
                confidence TEXT NOT NULL,
                user_corrected INTEGER NOT NULL DEFAULT 0 CHECK(user_corrected IN (0,1)),
                confirmed_value TEXT,
                excluded_by_user INTEGER NOT NULL DEFAULT 0 CHECK(excluded_by_user IN (0,1)),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(extraction_job_id) REFERENCES extraction_jobs(id),
                UNIQUE(extraction_job_id,field_code)
            )"""
        )
        run(
            """CREATE TABLE document_import_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                import_file_id INTEGER,
                import_batch_id INTEGER,
                whole_store_record_id INTEGER,
                confirmed_by_user_id INTEGER NOT NULL,
                confirmed_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(document_id) REFERENCES upload_documents(id),
                FOREIGN KEY(import_file_id) REFERENCES import_files(id),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
                FOREIGN KEY(whole_store_record_id) REFERENCES daily_store_sales_totals(id),
                FOREIGN KEY(confirmed_by_user_id) REFERENCES users(id),
                CHECK(import_batch_id IS NOT NULL OR whole_store_record_id IS NOT NULL)
            )"""
        )
        run(
            """CREATE INDEX ix_document_import_links_document
               ON document_import_links(document_id,created_at)"""
        )
        run(
            """CREATE TABLE store_data_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                channel_code TEXT,
                data_type TEXT NOT NULL,
                report_scope TEXT NOT NULL DEFAULT '',
                first_seen_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL,
                appearance_days INTEGER NOT NULL DEFAULT 0,
                recent_30d_days INTEGER NOT NULL DEFAULT 0,
                is_expected INTEGER NOT NULL DEFAULT 0 CHECK(is_expected IN (0,1)),
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            )"""
        )
        run(
            """CREATE UNIQUE INDEX uq_store_data_sources_identity
               ON store_data_sources(store_id,COALESCE(channel_code,''),data_type,report_scope)"""
        )
        run(
            """CREATE TABLE daily_data_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                business_date TEXT NOT NULL,
                expected_source_count INTEGER NOT NULL DEFAULT 0,
                received_source_count INTEGER NOT NULL DEFAULT 0,
                user_declared_complete INTEGER NOT NULL DEFAULT 0 CHECK(user_declared_complete IN (0,1)),
                completeness_level TEXT NOT NULL CHECK(completeness_level IN ('NO_DATA','PARTIAL','COMPLETE','DECLARED_COMPLETE')),
                calculation_version TEXT NOT NULL DEFAULT 'COMPLETENESS_V1',
                as_of_time INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(store_id,business_date),
                FOREIGN KEY(store_id) REFERENCES stores(id)
            )"""
        )
        run(
            """CREATE TABLE anomaly_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                business_date TEXT NOT NULL,
                anomaly_type TEXT NOT NULL,
                metric_code TEXT NOT NULL,
                observed_value TEXT,
                baseline_low TEXT,
                baseline_high TEXT,
                baseline_source TEXT,
                estimated_impact_low TEXT,
                estimated_impact_high TEXT,
                severity TEXT NOT NULL CHECK(severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
                confidence TEXT NOT NULL CHECK(confidence IN ('LOW','MEDIUM','HIGH')),
                actionability INTEGER NOT NULL CHECK(actionability BETWEEN 1 AND 3),
                persistence INTEGER NOT NULL CHECK(persistence BETWEEN 1 AND 3),
                root_cause_group TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','ACKNOWLEDGED','RESOLVED','DISMISSED')),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            )"""
        )
        run(
            """CREATE INDEX ix_anomaly_events_store_date
               ON anomaly_events(store_id,business_date,status)"""
        )
        run(
            "INSERT OR IGNORE INTO channels(code,name,category,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("TAOBAO_FLASH", "淘宝闪购", "DELIVERY", 1, now, now),
        )
        run(
            "INSERT INTO schema_meta(key,value,updated_at) VALUES('schema_version','3',?) "
            "ON CONFLICT(key) DO UPDATE SET value='3', updated_at=excluded.updated_at",
            (now,),
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


def ensure_v3_additive_schema(db_path: str | Path) -> None:
    """Backfill additive Mini V1 tables for development snapshots already marked Schema V3.

    Mini V1 is being built as one unreleased Schema V3. Intermediate checkpoints can therefore
    already carry schema_meta=3 before later V3 tables are added. This helper is idempotent and
    only creates missing additive tables/indexes; it never changes existing financial semantics.
    """
    path = Path(db_path)
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS upload_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                document_type TEXT NOT NULL CHECK(document_type IN ('IMAGE','FILE','MANUAL')),
                original_filename TEXT,
                storage_key TEXT,
                mime_type TEXT,
                business_date TEXT,
                report_scope TEXT,
                status TEXT NOT NULL CHECK(status IN ('UPLOADED','PROCESSING','NEEDS_CONFIRMATION','CONFIRMED','IMPORTED','FAILED','REJECTED')),
                error_code TEXT,
                error_message TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS ix_upload_documents_store_created ON upload_documents(store_id,created_at);
            CREATE TABLE IF NOT EXISTS extraction_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                detected_platform TEXT,
                detected_page_type TEXT,
                template_code TEXT,
                status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','NEEDS_CONFIRMATION','COMPLETED','FAILED')),
                provider TEXT NOT NULL,
                model_version TEXT,
                started_at INTEGER,
                completed_at INTEGER,
                error_code TEXT,
                error_message TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(document_id) REFERENCES upload_documents(id)
            );
            CREATE INDEX IF NOT EXISTS ix_extraction_jobs_document ON extraction_jobs(document_id,created_at);
            CREATE TABLE IF NOT EXISTS extracted_fields (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                extraction_job_id INTEGER NOT NULL,
                field_code TEXT NOT NULL,
                raw_text TEXT,
                normalized_value TEXT,
                confidence TEXT NOT NULL,
                user_corrected INTEGER NOT NULL DEFAULT 0 CHECK(user_corrected IN (0,1)),
                confirmed_value TEXT,
                excluded_by_user INTEGER NOT NULL DEFAULT 0 CHECK(excluded_by_user IN (0,1)),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(extraction_job_id) REFERENCES extraction_jobs(id),
                UNIQUE(extraction_job_id,field_code)
            );
            CREATE TABLE IF NOT EXISTS document_import_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                import_file_id INTEGER,
                import_batch_id INTEGER,
                whole_store_record_id INTEGER,
                confirmed_by_user_id INTEGER NOT NULL,
                confirmed_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(document_id) REFERENCES upload_documents(id),
                FOREIGN KEY(import_file_id) REFERENCES import_files(id),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
                FOREIGN KEY(whole_store_record_id) REFERENCES daily_store_sales_totals(id),
                FOREIGN KEY(confirmed_by_user_id) REFERENCES users(id),
                CHECK(import_batch_id IS NOT NULL OR whole_store_record_id IS NOT NULL)
            );
            CREATE INDEX IF NOT EXISTS ix_document_import_links_document ON document_import_links(document_id,created_at);
            CREATE TABLE IF NOT EXISTS store_data_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                channel_code TEXT,
                data_type TEXT NOT NULL,
                report_scope TEXT NOT NULL DEFAULT '',
                first_seen_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL,
                appearance_days INTEGER NOT NULL DEFAULT 0,
                recent_30d_days INTEGER NOT NULL DEFAULT 0,
                is_expected INTEGER NOT NULL DEFAULT 0 CHECK(is_expected IN (0,1)),
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_store_data_sources_identity
                ON store_data_sources(store_id,COALESCE(channel_code,''),data_type,report_scope);
            CREATE TABLE IF NOT EXISTS daily_data_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                business_date TEXT NOT NULL,
                expected_source_count INTEGER NOT NULL DEFAULT 0,
                received_source_count INTEGER NOT NULL DEFAULT 0,
                user_declared_complete INTEGER NOT NULL DEFAULT 0 CHECK(user_declared_complete IN (0,1)),
                completeness_level TEXT NOT NULL CHECK(completeness_level IN ('NO_DATA','PARTIAL','COMPLETE','DECLARED_COMPLETE')),
                calculation_version TEXT NOT NULL DEFAULT 'COMPLETENESS_V1',
                as_of_time INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(store_id,business_date),
                FOREIGN KEY(store_id) REFERENCES stores(id)
            );
            CREATE TABLE IF NOT EXISTS anomaly_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                business_date TEXT NOT NULL,
                anomaly_type TEXT NOT NULL,
                metric_code TEXT NOT NULL,
                observed_value TEXT,
                baseline_low TEXT,
                baseline_high TEXT,
                baseline_source TEXT,
                estimated_impact_low TEXT,
                estimated_impact_high TEXT,
                severity TEXT NOT NULL CHECK(severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
                confidence TEXT NOT NULL CHECK(confidence IN ('LOW','MEDIUM','HIGH')),
                actionability INTEGER NOT NULL CHECK(actionability BETWEEN 1 AND 3),
                persistence INTEGER NOT NULL CHECK(persistence BETWEEN 1 AND 3),
                root_cause_group TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','ACKNOWLEDGED','RESOLVED','DISMISSED')),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            );
            CREATE INDEX IF NOT EXISTS ix_anomaly_events_store_date
                ON anomaly_events(store_id,business_date,status);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_anomaly_identity
                ON anomaly_events(store_id,business_date,anomaly_type,metric_code,COALESCE(root_cause_group,''));
            CREATE TABLE IF NOT EXISTS action_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                business_date TEXT NOT NULL,
                anomaly_id INTEGER,
                action_type TEXT NOT NULL,
                title TEXT NOT NULL,
                reason TEXT NOT NULL,
                expected_metric TEXT NOT NULL,
                expected_direction TEXT NOT NULL CHECK(expected_direction IN ('UP','DOWN','STABLE')),
                priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 3),
                status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','EXECUTED','SKIPPED','REMIND','WAITING_VERIFICATION','VERIFIED')),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(anomaly_id) REFERENCES anomaly_events(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_action_identity
                ON action_items(store_id,business_date,COALESCE(anomaly_id,-1),action_type);
            CREATE INDEX IF NOT EXISTS ix_action_store_status
                ON action_items(store_id,status,business_date);
            CREATE TABLE IF NOT EXISTS action_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_id INTEGER NOT NULL UNIQUE,
                executed_at INTEGER NOT NULL,
                execution_note TEXT,
                baseline_metric_code TEXT NOT NULL,
                baseline_metric_value TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(action_id) REFERENCES action_items(id)
            );
            CREATE TABLE IF NOT EXISTS action_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_id INTEGER NOT NULL,
                verification_date TEXT NOT NULL,
                before_value TEXT,
                after_value TEXT,
                change_rate TEXT,
                result TEXT NOT NULL CHECK(result IN ('IMPROVED','NO_CLEAR_CHANGE','WORSENED','INSUFFICIENT_DATA')),
                confidence TEXT NOT NULL CHECK(confidence IN ('LOW','MEDIUM','HIGH')),
                explanation TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(action_id,verification_date),
                FOREIGN KEY(action_id) REFERENCES action_items(id)
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                store_id INTEGER NOT NULL,
                action_id INTEGER NOT NULL,
                reminder_type TEXT NOT NULL DEFAULT 'ACTION_FOLLOWUP',
                scheduled_at INTEGER NOT NULL,
                wechat_template_id TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','SENT','FAILED','CANCELLED')),
                sent_at INTEGER,
                error_message TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(action_id) REFERENCES action_items(id)
            );
            CREATE INDEX IF NOT EXISTS ix_reminders_due ON reminders(status,scheduled_at);
            CREATE TABLE IF NOT EXISTS ai_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS ai_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('USER','ASSISTANT','SYSTEM')),
                content TEXT NOT NULL,
                context_json TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES ai_conversations(id)
            );
            CREATE INDEX IF NOT EXISTS ix_ai_messages_conversation
                ON ai_messages(conversation_id,created_at);
            """
        )
        # Additive cloud-upload metadata. Schema stays V3 because Mini V1 is unreleased.
        existing_cols = {row[1] for row in con.execute("PRAGMA table_info(upload_documents)")}
        for col, ddl in (
            ("storage_policy", "TEXT NOT NULL DEFAULT 'EPHEMERAL'"),
            ("content_hash", "TEXT"),
            ("size_bytes", "INTEGER"),
            ("processed_at", "INTEGER"),
        ):
            if col not in existing_cols:
                con.execute(f"ALTER TABLE upload_documents ADD COLUMN {col} {ddl}")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS upload_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL UNIQUE,
                draft_type TEXT NOT NULL CHECK(draft_type IN ('FILE','IMAGE')),
                preview_json TEXT NOT NULL,
                canonical_payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(document_id) REFERENCES upload_documents(id)
            );
            """
        )
        con.commit()
    finally:
        con.close()
