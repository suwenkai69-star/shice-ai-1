from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from data_foundation import (
    SALES_ALIASES,
    _date_text,
    _int_optional,
    _money_optional,
    _pick,
    import_daily_sales,
    import_payments,
    import_settlements,
)
from db_v2 import money_text
from recognition.templates import get_template_by_code
from repositories.mini_sales_repository import MiniSalesRepository
from repositories.upload_repository import UploadRepository
from services.file_preview import FilePreviewError
from services.duplicate_detection import DuplicateDetectionService, DuplicateDecisionRequired, ReportIdentity
from persistence.database import DatabaseTarget, coerce_database


@dataclass(frozen=True)
class ImportOutcome:
    status: str
    import_batch_id: int | None
    whole_store_record_id: int | None
    accepted_rows: int
    rejected_rows: int
    error_message: str | None = None


_FIELD_TO_SALES = {
    "BUSINESS_DATE": "business_date",
    "GROSS_SALES": "gross_sales",
    "CUSTOMER_PAID": "customer_paid",
    "ORDER_COUNT": "order_count",
    "REFUND_AMOUNT": "refund_amount",
    "REFUND_COUNT": "refund_count",
    "MERCHANT_DISCOUNT": "merchant_discount",
    "PLATFORM_SUBSIDY": "platform_subsidy",
}
_MONEY_FIELDS = {"GROSS_SALES", "CUSTOMER_PAID", "REFUND_AMOUNT", "MERCHANT_DISCOUNT", "PLATFORM_SUBSIDY", "PLATFORM_FEE"}
_COUNT_FIELDS = {"ORDER_COUNT", "REFUND_COUNT"}


def normalize_confirmed_field(field_code: str, value: str | None) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if field_code == "BUSINESS_DATE":
        try:
            return date.fromisoformat(text.replace("/", "-")).isoformat()
        except Exception as exc:
            raise ValueError("BUSINESS_DATE 日期格式应为 YYYY-MM-DD") from exc
    if field_code in _MONEY_FIELDS:
        try:
            d = Decimal(text.replace(",", ""))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field_code} 不是有效金额") from exc
        if not d.is_finite() or d < 0:
            raise ValueError(f"{field_code} 不是有效非负金额")
        return money_text(d)
    if field_code in _COUNT_FIELDS:
        try:
            d = Decimal(text.replace(",", ""))
            if d != d.to_integral_value() or d < 0:
                raise ValueError
            return str(int(d))
        except Exception as exc:
            raise ValueError(f"{field_code} 不是有效非负整数") from exc
    return text


class MiniImportAdapter:
    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.uploads = UploadRepository(self.db)

    def _link_batch(self, document_id: int, doc: dict[str, Any], batch_id: int) -> None:
        with self.db.compat_connect() as con:
            row = con.execute("SELECT source_file_id FROM import_batches WHERE id=?", (batch_id,)).fetchone()
        if row is None:
            raise ValueError("import batch missing after confirmed import")
        self.uploads.add_import_link(
            document_id=document_id,
            confirmed_by_user_id=int(doc["user_id"]),
            import_file_id=int(row["source_file_id"]),
            import_batch_id=int(batch_id),
        )

    def _import_file(self, document_id: int, store_id: int, doc: dict[str, Any]) -> ImportOutcome:
        draft = self.uploads.get_draft(document_id)
        if draft is None or draft.get("draft_type") != "FILE":
            raise FilePreviewError("上传文件的结构化预览不存在，请重新上传")
        preview = draft["preview"]
        rows = list(draft["canonical_payload"])
        data_type = str(preview.get("data_type") or "")
        report_scope = str(preview.get("report_scope") or "CHANNEL")
        business_date_hint = preview.get("business_date")
        if report_scope == "WHOLE_STORE":
            if data_type != "SALES" or len(rows) != 1:
                raise ValueError("整店文件当前只支持单日单行销售总额")
            row = dict(rows[0])
            business_date = _date_text(_pick(row, SALES_ALIASES["business_date"]), "营业日期")
            gross_sales = _money_optional(_pick(row, SALES_ALIASES["gross_sales"]), "销售额")
            if gross_sales is None:
                raise ValueError("缺少销售额")
            order_count = _int_optional(_pick(row, SALES_ALIASES["order_count"]), "订单数")
            saved = MiniSalesRepository(self.db).upsert_store_total(
                store_id, business_date, gross_sales, "MINI_FILE_CONFIRMED", f"document:{document_id}",
                order_count=order_count, source_document_id=document_id,
            )
            self.uploads.add_import_link(
                document_id=document_id, confirmed_by_user_id=int(doc["user_id"]),
                whole_store_record_id=int(saved["id"]),
            )
            self.uploads.update_document_status(document_id, store_id=store_id, status="IMPORTED", business_date=business_date, report_scope="WHOLE_STORE")
            return ImportOutcome("IMPORTED", None, int(saved["id"]), 1, 0)

        canonical = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        filename = f"mini_confirmed_{document_id}_{data_type.lower()}.json"
        if data_type == "SALES":
            result = import_daily_sales(self.db, store_id, filename, canonical, "MINI_FILE_CONFIRMED", mime_type="application/json")
        elif data_type == "PAYMENT":
            result = import_payments(self.db, store_id, filename, canonical, "MINI_FILE_CONFIRMED", mime_type="application/json")
        elif data_type == "SETTLEMENT":
            result = import_settlements(self.db, store_id, filename, canonical, "MINI_FILE_CONFIRMED", mime_type="application/json")
        else:
            raise ValueError("unsupported preview data type")
        if result.status not in {"COMPLETED", "PARTIAL"} or result.accepted_rows < 1:
            self.uploads.update_document_status(document_id, store_id=store_id, status="FAILED", error_code="IMPORT_FAILED", error_message=result.error_message or "确认后导入失败")
            return ImportOutcome("FAILED", result.import_batch_id, None, result.accepted_rows, result.rejected_rows, result.error_message)
        self._link_batch(document_id, doc, result.import_batch_id)
        self.uploads.update_document_status(document_id, store_id=store_id, status="IMPORTED", business_date=business_date_hint, report_scope=report_scope)
        return ImportOutcome("IMPORTED", result.import_batch_id, None, result.accepted_rows, result.rejected_rows, result.error_message)

    def _import_image(self, document_id: int, store_id: int, doc: dict[str, Any], duplicate_decision: str | None = None) -> ImportOutcome:
        job = self.uploads.get_latest_extraction_job(document_id)
        if job is None or job["status"] != "NEEDS_CONFIRMATION":
            raise ValueError("图片识别结果还不能确认")
        template = get_template_by_code(job.get("template_code") or "")
        if template is None:
            raise ValueError("当前平台/页面还没有可确认导入的模板")
        fields = self.uploads.list_extracted_fields(int(job["id"]))
        values: dict[str, str] = {}
        for field in fields:
            if field.get("excluded_by_user"):
                continue
            raw = field.get("confirmed_value") if field.get("user_corrected") else field.get("normalized_value")
            normalized = normalize_confirmed_field(field["field_code"], raw)
            if normalized is not None:
                values[field["field_code"]] = normalized
        missing = [f for f in template.required_fields if not values.get(f)]
        if missing:
            raise ValueError("确认前还缺少必要字段：" + ", ".join(missing))
        if template.data_type != "SALES":
            raise ValueError("当前图片模板的数据类型暂未开放确认导入")
        business_date = values["BUSINESS_DATE"]
        gross_sales = values["GROSS_SALES"]
        if template.report_scope == "WHOLE_STORE":
            order_count = int(values["ORDER_COUNT"]) if values.get("ORDER_COUNT") else None
            saved = MiniSalesRepository(self.db).upsert_store_total(
                store_id, business_date, gross_sales, "MINI_OCR_CONFIRMED", f"document:{document_id}",
                order_count=order_count, source_document_id=document_id,
            )
            self.uploads.add_import_link(document_id=document_id, confirmed_by_user_id=int(doc["user_id"]), whole_store_record_id=int(saved["id"]))
            self.uploads.update_document_status(document_id, store_id=store_id, status="IMPORTED", business_date=business_date, report_scope="WHOLE_STORE")
            return ImportOutcome("IMPORTED", None, int(saved["id"]), 1, 0)
        if not template.channel_code:
            raise ValueError("模板缺少渠道定义")
        # Existing same-store/day/channel data is never silently replaced unless identity evidence proves UPDATE.
        with self.db.compat_connect() as con:
            existing = con.execute(
                """SELECT s.gross_sales FROM daily_channel_sales s JOIN channels c ON c.id=s.channel_id
                   WHERE s.store_id=? AND s.business_date=? AND c.code=?""",
                (store_id, business_date, template.channel_code),
            ).fetchone()
        if existing is not None and duplicate_decision != "REPLACE_EXISTING":
            candidate = ReportIdentity(store_id, template.channel_code, business_date, template.code, template.report_scope, None, None, None, template.cumulative, gross_sales)
            previous = ReportIdentity(store_id, template.channel_code, business_date, template.code, template.report_scope, None, None, None, template.cumulative, str(existing["gross_sales"]))
            classification = DuplicateDetectionService().classify(candidate, previous)
            if classification in {"POSSIBLE_DUPLICATE", "CONFLICTING_SCOPE"}:
                raise DuplicateDecisionRequired(classification, ["REPLACE_EXISTING"])
        if duplicate_decision not in {None, "REPLACE_EXISTING"}:
            raise ValueError("当前日报口径不能作为独立第二条记录累计")
        row = {"business_date": business_date, "channel_code": template.channel_code, "gross_sales": gross_sales}
        for code, key in _FIELD_TO_SALES.items():
            if code in {"BUSINESS_DATE", "GROSS_SALES"}: continue
            if code in values:
                row[key] = int(values[code]) if code in _COUNT_FIELDS else values[code]
        canonical=json.dumps([row],ensure_ascii=False,separators=(",", ":")).encode("utf-8")
        result=import_daily_sales(self.db,store_id,f"mini_ocr_{document_id}.json",canonical,"MINI_OCR_CONFIRMED",channel_code=template.channel_code,mime_type="application/json")
        if result.status not in {"COMPLETED","PARTIAL"} or result.accepted_rows < 1:
            raise ValueError(result.error_message or "确认后的截图数据未能导入")
        self._link_batch(document_id,doc,result.import_batch_id)
        # A confirmed daily-report PLATFORM_FEE is daily-attributable evidence. Keep it as OTHER unless a future template proves a more specific fee type.
        if values.get("PLATFORM_FEE"):
            with self.db.compat_connect() as con:
                channel_id=con.execute("SELECT id FROM channels WHERE code=?",(template.channel_code,)).fetchone()["id"]
                sale=con.execute("SELECT raw_import_row_id,source_file_id FROM daily_channel_sales WHERE store_id=? AND business_date=? AND channel_id=?",(store_id,business_date,channel_id)).fetchone()
                con.execute("DELETE FROM platform_fees WHERE store_id=? AND channel_id=? AND business_date=? AND fee_type='OTHER' AND description='平台费用（截图确认）'",(store_id,channel_id,business_date))
                con.execute("""INSERT INTO platform_fees(store_id,channel_id,business_date,settlement_id,fee_type,amount,description,source_file_id,import_batch_id,raw_import_row_id,created_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (store_id,channel_id,business_date,None,"OTHER",values["PLATFORM_FEE"],"平台费用（截图确认）",sale["source_file_id"],result.import_batch_id,sale["raw_import_row_id"],int(time.time()*1000)))
                con.commit()
        self.uploads.update_document_status(document_id,store_id=store_id,status="IMPORTED",business_date=business_date,report_scope=template.report_scope)
        return ImportOutcome("IMPORTED",result.import_batch_id,None,result.accepted_rows,result.rejected_rows,result.error_message)

    def import_confirmed_document(self, document_id: int, store_id: int, duplicate_decision: str | None = None) -> ImportOutcome:
        doc=self.uploads.get_document(document_id,store_id=store_id)
        if doc is None: raise KeyError("document not found")
        if doc["status"] not in {"NEEDS_CONFIRMATION","CONFIRMED"}: raise ValueError("document is not ready for confirmation")
        if doc["document_type"] == "FILE": return self._import_file(document_id,store_id,doc)
        if doc["document_type"] == "IMAGE": return self._import_image(document_id,store_id,doc,duplicate_decision=duplicate_decision)
        raise ValueError("unsupported document type")
