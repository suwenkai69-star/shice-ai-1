from __future__ import annotations

import csv
import base64
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from db_v2 import connect_v2, money, money_text
from db import migrate_database
from persistence.database import Database, DatabaseTarget, coerce_database

def _ensure_database(target: DatabaseTarget) -> Database:
    if isinstance(target, Database):
        return target
    migrate_database(target)
    return coerce_database(target)


def _connect_target(target: DatabaseTarget):
    return _ensure_database(target).compat_connect()


PARSER_VERSION = "v2.0.0"

SALES_ALIASES = {
    "business_date": ["business_date", "营业日期", "日期", "业务日期"],
    "channel_code": ["channel_code", "渠道代码", "渠道", "channel"],
    "gross_sales": ["gross_sales", "销售额", "营业额", "原价销售额"],
    "customer_paid": ["customer_paid", "顾客实付", "实付金额", "用户实付"],
    "order_count": ["order_count", "订单数", "订单量"],
    "refund_amount": ["refund_amount", "退款金额"],
    "refund_count": ["refund_count", "退款笔数", "退款单数"],
    "merchant_discount": ["merchant_discount", "商家优惠", "商户优惠"],
    "platform_subsidy": ["platform_subsidy", "平台补贴", "平台优惠"],
}
KNOWN_SALES_HEADERS = {x for names in SALES_ALIASES.values() for x in names}

@dataclass
class ImportResult:
    import_batch_id: int
    row_count: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int
    updated_rows: int
    status: str
    unknown_fields: list[str]
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decode_text(data: bytes) -> str:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
    raise ValueError("文件编码无法识别，请使用UTF-8或GB18030编码")


def _parse_rows(filename: str, data: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    if filename.lower().endswith(".json"):
        payload = json.loads(_decode_text(data))
        rows = payload if isinstance(payload, list) else [payload]
        rows = [dict(r) for r in rows if isinstance(r, dict)]
        headers: list[str] = []
        for row in rows:
            for key in row:
                if key not in headers:
                    headers.append(key)
        return rows, headers
    text = _decode_text(data)
    reader = csv.DictReader(io.StringIO(text))
    return list(reader), list(reader.fieldnames or [])


def _pick(row: dict[str, Any], aliases: Iterable[str]) -> Any:
    for name in aliases:
        if name in row and row[name] is not None and str(row[name]).strip() != "":
            return row[name]
    return None


def _money_optional(value: Any, label: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return money_text(value)
    except ValueError as exc:
        raise ValueError(f"{label}不是有效金额") from exc



def _money_magnitude_optional(value: Any, label: str) -> str | None:
    normalized = _money_optional(value, label)
    if normalized is None:
        return None
    return money_text(abs(money(normalized)))

def _int_optional(value: Any, label: str) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        d = Decimal(str(value).replace(",", "").strip())
        if d != d.to_integral_value():
            raise ValueError
        return int(d)
    except Exception as exc:
        raise ValueError(f"{label}不是有效整数") from exc


def _date_text(value: Any, label: str) -> str:
    if value is None or str(value).strip() == "":
        raise ValueError(f"缺少{label}")
    text = str(value).strip().replace("/", "-")
    parts = text.split("-")
    try:
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2])).isoformat()
        return date.fromisoformat(text).isoformat()
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label}格式应为YYYY-MM-DD") from exc


def _resolve_channel_code(row: dict[str, Any], aliases: Iterable[str], default_channel_code: str | None) -> str:
    row_value = _pick(row, aliases)
    row_code = str(row_value).strip() if row_value is not None else ""
    default_code = str(default_channel_code or "").strip()
    if row_code and default_code and row_code != default_code:
        raise ValueError(f"文件渠道{row_code}与本次指定渠道{default_code}不一致，请确认后重新导入")
    code = row_code or default_code
    if not code:
        raise ValueError("缺少渠道代码")
    return code


def _channel_id(con: sqlite3.Connection, code: str) -> int:
    row = con.execute("SELECT id FROM channels WHERE code=? AND enabled=1", (code,)).fetchone()
    if not row:
        raise ValueError(f"无法识别渠道代码：{code}")
    return int(row[0])


def _ensure_import_file(con: sqlite3.Connection, store_id: int, filename: str, data: bytes, mime_type: str | None) -> tuple[int, str]:
    digest = hashlib.sha256(data).hexdigest()
    now = int(time.time() * 1000)
    con.execute(
        "INSERT OR IGNORE INTO import_files(store_id,file_name,file_hash,file_size,mime_type,created_at) VALUES(?,?,?,?,?,?)",
        (store_id, filename, digest, len(data), mime_type, now),
    )
    row = con.execute("SELECT id FROM import_files WHERE store_id=? AND file_hash=?", (store_id, digest)).fetchone()
    return int(row[0]), digest


def _create_batch(con: sqlite3.Connection, store_id: int, source_file_id: int, filename: str, source_type: str, channel_id: int | None, row_count: int, file_hash: str) -> int:
    now = int(time.time() * 1000)
    cur = con.execute(
        """INSERT INTO import_batches(
           store_id,source_file_id,file_name,source_type,channel_id,imported_at,row_count,
           accepted_rows,rejected_rows,duplicate_rows,updated_rows,parser_version,status,error_message,file_hash,unknown_fields_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (store_id, source_file_id, filename, source_type, channel_id, now, row_count, 0, 0, 0, 0, PARSER_VERSION, "PROCESSING", None, file_hash, "[]"),
    )
    return int(cur.lastrowid)


def _finish_batch(
    con: sqlite3.Connection,
    batch_id: int,
    accepted: int,
    rejected: int,
    duplicates: int,
    updated: int,
    unknown: list[str],
    error_message: str | None = None,
    row_count: int | None = None,
) -> str:
    if row_count == 0:
        status = "FAILED"
    elif rejected and not accepted and not duplicates:
        status = "FAILED"
    elif rejected:
        status = "PARTIAL"
    else:
        status = "COMPLETED"
    con.execute(
        "UPDATE import_batches SET accepted_rows=?,rejected_rows=?,duplicate_rows=?,updated_rows=?,status=?,error_message=?,unknown_fields_json=? WHERE id=?",
        (accepted, rejected, duplicates, updated, status, error_message, json.dumps(unknown, ensure_ascii=False), batch_id),
    )
    return status


def _normalize_sales(row: dict[str, Any], default_channel_code: str | None) -> dict[str, Any]:
    channel_code = _resolve_channel_code(row, SALES_ALIASES["channel_code"], default_channel_code)
    gross_raw = _pick(row, SALES_ALIASES["gross_sales"])
    if gross_raw is None:
        raise ValueError("缺少销售额")
    return {
        "business_date": _date_text(_pick(row, SALES_ALIASES["business_date"]), "营业日期"),
        "channel_code": channel_code,
        "gross_sales": _money_optional(gross_raw, "销售额"),
        "customer_paid": _money_optional(_pick(row, SALES_ALIASES["customer_paid"]), "顾客实付"),
        "order_count": _int_optional(_pick(row, SALES_ALIASES["order_count"]), "订单数"),
        "refund_amount": _money_magnitude_optional(_pick(row, SALES_ALIASES["refund_amount"]), "退款金额"),
        "refund_count": _int_optional(_pick(row, SALES_ALIASES["refund_count"]), "退款笔数"),
        "merchant_discount": _money_magnitude_optional(_pick(row, SALES_ALIASES["merchant_discount"]), "商家优惠"),
        "platform_subsidy": _money_magnitude_optional(_pick(row, SALES_ALIASES["platform_subsidy"]), "平台补贴"),
    }


def _same_sale(row: sqlite3.Row, norm: dict[str, Any]) -> bool:
    fields = ["gross_sales", "customer_paid", "order_count", "refund_amount", "refund_count", "merchant_discount", "platform_subsidy"]
    return all(row[f] == norm[f] for f in fields)


def _reject_partial_overwrite(existing: sqlite3.Row, norm: dict[str, Any], fields: Iterable[str]) -> None:
    missing: list[str] = []
    for field in fields:
        if existing[field] is not None and norm.get(field) is None:
            missing.append(field)
    if missing:
        raise ValueError(
            "修正版字段不完整，为避免覆盖已有数据，本次未更新；请提供完整记录。缺少："
            + ", ".join(missing)
        )


def import_daily_sales(
    db_path: DatabaseTarget,
    store_id: int,
    filename: str,
    data: bytes,
    source_type: str,
    channel_code: str | None = None,
    mime_type: str | None = "text/csv",
) -> ImportResult:
    _ensure_database(db_path)
    con = _connect_target(db_path)
    try:
        con.execute("BEGIN")
        batch_channel_id = _channel_id(con, channel_code) if channel_code else None
        source_file_id, digest = _ensure_import_file(con, store_id, filename, data, mime_type)
        batch_id = _create_batch(con, store_id, source_file_id, filename, source_type, batch_channel_id, 0, digest)
        try:
            rows, headers = _parse_rows(filename, data)
        except Exception as exc:
            message = f"文件解析失败：{exc}"
            con.execute("UPDATE import_batches SET row_count=0 WHERE id=?", (batch_id,))
            status = _finish_batch(con, batch_id, 0, 0, 0, 0, [], message, row_count=0)
            con.commit()
            return ImportResult(batch_id, 0, 0, 0, 0, 0, status, [], message)
        unknown = [h for h in headers if h and h not in KNOWN_SALES_HEADERS]
        con.execute("UPDATE import_batches SET row_count=? WHERE id=?", (len(rows), batch_id))
        if not rows:
            message = "文件里没有识别到可导入的销售数据，请检查文件内容"
            status = _finish_batch(con, batch_id, 0, 0, 0, 0, unknown, message, row_count=0)
            con.commit()
            return ImportResult(batch_id, 0, 0, 0, 0, 0, status, unknown, message)
        accepted = rejected = duplicates = updated = 0
        errors: list[str] = []
        now = int(time.time() * 1000)

        for row_number, row in enumerate(rows, start=1):
            raw_cur = con.execute(
                "INSERT INTO raw_import_rows(import_batch_id,row_number,raw_payload_json,parse_status,error_message,created_at) VALUES(?,?,?,?,?,?)",
                (batch_id, row_number, json.dumps(row, ensure_ascii=False), "PENDING", None, now),
            )
            raw_id = int(raw_cur.lastrowid)
            try:
                norm = _normalize_sales(row, channel_code)
                ch_id = _channel_id(con, norm["channel_code"])
                existing = con.execute(
                    "SELECT * FROM daily_channel_sales WHERE store_id=? AND business_date=? AND channel_id=?",
                    (store_id, norm["business_date"], ch_id),
                ).fetchone()
                if existing:
                    _reject_partial_overwrite(
                        existing,
                        norm,
                        ["customer_paid", "order_count", "refund_amount", "refund_count", "merchant_discount", "platform_subsidy"],
                    )
                if existing and _same_sale(existing, norm):
                    duplicates += 1
                    con.execute("UPDATE raw_import_rows SET parse_status='DUPLICATE' WHERE id=?", (raw_id,))
                    continue
                if existing:
                    con.execute(
                        """UPDATE daily_channel_sales SET gross_sales=?,customer_paid=?,order_count=?,refund_amount=?,refund_count=?,
                           merchant_discount=?,platform_subsidy=?,source=?,source_file_id=?,import_batch_id=?,raw_import_row_id=?,updated_at=? WHERE id=?""",
                        (norm["gross_sales"], norm["customer_paid"], norm["order_count"], norm["refund_amount"], norm["refund_count"],
                         norm["merchant_discount"], norm["platform_subsidy"], source_type, source_file_id, batch_id, raw_id, now, existing["id"]),
                    )
                    updated += 1
                    accepted += 1
                    con.execute("UPDATE raw_import_rows SET parse_status='UPDATED' WHERE id=?", (raw_id,))
                else:
                    con.execute(
                        """INSERT INTO daily_channel_sales(store_id,business_date,channel_id,gross_sales,customer_paid,order_count,
                           refund_amount,refund_count,merchant_discount,platform_subsidy,source,source_file_id,import_batch_id,raw_import_row_id,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (store_id, norm["business_date"], ch_id, norm["gross_sales"], norm["customer_paid"], norm["order_count"],
                         norm["refund_amount"], norm["refund_count"], norm["merchant_discount"], norm["platform_subsidy"], source_type,
                         source_file_id, batch_id, raw_id, now, now),
                    )
                    accepted += 1
                    con.execute("UPDATE raw_import_rows SET parse_status='ACCEPTED' WHERE id=?", (raw_id,))
            except Exception as exc:
                rejected += 1
                message = str(exc)
                errors.append(f"第{row_number}行：{message}")
                con.execute("UPDATE raw_import_rows SET parse_status='REJECTED',error_message=? WHERE id=?", (message, raw_id))

        status = _finish_batch(
            con, batch_id, accepted, rejected, duplicates, updated, unknown,
            "; ".join(errors) if errors else None, row_count=len(rows)
        )
        con.commit()
        return ImportResult(batch_id, len(rows), accepted, rejected, duplicates, updated, status, unknown, "; ".join(errors) if errors else None)
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def list_daily_sales(db_path: DatabaseTarget, store_id: int, start: str | None = None, end: str | None = None, channel_code: str | None = None) -> list[dict[str, Any]]:
    _ensure_database(db_path)
    sql = """SELECT s.*, c.code AS channel_code, c.name AS channel_name, c.category AS channel_category
             FROM daily_channel_sales s JOIN channels c ON c.id=s.channel_id WHERE s.store_id=?"""
    params: list[Any] = [store_id]
    if start:
        sql += " AND s.business_date>=?"; params.append(start)
    if end:
        sql += " AND s.business_date<=?"; params.append(end)
    if channel_code:
        sql += " AND c.code=?"; params.append(channel_code)
    sql += " ORDER BY s.business_date,c.code"
    with _connect_target(db_path) as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def list_import_batches(db_path: DatabaseTarget, store_id: int) -> list[dict[str, Any]]:
    _ensure_database(db_path)
    with _connect_target(db_path) as con:
        rows = con.execute("SELECT * FROM import_batches WHERE store_id=? ORDER BY id", (store_id,)).fetchall()
    out=[]
    for row in rows:
        item=dict(row)
        try: item["unknown_fields"] = json.loads(item.pop("unknown_fields_json"))
        except Exception: item["unknown_fields"] = []
        out.append(item)
    return out


def get_sales_trace(db_path: DatabaseTarget, sale_id: int) -> dict[str, Any]:
    _ensure_database(db_path)
    with _connect_target(db_path) as con:
        sale = con.execute("SELECT * FROM daily_channel_sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            raise KeyError(f"sale {sale_id} not found")
        raw = con.execute("SELECT * FROM raw_import_rows WHERE id=?", (sale["raw_import_row_id"],)).fetchone() if sale["raw_import_row_id"] else None
        batch = con.execute("SELECT * FROM import_batches WHERE id=?", (sale["import_batch_id"],)).fetchone() if sale["import_batch_id"] else None
        file_row = con.execute("SELECT * FROM import_files WHERE id=?", (sale["source_file_id"],)).fetchone() if sale["source_file_id"] else None
    return {"sale": dict(sale), "raw_row": dict(raw) if raw else None, "batch": dict(batch) if batch else None, "file": dict(file_row) if file_row else None}

SETTLEMENT_ALIASES = {
    "channel_code": ["channel_code", "渠道代码", "渠道", "channel"],
    "settlement_period_start": ["settlement_period_start", "结算周期开始", "周期开始"],
    "settlement_period_end": ["settlement_period_end", "结算周期结束", "周期结束"],
    "settlement_date": ["settlement_date", "结算日期"],
    "gross_sales": ["gross_sales", "销售额", "营业额"],
    "customer_paid": ["customer_paid", "顾客实付", "用户实付"],
    "commission_fee": ["commission_fee", "佣金", "平台佣金"],
    "delivery_fee": ["delivery_fee", "配送费"],
    "technical_service_fee": ["technical_service_fee", "技术服务费"],
    "promotion_fee": ["promotion_fee", "推广费", "推广费用"],
    "merchant_discount": ["merchant_discount", "商家优惠", "商户优惠"],
    "refund_amount": ["refund_amount", "退款金额"],
    "other_fee": ["other_fee", "其他费用", "其他费"],
    "platform_subsidy": ["platform_subsidy", "平台补贴", "平台优惠"],
    "reported_expected_settlement": ["reported_expected_settlement", "平台应结算", "应结算", "结算金额"],
    "settlement_reference": ["settlement_reference", "结算单号", "结算参考号"],
    "calculation_profile": ["calculation_profile", "计算口径"],
}
KNOWN_SETTLEMENT_HEADERS = {x for names in SETTLEMENT_ALIASES.values() for x in names}

PAYMENT_ALIASES = {
    "channel_code": ["channel_code", "渠道代码", "渠道", "channel"],
    "payment_date": ["payment_date", "到账日期", "入账日期"],
    "amount": ["amount", "到账金额", "入账金额", "金额"],
    "payment_method": ["payment_method", "到账方式", "收款方式"],
    "bank_reference": ["bank_reference", "银行流水号", "银行参考号", "流水号"],
    "settlement_reference": ["settlement_reference", "结算单号", "结算参考号"],
    "source": ["source", "来源"],
}
KNOWN_PAYMENT_HEADERS = {x for names in PAYMENT_ALIASES.values() for x in names}

FEE_FIELD_MAP = (
    ("commission_fee", "COMMISSION", "平台佣金"),
    ("delivery_fee", "DELIVERY", "配送相关费用"),
    ("technical_service_fee", "TECH_SERVICE", "技术服务费"),
    ("promotion_fee", "PROMOTION", "推广费用"),
    ("merchant_discount", "MERCHANT_DISCOUNT", "商家承担优惠"),
    ("refund_amount", "REFUND", "退款"),
    ("other_fee", "OTHER", "其他费用"),
)


def _date_optional(value: Any, label: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return _date_text(value, label)


def _text_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _stable_hash(*parts: Any) -> str:
    payload = "\x1f".join("" if p is None else str(p).strip() for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_settlement(row: dict[str, Any], default_channel_code: str | None) -> dict[str, Any]:
    channel_code = _resolve_channel_code(row, SETTLEMENT_ALIASES["channel_code"], default_channel_code)
    out = {
        "channel_code": channel_code,
        "settlement_period_start": _date_optional(_pick(row, SETTLEMENT_ALIASES["settlement_period_start"]), "结算周期开始"),
        "settlement_period_end": _date_optional(_pick(row, SETTLEMENT_ALIASES["settlement_period_end"]), "结算周期结束"),
        "settlement_date": _date_optional(_pick(row, SETTLEMENT_ALIASES["settlement_date"]), "结算日期"),
        "settlement_reference": _text_optional(_pick(row, SETTLEMENT_ALIASES["settlement_reference"])),
        "calculation_profile": _text_optional(_pick(row, SETTLEMENT_ALIASES["calculation_profile"])),
    }
    magnitude_fields = {"commission_fee", "delivery_fee", "technical_service_fee", "promotion_fee", "merchant_discount", "refund_amount", "other_fee", "platform_subsidy"}
    for field, label in (
        ("gross_sales", "销售额"), ("customer_paid", "顾客实付"), ("commission_fee", "佣金"),
        ("delivery_fee", "配送费"), ("technical_service_fee", "技术服务费"), ("promotion_fee", "推广费"),
        ("merchant_discount", "商家优惠"), ("refund_amount", "退款金额"), ("other_fee", "其他费用"),
        ("platform_subsidy", "平台补贴"), ("reported_expected_settlement", "平台应结算"),
    ):
        parser = _money_magnitude_optional if field in magnitude_fields else _money_optional
        out[field] = parser(_pick(row, SETTLEMENT_ALIASES[field]), label)
    if not any((out["settlement_reference"], out["settlement_date"], out["settlement_period_start"], out["settlement_period_end"])):
        raise ValueError("缺少结算单号或结算日期/周期")
    return out


def _settlement_fingerprint(store_id: int, channel_id: int, norm: dict[str, Any]) -> str:
    if norm["settlement_reference"]:
        return _stable_hash("settlement-ref", store_id, channel_id, norm["settlement_reference"])
    return _stable_hash(
        "settlement-fallback", store_id, channel_id, norm["settlement_period_start"],
        norm["settlement_period_end"], norm["settlement_date"]
    )


def _same_record(row: sqlite3.Row, norm: dict[str, Any], fields: Iterable[str]) -> bool:
    return all(row[field] == norm.get(field) for field in fields)


def _replace_platform_fees(
    con: sqlite3.Connection,
    store_id: int,
    channel_id: int,
    settlement_id: int,
    norm: dict[str, Any],
    source_file_id: int,
    batch_id: int,
    raw_id: int,
    now: int,
) -> None:
    con.execute("DELETE FROM platform_fees WHERE settlement_id=?", (settlement_id,))
    # A settlement row usually represents a period, not a single business day.
    # Do not fabricate daily fee timing from settlement_period_end/settlement_date.
    business_date = None
    for field, fee_type, description in FEE_FIELD_MAP:
        value = norm.get(field)
        if value is None or money(value) == Decimal("0.00"):
            continue
        con.execute(
            """INSERT INTO platform_fees(store_id,channel_id,business_date,settlement_id,fee_type,amount,description,
               source_file_id,import_batch_id,raw_import_row_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (store_id, channel_id, business_date, settlement_id, fee_type, value, description, source_file_id, batch_id, raw_id, now),
        )


def import_settlements(
    db_path: DatabaseTarget,
    store_id: int,
    filename: str,
    data: bytes,
    source_type: str,
    channel_code: str | None = None,
    mime_type: str | None = "text/csv",
) -> ImportResult:
    _ensure_database(db_path)
    con = _connect_target(db_path)
    try:
        con.execute("BEGIN")
        batch_channel_id = _channel_id(con, channel_code) if channel_code else None
        source_file_id, digest = _ensure_import_file(con, store_id, filename, data, mime_type)
        batch_id = _create_batch(con, store_id, source_file_id, filename, source_type, batch_channel_id, 0, digest)
        try:
            rows, headers = _parse_rows(filename, data)
        except Exception as exc:
            message = f"文件解析失败：{exc}"
            status = _finish_batch(con, batch_id, 0, 0, 0, 0, [], message, row_count=0)
            con.commit()
            return ImportResult(batch_id, 0, 0, 0, 0, 0, status, [], message)
        unknown = [h for h in headers if h and h not in KNOWN_SETTLEMENT_HEADERS]
        con.execute("UPDATE import_batches SET row_count=? WHERE id=?", (len(rows), batch_id))
        if not rows:
            message = "文件里没有识别到可导入的结算数据，请检查文件内容"
            status = _finish_batch(con, batch_id, 0, 0, 0, 0, unknown, message, row_count=0)
            con.commit()
            return ImportResult(batch_id, 0, 0, 0, 0, 0, status, unknown, message)
        accepted = rejected = duplicates = updated = 0
        errors: list[str] = []
        now = int(time.time() * 1000)
        compare_fields = [
            "settlement_period_start", "settlement_period_end", "settlement_date", "gross_sales", "customer_paid",
            "commission_fee", "delivery_fee", "technical_service_fee", "promotion_fee", "merchant_discount",
            "refund_amount", "other_fee", "platform_subsidy", "reported_expected_settlement", "settlement_reference",
            "calculation_profile", "calculated_expected_settlement", "calculation_version",
        ]
        for row_number, row in enumerate(rows, start=1):
            raw_cur = con.execute(
                "INSERT INTO raw_import_rows(import_batch_id,row_number,raw_payload_json,parse_status,error_message,created_at) VALUES(?,?,?,?,?,?)",
                (batch_id, row_number, json.dumps(row, ensure_ascii=False), "PENDING", None, now),
            )
            raw_id = int(raw_cur.lastrowid)
            try:
                norm = _normalize_settlement(row, channel_code)
                calc = calculate_expected_settlement(norm, norm.get("calculation_profile"))
                norm["calculated_expected_settlement"] = money_text(calc) if calc is not None else None
                norm["calculation_version"] = CALCULATION_VERSION if calc is not None else None
                ch_id = _channel_id(con, norm["channel_code"])
                fingerprint = _settlement_fingerprint(store_id, ch_id, norm)
                existing = con.execute("SELECT * FROM settlements WHERE settlement_fingerprint=?", (fingerprint,)).fetchone()
                if existing:
                    _reject_partial_overwrite(
                        existing,
                        norm,
                        [
                            "settlement_period_start", "settlement_period_end", "settlement_date", "gross_sales", "customer_paid",
                            "commission_fee", "delivery_fee", "technical_service_fee", "promotion_fee", "merchant_discount",
                            "refund_amount", "other_fee", "platform_subsidy", "reported_expected_settlement", "calculation_profile",
                        ],
                    )
                if existing and _same_record(existing, norm, compare_fields):
                    duplicates += 1
                    con.execute("UPDATE raw_import_rows SET parse_status='DUPLICATE' WHERE id=?", (raw_id,))
                    continue
                if existing:
                    con.execute(
                        """UPDATE settlements SET settlement_period_start=?,settlement_period_end=?,settlement_date=?,gross_sales=?,customer_paid=?,
                           commission_fee=?,delivery_fee=?,technical_service_fee=?,promotion_fee=?,merchant_discount=?,refund_amount=?,other_fee=?,platform_subsidy=?,
                           reported_expected_settlement=?,calculation_profile=?,calculation_version=?,calculated_expected_settlement=?,settlement_reference=?,
                           source_file_id=?,import_batch_id=?,raw_import_row_id=?,updated_at=? WHERE id=?""",
                        (norm["settlement_period_start"], norm["settlement_period_end"], norm["settlement_date"], norm["gross_sales"], norm["customer_paid"],
                         norm["commission_fee"], norm["delivery_fee"], norm["technical_service_fee"], norm["promotion_fee"], norm["merchant_discount"],
                         norm["refund_amount"], norm["other_fee"], norm["platform_subsidy"], norm["reported_expected_settlement"], norm["calculation_profile"],
                         norm["calculation_version"], norm["calculated_expected_settlement"], norm["settlement_reference"], source_file_id, batch_id, raw_id, now, existing["id"]),
                    )
                    settlement_id = int(existing["id"])
                    updated += 1; accepted += 1
                    con.execute("UPDATE raw_import_rows SET parse_status='UPDATED' WHERE id=?", (raw_id,))
                else:
                    cur = con.execute(
                        """INSERT INTO settlements(store_id,channel_id,settlement_period_start,settlement_period_end,settlement_date,gross_sales,customer_paid,
                           commission_fee,delivery_fee,technical_service_fee,promotion_fee,merchant_discount,refund_amount,other_fee,platform_subsidy,
                           reported_expected_settlement,calculated_expected_settlement,calculation_profile,calculation_version,settlement_reference,settlement_fingerprint,
                           source_file_id,import_batch_id,raw_import_row_id,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (store_id, ch_id, norm["settlement_period_start"], norm["settlement_period_end"], norm["settlement_date"], norm["gross_sales"], norm["customer_paid"],
                         norm["commission_fee"], norm["delivery_fee"], norm["technical_service_fee"], norm["promotion_fee"], norm["merchant_discount"], norm["refund_amount"],
                         norm["other_fee"], norm["platform_subsidy"], norm["reported_expected_settlement"], norm["calculated_expected_settlement"], norm["calculation_profile"], norm["calculation_version"],
                         norm["settlement_reference"], fingerprint, source_file_id, batch_id, raw_id, now, now),
                    )
                    settlement_id = int(cur.lastrowid)
                    accepted += 1
                    con.execute("UPDATE raw_import_rows SET parse_status='ACCEPTED' WHERE id=?", (raw_id,))
                _replace_platform_fees(con, store_id, ch_id, settlement_id, norm, source_file_id, batch_id, raw_id, now)
            except Exception as exc:
                rejected += 1
                message = str(exc); errors.append(f"第{row_number}行：{message}")
                con.execute("UPDATE raw_import_rows SET parse_status='REJECTED',error_message=? WHERE id=?", (message, raw_id))
        status = _finish_batch(
            con, batch_id, accepted, rejected, duplicates, updated, unknown,
            "; ".join(errors) if errors else None, row_count=len(rows)
        )
        con.commit()
        return ImportResult(batch_id, len(rows), accepted, rejected, duplicates, updated, status, unknown, "; ".join(errors) if errors else None)
    except Exception:
        con.rollback(); raise
    finally:
        con.close()


def list_settlements(db_path: DatabaseTarget, store_id: int) -> list[dict[str, Any]]:
    _ensure_database(db_path)
    with _connect_target(db_path) as con:
        rows = con.execute(
            "SELECT s.*,c.code AS channel_code,c.name AS channel_name FROM settlements s JOIN channels c ON c.id=s.channel_id WHERE s.store_id=? ORDER BY COALESCE(s.settlement_date,s.settlement_period_end),s.id",
            (store_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_platform_fees(db_path: DatabaseTarget, store_id: int) -> list[dict[str, Any]]:
    _ensure_database(db_path)
    with _connect_target(db_path) as con:
        rows = con.execute(
            "SELECT f.*,c.code AS channel_code FROM platform_fees f JOIN channels c ON c.id=f.channel_id WHERE f.store_id=? ORDER BY f.id",
            (store_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _normalize_payment(row: dict[str, Any], default_channel_code: str | None, default_source: str) -> dict[str, Any]:
    channel_code = _resolve_channel_code(row, PAYMENT_ALIASES["channel_code"], default_channel_code)
    amount_raw = _pick(row, PAYMENT_ALIASES["amount"])
    if amount_raw is None:
        raise ValueError("缺少到账金额")
    return {
        "channel_code": channel_code,
        "payment_date": _date_text(_pick(row, PAYMENT_ALIASES["payment_date"]), "到账日期"),
        "amount": _money_optional(amount_raw, "到账金额"),
        "payment_method": _text_optional(_pick(row, PAYMENT_ALIASES["payment_method"])),
        "bank_reference": _text_optional(_pick(row, PAYMENT_ALIASES["bank_reference"])),
        "settlement_reference": _text_optional(_pick(row, PAYMENT_ALIASES["settlement_reference"])),
        "source": _text_optional(_pick(row, PAYMENT_ALIASES["source"])) or default_source,
    }


def _payment_fingerprint(store_id: int, channel_id: int, norm: dict[str, Any]) -> str:
    if norm["bank_reference"]:
        return _stable_hash("payment-bank-ref", store_id, channel_id, norm["bank_reference"])
    return _stable_hash(
        "payment-fallback", store_id, channel_id, norm["payment_date"], norm["amount"],
        norm["settlement_reference"], norm["source"]
    )


def import_payments(
    db_path: DatabaseTarget,
    store_id: int,
    filename: str,
    data: bytes,
    source_type: str,
    channel_code: str | None = None,
    mime_type: str | None = "text/csv",
) -> ImportResult:
    _ensure_database(db_path)
    con = _connect_target(db_path)
    try:
        con.execute("BEGIN")
        batch_channel_id = _channel_id(con, channel_code) if channel_code else None
        source_file_id, digest = _ensure_import_file(con, store_id, filename, data, mime_type)
        batch_id = _create_batch(con, store_id, source_file_id, filename, source_type, batch_channel_id, 0, digest)
        try:
            rows, headers = _parse_rows(filename, data)
        except Exception as exc:
            message = f"文件解析失败：{exc}"
            status = _finish_batch(con, batch_id, 0, 0, 0, 0, [], message, row_count=0)
            con.commit()
            return ImportResult(batch_id, 0, 0, 0, 0, 0, status, [], message)
        unknown = [h for h in headers if h and h not in KNOWN_PAYMENT_HEADERS]
        con.execute("UPDATE import_batches SET row_count=? WHERE id=?", (len(rows), batch_id))
        if not rows:
            message = "文件里没有识别到可导入的到账数据，请检查文件内容"
            status = _finish_batch(con, batch_id, 0, 0, 0, 0, unknown, message, row_count=0)
            con.commit()
            return ImportResult(batch_id, 0, 0, 0, 0, 0, status, unknown, message)
        accepted = rejected = duplicates = updated = 0
        errors: list[str] = []
        now = int(time.time() * 1000)
        compare_fields = ["payment_date", "amount", "payment_method", "bank_reference", "settlement_reference", "source"]
        for row_number, row in enumerate(rows, start=1):
            raw_cur = con.execute(
                "INSERT INTO raw_import_rows(import_batch_id,row_number,raw_payload_json,parse_status,error_message,created_at) VALUES(?,?,?,?,?,?)",
                (batch_id, row_number, json.dumps(row, ensure_ascii=False), "PENDING", None, now),
            )
            raw_id = int(raw_cur.lastrowid)
            try:
                norm = _normalize_payment(row, channel_code, source_type)
                ch_id = _channel_id(con, norm["channel_code"])
                fingerprint = _payment_fingerprint(store_id, ch_id, norm)
                existing = con.execute("SELECT * FROM payments WHERE payment_fingerprint=?", (fingerprint,)).fetchone()
                if existing:
                    _reject_partial_overwrite(
                        existing,
                        norm,
                        ["payment_method", "bank_reference", "settlement_reference"],
                    )
                if existing and _same_record(existing, norm, compare_fields):
                    duplicates += 1
                    con.execute("UPDATE raw_import_rows SET parse_status='DUPLICATE' WHERE id=?", (raw_id,))
                    continue
                if existing:
                    con.execute(
                        """UPDATE payments SET payment_date=?,amount=?,payment_method=?,bank_reference=?,settlement_reference=?,source=?,
                           source_file_id=?,import_batch_id=?,raw_import_row_id=?,updated_at=? WHERE id=?""",
                        (norm["payment_date"], norm["amount"], norm["payment_method"], norm["bank_reference"], norm["settlement_reference"], norm["source"],
                         source_file_id, batch_id, raw_id, now, existing["id"]),
                    )
                    con.execute(
                        "UPDATE reconciliation_matches SET matched_amount=? WHERE payment_id=? AND match_type='REFERENCE'",
                        (norm["amount"], existing["id"]),
                    )
                    updated += 1; accepted += 1
                    con.execute("UPDATE raw_import_rows SET parse_status='UPDATED' WHERE id=?", (raw_id,))
                else:
                    con.execute(
                        """INSERT INTO payments(store_id,channel_id,payment_date,amount,payment_method,bank_reference,settlement_reference,payment_fingerprint,
                           source,source_file_id,import_batch_id,raw_import_row_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (store_id, ch_id, norm["payment_date"], norm["amount"], norm["payment_method"], norm["bank_reference"], norm["settlement_reference"],
                         fingerprint, norm["source"], source_file_id, batch_id, raw_id, now, now),
                    )
                    accepted += 1
                    con.execute("UPDATE raw_import_rows SET parse_status='ACCEPTED' WHERE id=?", (raw_id,))
            except Exception as exc:
                rejected += 1
                message = str(exc); errors.append(f"第{row_number}行：{message}")
                con.execute("UPDATE raw_import_rows SET parse_status='REJECTED',error_message=? WHERE id=?", (message, raw_id))
        status = _finish_batch(
            con, batch_id, accepted, rejected, duplicates, updated, unknown,
            "; ".join(errors) if errors else None, row_count=len(rows)
        )
        con.commit()
        return ImportResult(batch_id, len(rows), accepted, rejected, duplicates, updated, status, unknown, "; ".join(errors) if errors else None)
    except Exception:
        con.rollback(); raise
    finally:
        con.close()


def list_payments(db_path: DatabaseTarget, store_id: int) -> list[dict[str, Any]]:
    _ensure_database(db_path)
    with _connect_target(db_path) as con:
        rows = con.execute(
            "SELECT p.*,c.code AS channel_code,c.name AS channel_name FROM payments p JOIN channels c ON c.id=p.channel_id WHERE p.store_id=? ORDER BY p.payment_date,p.id",
            (store_id,),
        ).fetchall()
    return [dict(r) for r in rows]

CALCULATION_VERSION = "1"
SUPPORTED_CALCULATION_PROFILES = {
    "CUSTOMER_PAID_SUBSIDY_ADD_V1",
    "CUSTOMER_PAID_SUBSIDY_INCLUDED_V1",
}
SUPPORTED_REVENUE_PROFILES = {
    "CUSTOMER_PAID_V1",
    "CUSTOMER_PAID_PLUS_SUBSIDY_V1",
    "GROSS_SALES_V1",
}


def _money_or_zero(value: Any) -> Decimal:
    if value is None or str(value).strip() == "":
        return Decimal("0.00")
    return money(value)


def calculate_expected_settlement(values: dict[str, Any] | sqlite3.Row, profile: str | None) -> Decimal | None:
    if not profile:
        return None
    if profile not in SUPPORTED_CALCULATION_PROFILES:
        raise ValueError(f"未知结算计算口径：{profile}")
    customer_paid = values["customer_paid"] if "customer_paid" in values.keys() else None
    if customer_paid is None or str(customer_paid).strip() == "":
        return None
    result = money(customer_paid)
    if profile == "CUSTOMER_PAID_SUBSIDY_ADD_V1":
        result += _money_or_zero(values["platform_subsidy"] if "platform_subsidy" in values.keys() else None)
    for field in (
        "commission_fee", "delivery_fee", "technical_service_fee", "promotion_fee",
        "merchant_discount", "refund_amount", "other_fee",
    ):
        result -= _money_or_zero(values[field] if field in values.keys() else None)
    return money(result)


def calculate_channel_net_revenue(values: dict[str, Any] | sqlite3.Row, profile: str | None) -> Decimal | None:
    if not profile:
        return None
    if profile not in SUPPORTED_REVENUE_PROFILES:
        raise ValueError(f"未知渠道收入计算口径：{profile}")
    keys = values.keys()
    if profile == "GROSS_SALES_V1":
        raw = values["gross_sales"] if "gross_sales" in keys else None
        return money(raw) if raw is not None and str(raw).strip() != "" else None
    paid = values["customer_paid"] if "customer_paid" in keys else None
    if paid is None or str(paid).strip() == "":
        return None
    result = money(paid)
    if profile == "CUSTOMER_PAID_PLUS_SUBSIDY_V1":
        result += _money_or_zero(values["platform_subsidy"] if "platform_subsidy" in keys else None)
    return money(result)


def create_reconciliation_match(
    db_path: DatabaseTarget,
    settlement_id: int,
    payment_id: int,
    matched_amount: Any,
    match_type: str = "MANUAL",
    confidence: str | None = None,
) -> dict[str, Any]:
    _ensure_database(db_path)
    amount = money(matched_amount)
    if amount <= 0:
        raise ValueError("匹配金额必须大于0")
    now = int(time.time() * 1000)
    with _connect_target(db_path) as con:
        settlement = con.execute("SELECT id,store_id,channel_id FROM settlements WHERE id=?", (settlement_id,)).fetchone()
        payment = con.execute("SELECT id,store_id,channel_id,amount FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not settlement or not payment:
            raise ValueError("结算或到账记录不存在")
        if settlement["store_id"] != payment["store_id"]:
            raise ValueError("不能跨门店匹配结算与到账")
        if settlement["channel_id"] != payment["channel_id"]:
            raise ValueError("不能跨渠道匹配结算与到账")
        existing_allocated = con.execute(
            "SELECT matched_amount FROM reconciliation_matches WHERE payment_id=? AND settlement_id<>?",
            (payment_id, settlement_id),
        ).fetchall()
        allocated = sum((money(r[0]) for r in existing_allocated), Decimal("0.00"))
        if allocated + amount > money(payment["amount"]):
            raise ValueError("匹配金额超过到账记录可分配金额")
        con.execute(
            """INSERT INTO reconciliation_matches(settlement_id,payment_id,matched_amount,match_type,confidence,created_at)
               VALUES(?,?,?,?,?,?) ON CONFLICT(settlement_id,payment_id) DO UPDATE SET
               matched_amount=excluded.matched_amount,match_type=excluded.match_type,confidence=excluded.confidence""",
            (settlement_id, payment_id, money_text(amount), match_type, confidence, now),
        )
        con.commit()
        row = con.execute("SELECT * FROM reconciliation_matches WHERE settlement_id=? AND payment_id=?", (settlement_id, payment_id)).fetchone()
    return dict(row)


def _auto_match_references(db_path: DatabaseTarget, store_id: int) -> None:
    now = int(time.time() * 1000)
    with _connect_target(db_path) as con:
        payments = con.execute(
            """SELECT p.* FROM payments p
               WHERE p.store_id=? AND p.settlement_reference IS NOT NULL AND TRIM(p.settlement_reference)<>''
               AND NOT EXISTS(SELECT 1 FROM reconciliation_matches m WHERE m.payment_id=p.id)""",
            (store_id,),
        ).fetchall()
        for payment in payments:
            settlement = con.execute(
                "SELECT id FROM settlements WHERE store_id=? AND channel_id=? AND settlement_reference=?",
                (store_id, payment["channel_id"], payment["settlement_reference"]),
            ).fetchone()
            if settlement:
                con.execute(
                    "INSERT OR IGNORE INTO reconciliation_matches(settlement_id,payment_id,matched_amount,match_type,confidence,created_at) VALUES(?,?,?,?,?,?)",
                    (settlement["id"], payment["id"], payment["amount"], "REFERENCE", "HIGH", now),
                )
        con.commit()


def get_reconciliation(db_path: DatabaseTarget, store_id: int, tolerance: Any = "10.00") -> dict[str, Any]:
    _ensure_database(db_path)
    tol = abs(money(tolerance))
    _auto_match_references(db_path, store_id)
    with _connect_target(db_path) as con:
        settlements = con.execute(
            """SELECT s.*,c.code AS channel_code,c.name AS channel_name
               FROM settlements s JOIN channels c ON c.id=s.channel_id
               WHERE s.store_id=? ORDER BY COALESCE(s.settlement_date,s.settlement_period_end),s.id""",
            (store_id,),
        ).fetchall()
        output: list[dict[str, Any]] = []
        for settlement in settlements:
            matches = con.execute(
                """SELECT m.*,p.payment_date,p.amount AS payment_amount,p.bank_reference
                   FROM reconciliation_matches m JOIN payments p ON p.id=m.payment_id
                   WHERE m.settlement_id=? ORDER BY m.id""",
                (settlement["id"],),
            ).fetchall()
            expected_raw = settlement["reported_expected_settlement"] or settlement["calculated_expected_settlement"]
            expected = money(expected_raw) if expected_raw is not None else None
            actual = sum((money(m["matched_amount"]) for m in matches), Decimal("0.00"))
            if not matches:
                status = "MISSING_PAYMENT"
                difference = None if expected is None else Decimal("0.00") - expected
            elif expected is None:
                status = "UNMATCHED"
                difference = None
            else:
                difference = money(actual - expected)
                if difference == Decimal("0.00"):
                    status = "MATCHED"
                elif abs(difference) <= tol:
                    status = "SMALL_DIFFERENCE"
                else:
                    status = "UNMATCHED"
            item = dict(settlement)
            item.update({
                "expected_settlement": money_text(expected) if expected is not None else None,
                "actual_payment": money_text(actual),
                "settlement_difference": money_text(difference) if difference is not None else None,
                "status": status,
                "matches": [dict(m) for m in matches],
            })
            output.append(item)
        unmatched = con.execute(
            """SELECT p.*,c.code AS channel_code,c.name AS channel_name
               FROM payments p JOIN channels c ON c.id=p.channel_id
               WHERE p.store_id=? AND NOT EXISTS(SELECT 1 FROM reconciliation_matches m WHERE m.payment_id=p.id)
               ORDER BY p.payment_date,p.id""",
            (store_id,),
        ).fetchall()
    return {
        "tolerance": money_text(tol),
        "settlements": output,
        "unmatched_payments": [{**dict(p), "status": "MISSING_SETTLEMENT"} for p in unmatched],
    }


def list_channels(db_path: DatabaseTarget, enabled_only: bool = False) -> list[dict[str, Any]]:
    _ensure_database(db_path)
    sql = "SELECT * FROM channels"
    params: tuple[Any, ...] = ()
    if enabled_only:
        sql += " WHERE enabled=1"
    sql += " ORDER BY id"
    with _connect_target(db_path) as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def legacy_business_adapter(
    db_path: DatabaseTarget,
    store_id: int,
    legacy_business: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Overlay only V2 facts that are provable onto the legacy V0.4 business shape.

    V2 does not yet know ingredient/personnel/profit fields, so without a legacy
    business object it deliberately returns None instead of inventing a complete
    V0.4 business record.
    """
    if not legacy_business:
        return None
    rows = list_daily_sales(db_path, store_id)
    if not rows:
        return dict(legacy_business)
    out = dict(legacy_business)
    revenue = sum((money(r["gross_sales"]) for r in rows if r["gross_sales"] is not None), Decimal("0.00"))
    out["revenue"] = float(revenue)
    order_values = [r["order_count"] for r in rows]
    if order_values and all(v is not None for v in order_values):
        out["cups"] = int(sum(order_values))
    out["source"] = "schema-v2+legacy"
    return out


def reset_store_data(db_path: DatabaseTarget, store_id: int) -> None:
    """Delete V2 operational/import data for one store while preserving schema and dictionaries."""
    _ensure_database(db_path)
    with _connect_target(db_path) as con:
        con.execute("BEGIN")
        con.execute(
            """DELETE FROM reconciliation_matches
               WHERE settlement_id IN (SELECT id FROM settlements WHERE store_id=?)
                  OR payment_id IN (SELECT id FROM payments WHERE store_id=?)""",
            (store_id, store_id),
        )
        con.execute("DELETE FROM platform_fees WHERE store_id=?", (store_id,))
        con.execute("DELETE FROM daily_channel_sales WHERE store_id=?", (store_id,))
        con.execute("DELETE FROM payments WHERE store_id=?", (store_id,))
        con.execute("DELETE FROM settlements WHERE store_id=?", (store_id,))
        con.execute(
            "DELETE FROM raw_import_rows WHERE import_batch_id IN (SELECT id FROM import_batches WHERE store_id=?)",
            (store_id,),
        )
        con.execute("DELETE FROM import_batches WHERE store_id=?", (store_id,))
        con.execute("DELETE FROM import_files WHERE store_id=?", (store_id,))
        con.commit()


def _database_snapshot_bytes(db_path: DatabaseTarget) -> bytes:
    if isinstance(db_path, Database):
        if db_path.backend != 'sqlite' or db_path.sqlite_path is None:
            raise RuntimeError('SQLite backup is not supported for PostgreSQL')
        path = db_path.sqlite_path
    else:
        path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    migrate_database(path)
    fd, tmp_name = tempfile.mkstemp(prefix="shice-ai-backup-", suffix=".db", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with sqlite3.connect(str(path)) as source, sqlite3.connect(str(tmp_path)) as target:
            source.backup(target)
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)


def export_full_backup(db_path: DatabaseTarget, store_id: int, legacy_state: dict[str, Any]) -> dict[str, Any]:
    """Export an inspectable JSON envelope plus an exact SQLite snapshot for lossless restore."""
    _ensure_database(db_path)
    with _connect_target(db_path) as con:
        def rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

        data_foundation = {
            "channels": rows("SELECT * FROM channels ORDER BY id"),
            "daily_channel_sales": rows("SELECT * FROM daily_channel_sales WHERE store_id=? ORDER BY id", (store_id,)),
            "settlements": rows("SELECT * FROM settlements WHERE store_id=? ORDER BY id", (store_id,)),
            "platform_fees": rows("SELECT * FROM platform_fees WHERE store_id=? ORDER BY id", (store_id,)),
            "payments": rows("SELECT * FROM payments WHERE store_id=? ORDER BY id", (store_id,)),
            "import_files": rows("SELECT * FROM import_files WHERE store_id=? ORDER BY id", (store_id,)),
            "import_batches": rows("SELECT * FROM import_batches WHERE store_id=? ORDER BY id", (store_id,)),
            "raw_import_rows": rows(
                "SELECT r.* FROM raw_import_rows r JOIN import_batches b ON b.id=r.import_batch_id WHERE b.store_id=? ORDER BY r.id",
                (store_id,),
            ),
            "reconciliation_matches": rows(
                """SELECT m.* FROM reconciliation_matches m
                   JOIN settlements s ON s.id=m.settlement_id
                   WHERE s.store_id=? ORDER BY m.id""",
                (store_id,),
            ),
        }

    db_bytes = _database_snapshot_bytes(db_path)
    return {
        "backup_format": "shice-ai-full-v1",
        "app_version": "0.5.0-R1",
        "created_at": int(time.time() * 1000),
        "store_id": store_id,
        "state": legacy_state,
        "data_foundation": data_foundation,
        "sqlite_sha256": hashlib.sha256(db_bytes).hexdigest(),
        "sqlite_base64": base64.b64encode(db_bytes).decode("ascii"),
    }


def restore_full_backup(db_path: DatabaseTarget, payload: dict[str, Any]) -> None:
    """Restore a full JSON backup atomically after validating its SQLite snapshot."""
    if payload.get("backup_format") != "shice-ai-full-v1":
        raise ValueError("备份格式无法识别，请选择食策AI完整备份JSON")
    encoded = payload.get("sqlite_base64")
    expected_hash = payload.get("sqlite_sha256")
    if not encoded or not expected_hash:
        raise ValueError("备份文件不完整：缺少数据库快照")
    try:
        db_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("备份数据库内容损坏，无法恢复") from exc
    if hashlib.sha256(db_bytes).hexdigest() != expected_hash:
        raise ValueError("备份校验失败，文件可能已损坏")
    if not db_bytes.startswith(b"SQLite format 3\x00"):
        raise ValueError("备份中的数据库格式无效")

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="shice-ai-restore-", suffix=".db", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_bytes(db_bytes)
        # Validate/migrate the staged file before touching the active database.
        migrate_database(tmp_path)
        with connect_v2(tmp_path) as con:
            if not con.execute("SELECT 1 FROM stores LIMIT 1").fetchone():
                raise ValueError("备份中没有有效门店数据")
        if path.exists():
            stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000_000:09d}"
            safety = path.with_name(f"{path.stem}_pre_restore_{stamp}{path.suffix}")
            safety.write_bytes(_database_snapshot_bytes(path))
        os.replace(tmp_path, path)
        migrate_database(path)
    finally:
        tmp_path.unlink(missing_ok=True)
