from __future__ import annotations

import time
import hashlib
import json
from pathlib import Path

from persistence.database import DatabaseTarget, coerce_database
from typing import Any, Iterable



class UploadRepository:
    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path

    def _connect(self):
        return self.db.compat_connect()

    @staticmethod
    def _row(row: Any | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def create_document(
        self,
        *,
        store_id: int,
        user_id: int,
        document_type: str,
        original_filename: str | None,
        storage_key: str | None,
        mime_type: str | None,
        storage_policy: str = "EPHEMERAL",
        content_hash: str | None = None,
        size_bytes: int | None = None,
        processed_at: int | None = None,
        business_date: str | None = None,
        report_scope: str | None = None,
        status: str = "UPLOADED",
    ) -> int:
        now = int(time.time() * 1000)
        with self._connect() as con:
            cur = con.execute(
                """INSERT INTO upload_documents(
                       store_id,user_id,document_type,original_filename,storage_key,mime_type,
                       storage_policy,content_hash,size_bytes,processed_at,
                       business_date,report_scope,status,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(store_id), int(user_id), document_type, original_filename, storage_key,
                    mime_type, storage_policy, content_hash, size_bytes, processed_at,
                    business_date, report_scope, status, now, now,
                ),
            )
            con.commit()
            return int(cur.lastrowid)

    def get_document(self, document_id: int, *, store_id: int) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM upload_documents WHERE id=? AND store_id=?",
                (int(document_id), int(store_id)),
            ).fetchone()
        return self._row(row)

    def update_document_status(
        self,
        document_id: int,
        *,
        store_id: int,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        business_date: str | None = None,
        report_scope: str | None = None,
    ) -> None:
        now = int(time.time() * 1000)
        with self._connect() as con:
            cur = con.execute(
                """UPDATE upload_documents
                   SET status=?,error_code=?,error_message=?,
                       business_date=COALESCE(?,business_date),report_scope=COALESCE(?,report_scope),updated_at=?
                   WHERE id=? AND store_id=?""",
                (status, error_code, error_message, business_date, report_scope, now, int(document_id), int(store_id)),
            )
            if cur.rowcount != 1:
                raise KeyError("document not found")
            con.commit()

    def save_draft(
        self,
        document_id: int,
        *,
        draft_type: str,
        preview: dict[str, Any],
        canonical_payload: Any,
    ) -> dict[str, Any]:
        now = int(time.time() * 1000)
        preview_json = json.dumps(preview, ensure_ascii=False, separators=(",", ":"), default=str)
        canonical_json = json.dumps(canonical_payload, ensure_ascii=False, separators=(",", ":"), default=str)
        payload_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        with self._connect() as con:
            existing = con.execute("SELECT id,created_at FROM upload_drafts WHERE document_id=?", (int(document_id),)).fetchone()
            if existing is None:
                con.execute(
                    """INSERT INTO upload_drafts(
                           document_id,draft_type,preview_json,canonical_payload_json,payload_hash,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (int(document_id), draft_type, preview_json, canonical_json, payload_hash, now, now),
                )
            else:
                con.execute(
                    """UPDATE upload_drafts SET draft_type=?,preview_json=?,canonical_payload_json=?,
                           payload_hash=?,updated_at=? WHERE document_id=?""",
                    (draft_type, preview_json, canonical_json, payload_hash, now, int(document_id)),
                )
            row = con.execute("SELECT * FROM upload_drafts WHERE document_id=?", (int(document_id),)).fetchone()
            con.commit()
        assert row is not None
        return dict(row)

    def get_draft(self, document_id: int) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM upload_drafts WHERE document_id=?", (int(document_id),)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["preview"] = json.loads(out["preview_json"])
        out["canonical_payload"] = json.loads(out["canonical_payload_json"])
        return out

    def mark_processed(self, document_id: int, *, store_id: int) -> None:
        now = int(time.time() * 1000)
        with self._connect() as con:
            cur = con.execute(
                "UPDATE upload_documents SET processed_at=?,updated_at=? WHERE id=? AND store_id=?",
                (now, now, int(document_id), int(store_id)),
            )
            if cur.rowcount != 1:
                raise KeyError("document not found")
            con.commit()

    def create_extraction_job(
        self,
        document_id: int,
        *,
        provider: str,
        model_version: str | None = None,
        status: str = "PENDING",
    ) -> int:
        now = int(time.time() * 1000)
        with self._connect() as con:
            cur = con.execute(
                """INSERT INTO extraction_jobs(
                       document_id,status,provider,model_version,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?)""",
                (int(document_id), status, provider, model_version, now, now),
            )
            con.commit()
            return int(cur.lastrowid)

    def update_extraction_job(self, job_id: int, **values: Any) -> None:
        allowed = {
            "detected_platform", "detected_page_type", "template_code", "status",
            "provider", "model_version", "started_at", "completed_at", "error_code", "error_message",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unsupported extraction fields: {sorted(unknown)}")
        if not values:
            return
        values["updated_at"] = int(time.time() * 1000)
        assignments = ",".join(f"{key}=?" for key in values)
        params = list(values.values()) + [int(job_id)]
        with self._connect() as con:
            cur = con.execute(f"UPDATE extraction_jobs SET {assignments} WHERE id=?", params)
            if cur.rowcount != 1:
                raise KeyError("extraction job not found")
            con.commit()

    def get_latest_extraction_job(self, document_id: int) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM extraction_jobs WHERE document_id=? ORDER BY id DESC LIMIT 1",
                (int(document_id),),
            ).fetchone()
        return self._row(row)

    def replace_extracted_fields(self, job_id: int, fields: Iterable[dict[str, Any]]) -> None:
        now = int(time.time() * 1000)
        with self._connect() as con:
            con.execute("DELETE FROM extracted_fields WHERE extraction_job_id=?", (int(job_id),))
            for field in fields:
                con.execute(
                    """INSERT INTO extracted_fields(
                           extraction_job_id,field_code,raw_text,normalized_value,confidence,
                           user_corrected,confirmed_value,excluded_by_user,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        int(job_id), field["field_code"], field.get("raw_text"), field.get("normalized_value"),
                        str(field.get("confidence", "0")), int(bool(field.get("user_corrected", False))),
                        field.get("confirmed_value"), int(bool(field.get("excluded_by_user", False))), now, now,
                    ),
                )
            con.commit()

    def list_extracted_fields(self, job_id: int) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM extracted_fields WHERE extraction_job_id=? ORDER BY id",
                (int(job_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def patch_extracted_field(
        self,
        job_id: int,
        field_code: str,
        *,
        confirmed_value: str | None,
        excluded_by_user: bool = False,
    ) -> None:
        now = int(time.time() * 1000)
        with self._connect() as con:
            cur = con.execute(
                """UPDATE extracted_fields
                   SET confirmed_value=?,user_corrected=1,excluded_by_user=?,updated_at=?
                   WHERE extraction_job_id=? AND field_code=?""",
                (confirmed_value, int(excluded_by_user), now, int(job_id), field_code),
            )
            if cur.rowcount != 1:
                raise KeyError("extracted field not found")
            con.commit()

    def add_import_link(
        self,
        *,
        document_id: int,
        confirmed_by_user_id: int,
        import_file_id: int | None = None,
        import_batch_id: int | None = None,
        whole_store_record_id: int | None = None,
    ) -> int:
        if import_batch_id is None and whole_store_record_id is None:
            raise ValueError("import_batch_id or whole_store_record_id is required")
        now = int(time.time() * 1000)
        with self._connect() as con:
            cur = con.execute(
                """INSERT INTO document_import_links(
                       document_id,import_file_id,import_batch_id,whole_store_record_id,
                       confirmed_by_user_id,confirmed_at,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    int(document_id), import_file_id, import_batch_id, whole_store_record_id,
                    int(confirmed_by_user_id), now, now,
                ),
            )
            con.commit()
            return int(cur.lastrowid)
