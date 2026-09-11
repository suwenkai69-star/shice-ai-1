from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from persistence.database import DatabaseTarget, coerce_database
from recognition.provider import RecognitionProvider
from recognition.templates import get_template
from repositories.upload_repository import UploadRepository


@dataclass(frozen=True)
class DraftField:
    field_code: str
    raw_text: str | None
    normalized_value: str | None
    confidence: float


@dataclass(frozen=True)
class ExtractionDraft:
    document_id: int
    job_id: int
    status: str
    detected_platform: str
    detected_page_type: str
    template_code: str | None
    fields: tuple[DraftField, ...]


class RecognitionService:
    def __init__(self, database: DatabaseTarget, provider: RecognitionProvider):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.provider = provider
        self.repo = UploadRepository(self.db)

    def _document(self, document_id: int) -> dict:
        with self.db.compat_connect() as con:
            row = con.execute("SELECT * FROM upload_documents WHERE id=?", (int(document_id),)).fetchone()
        if row is None:
            raise KeyError("document not found")
        return dict(row)

    def _legacy_read(self, doc: dict) -> bytes:
        key = doc.get("storage_key")
        if not key or self.db_path is None:
            raise ValueError("图片文件不存在")
        return (self.db_path.parent / str(key)).read_bytes()

    def create_draft_from_bytes(self, document_id: int, image_bytes: bytes) -> ExtractionDraft:
        doc = self._document(document_id)
        if doc["document_type"] != "IMAGE":
            raise ValueError("recognition requires IMAGE document")
        job_id = self.repo.create_extraction_job(
            document_id,
            provider=type(self.provider).__name__,
            status="PENDING",
        )
        now = int(time.time() * 1000)
        self.repo.update_document_status(document_id, store_id=int(doc["store_id"]), status="PROCESSING")
        self.repo.update_extraction_job(job_id, status="PROCESSING", started_at=now)
        try:
            result = self.provider.recognize(image_bytes)
            template = get_template(result.platform, result.page_type)
            fields = []
            for field in result.fields:
                confidence = max(0.0, min(1.0, float(field.confidence)))
                fields.append(
                    {
                        "field_code": field.field_code,
                        "raw_text": field.raw_text,
                        "normalized_value": field.value,
                        "confidence": str(confidence),
                    }
                )
            self.repo.replace_extracted_fields(job_id, fields)
            done = int(time.time() * 1000)
            self.repo.update_extraction_job(
                job_id,
                detected_platform=result.platform,
                detected_page_type=result.page_type,
                template_code=template.code if template else None,
                status="NEEDS_CONFIRMATION",
                provider=result.provider or type(self.provider).__name__,
                model_version=result.model_version,
                completed_at=done,
            )
            self.repo.update_document_status(
                document_id,
                store_id=int(doc["store_id"]),
                status="NEEDS_CONFIRMATION",
                report_scope=template.report_scope if template else None,
            )
            self.repo.mark_processed(document_id, store_id=int(doc["store_id"]))
            stored = self.repo.list_extracted_fields(job_id)
            return ExtractionDraft(
                document_id,
                job_id,
                "NEEDS_CONFIRMATION",
                result.platform,
                result.page_type,
                template.code if template else None,
                tuple(
                    DraftField(
                        item["field_code"],
                        item["raw_text"],
                        item["normalized_value"],
                        float(item["confidence"]),
                    )
                    for item in stored
                ),
            )
        except Exception as exc:
            done = int(time.time() * 1000)
            self.repo.update_extraction_job(
                job_id,
                status="FAILED",
                completed_at=done,
                error_code="RECOGNITION_FAILED",
                error_message=str(exc),
            )
            self.repo.update_document_status(
                document_id,
                store_id=int(doc["store_id"]),
                status="FAILED",
                error_code="RECOGNITION_FAILED",
                error_message=str(exc),
            )
            self.repo.mark_processed(document_id, store_id=int(doc["store_id"]))
            raise

    def create_draft(self, document_id: int) -> ExtractionDraft:
        """Legacy local helper; cloud routes always pass request bytes directly."""
        doc = self._document(document_id)
        return self.create_draft_from_bytes(document_id, self._legacy_read(doc))


def recover_interrupted_jobs(database: DatabaseTarget) -> int:
    """Recover local/background work from older builds; cloud upload is synchronous."""
    db = coerce_database(database)
    now = int(time.time() * 1000)
    recovered = 0
    with db.compat_connect() as con:
        jobs = con.execute(
            "SELECT id,document_id FROM extraction_jobs WHERE status IN ('PENDING','PROCESSING')"
        ).fetchall()
        for row in jobs:
            con.execute(
                """UPDATE extraction_jobs SET status='FAILED',completed_at=?,error_code='RESTART_INTERRUPTED',
                       error_message='识别任务被服务重启中断，可重新识别',updated_at=? WHERE id=?""",
                (now, now, row["id"]),
            )
            con.execute(
                """UPDATE upload_documents SET status='FAILED',error_code='RESTART_INTERRUPTED',
                       error_message='处理任务被服务重启中断，可重试',updated_at=?
                   WHERE id=? AND status IN ('UPLOADED','PROCESSING')""",
                (now, row["document_id"]),
            )
            recovered += 1
        cur = con.execute(
            """UPDATE upload_documents SET status='FAILED',error_code='RESTART_INTERRUPTED',
                   error_message='上传处理被服务重启中断，可重试',updated_at=?
               WHERE status IN ('UPLOADED','PROCESSING')
                 AND id NOT IN (
                     SELECT document_id FROM extraction_jobs
                     WHERE status='FAILED' AND error_code='RESTART_INTERRUPTED'
                 )""",
            (now,),
        )
        recovered += max(0, cur.rowcount)
        con.commit()
    return recovered
