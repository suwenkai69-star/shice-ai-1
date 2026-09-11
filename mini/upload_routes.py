from __future__ import annotations

import hashlib
import os

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from pydantic import BaseModel

from mini.deps import current_store, get_database, user_id_from_authorization
from mini.extraction_models import ExtractionFieldsPatchRequest
from recognition.provider import RecognitionDocument, RecognitionProvider
from recognition.service import RecognitionService
from repositories.upload_repository import UploadRepository
from services.file_preview import FilePreviewError, preview_file
from services.mini_import_adapter import MiniImportAdapter, normalize_confirmed_field
from services.duplicate_detection import DuplicateDecisionRequired

router = APIRouter()


class DuplicateDecisionRequest(BaseModel):
    decision: str


class UnavailableRecognitionProvider:
    def recognize(self, image_bytes: bytes) -> RecognitionDocument:
        raise RuntimeError("截图识别服务尚未配置")


def get_recognition_provider() -> RecognitionProvider:
    provider = os.getenv("RECOGNITION_PROVIDER", "").strip().lower()
    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        model = os.getenv("OPENAI_RECOGNITION_MODEL", "").strip()
        if api_key and model:
            from recognition.providers.openai import OpenAIRecognitionProvider

            return OpenAIRecognitionProvider(api_key=api_key, model=model)
    return UnavailableRecognitionProvider()


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _image_fields(repo: UploadRepository, doc_id: int) -> tuple[dict | None, list[dict]]:
    job = repo.get_latest_extraction_job(doc_id)
    return job, repo.list_extracted_fields(int(job["id"])) if job else []


@router.post("/uploads/file")
async def upload_file(file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user_id = user_id_from_authorization(authorization)
    store = current_store(authorization)
    sid = int(store["id"])
    raw = await file.read()
    repo = UploadRepository(get_database())
    doc_id = repo.create_document(
        store_id=sid,
        user_id=user_id,
        document_type="FILE",
        original_filename=file.filename,
        storage_key=None,
        mime_type=file.content_type,
        storage_policy="EPHEMERAL",
        content_hash=_content_hash(raw),
        size_bytes=len(raw),
    )
    try:
        preview = preview_file(file.filename or "", raw)
        repo.save_draft(
            doc_id,
            draft_type="FILE",
            preview=preview.summary(),
            canonical_payload=list(preview.rows),
        )
        repo.update_document_status(
            doc_id,
            store_id=sid,
            status="NEEDS_CONFIRMATION",
            business_date=preview.business_date,
            report_scope=preview.report_scope,
        )
        repo.mark_processed(doc_id, store_id=sid)
    except FilePreviewError as exc:
        repo.update_document_status(
            doc_id,
            store_id=sid,
            status="FAILED",
            error_code=exc.code,
            error_message=str(exc),
        )
        repo.mark_processed(doc_id, store_id=sid)
        raise HTTPException(400, str(exc)) from exc
    finally:
        raw = b""
    return {"id": doc_id, "status": "NEEDS_CONFIRMATION", "preview": preview.summary()}


@router.post("/uploads/image")
async def upload_image(file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user_id = user_id_from_authorization(authorization)
    store = current_store(authorization)
    sid = int(store["id"])
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "请上传图片或截图")
    raw = await file.read()
    repo = UploadRepository(get_database())
    doc_id = repo.create_document(
        store_id=sid,
        user_id=user_id,
        document_type="IMAGE",
        original_filename=file.filename,
        storage_key=None,
        mime_type=file.content_type,
        storage_policy="EPHEMERAL",
        content_hash=_content_hash(raw),
        size_bytes=len(raw),
    )
    try:
        draft = RecognitionService(get_database(), provider=get_recognition_provider()).create_draft_from_bytes(doc_id, raw)
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc
    finally:
        raw = b""
    return {"id": doc_id, "status": draft.status}


@router.get("/uploads/{document_id}")
def get_upload(document_id: int, authorization: str | None = Header(default=None)):
    store = current_store(authorization)
    repo = UploadRepository(get_database())
    doc = repo.get_document(document_id, store_id=int(store["id"]))
    if doc is None:
        raise HTTPException(404, "没有找到这份上传数据")
    preview = None
    job = None
    fields: list[dict] = []
    if doc["document_type"] == "FILE":
        draft = repo.get_draft(document_id)
        if draft is not None:
            preview = draft["preview"]
    if doc["document_type"] == "IMAGE":
        job, fields = _image_fields(repo, document_id)
    return {"document": doc, "preview": preview, "extraction_job": job, "fields": fields}


@router.patch("/uploads/{document_id}/fields")
def patch_fields(
    document_id: int,
    payload: ExtractionFieldsPatchRequest,
    authorization: str | None = Header(default=None),
):
    store = current_store(authorization)
    sid = int(store["id"])
    repo = UploadRepository(get_database())
    doc = repo.get_document(document_id, store_id=sid)
    if doc is None:
        raise HTTPException(404, "没有找到这份上传数据")
    if doc["document_type"] != "IMAGE" or doc["status"] != "NEEDS_CONFIRMATION":
        raise HTTPException(409, "当前数据不在可修改确认状态")
    job = repo.get_latest_extraction_job(document_id)
    if not job:
        raise HTTPException(409, "没有可修改的识别结果")
    try:
        for item in payload.fields:
            value = normalize_confirmed_field(item.field_code, item.confirmed_value) if not item.excluded else None
            repo.patch_extracted_field(
                int(job["id"]),
                item.field_code,
                confirmed_value=value,
                excluded_by_user=item.excluded,
            )
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": document_id, "fields": repo.list_extracted_fields(int(job["id"]))}


@router.post("/uploads/{document_id}/confirm")
def confirm_upload(document_id: int, authorization: str | None = Header(default=None)):
    store = current_store(authorization)
    sid = int(store["id"])
    repo = UploadRepository(get_database())
    doc = repo.get_document(document_id, store_id=sid)
    if doc is None:
        raise HTTPException(404, "没有找到这份上传数据")
    if doc["status"] != "NEEDS_CONFIRMATION":
        raise HTTPException(409, "当前数据还不能确认或已经处理")
    repo.update_document_status(document_id, store_id=sid, status="CONFIRMED")
    try:
        outcome = MiniImportAdapter(get_database()).import_confirmed_document(document_id, sid)
    except DuplicateDecisionRequired as exc:
        repo.update_document_status(document_id, store_id=sid, status="NEEDS_CONFIRMATION")
        raise HTTPException(409, exc.detail()) from exc
    except (FilePreviewError, ValueError, KeyError) as exc:
        if doc["document_type"] == "IMAGE":
            repo.update_document_status(document_id, store_id=sid, status="NEEDS_CONFIRMATION")
        else:
            repo.update_document_status(
                document_id,
                store_id=sid,
                status="FAILED",
                error_code="IMPORT_FAILED",
                error_message=str(exc),
            )
        raise HTTPException(400, str(exc)) from exc
    if outcome.status != "IMPORTED":
        raise HTTPException(400, outcome.error_message or "导入失败")
    return {"id": document_id, "status": "IMPORTED", "import": outcome.__dict__}


@router.post("/uploads/{document_id}/duplicate-decision")
def duplicate_decision(
    document_id: int,
    payload: DuplicateDecisionRequest,
    authorization: str | None = Header(default=None),
):
    store = current_store(authorization)
    sid = int(store["id"])
    repo = UploadRepository(get_database())
    doc = repo.get_document(document_id, store_id=sid)
    if doc is None:
        raise HTTPException(404, "没有找到这份上传数据")
    if doc["status"] != "NEEDS_CONFIRMATION":
        raise HTTPException(409, "当前数据不在待确认状态")
    if payload.decision != "REPLACE_EXISTING":
        raise HTTPException(400, "当前日报口径只支持更新之前的数据，不能作为独立第二条累计")
    repo.update_document_status(document_id, store_id=sid, status="CONFIRMED")
    try:
        outcome = MiniImportAdapter(get_database()).import_confirmed_document(
            document_id,
            sid,
            duplicate_decision=payload.decision,
        )
    except (ValueError, KeyError) as exc:
        repo.update_document_status(document_id, store_id=sid, status="NEEDS_CONFIRMATION")
        raise HTTPException(400, str(exc)) from exc
    return {"id": document_id, "status": outcome.status, "import": outcome.__dict__}


@router.post("/uploads/{document_id}/reject")
def reject_upload(document_id: int, authorization: str | None = Header(default=None)):
    store = current_store(authorization)
    sid = int(store["id"])
    repo = UploadRepository(get_database())
    doc = repo.get_document(document_id, store_id=sid)
    if doc is None:
        raise HTTPException(404, "没有找到这份上传数据")
    if doc["status"] == "IMPORTED":
        raise HTTPException(409, "已导入的数据不能通过这个入口撤销")
    repo.update_document_status(document_id, store_id=sid, status="REJECTED")
    return {"id": document_id, "status": "REJECTED"}
