from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from .database import Database
from .schema import STANDARD_CHANNELS


@dataclass(frozen=True)
class SeedResult:
    channel_count: int
    benchmark_count: int
    seed_sha256: str


def _normalized_approved(path: Path) -> tuple[list[dict], str]:
    rows = json.loads(path.read_text(encoding='utf-8'))
    approved = [dict(row) for row in rows if row.get('review_status') == 'APPROVED']
    payload = json.dumps(approved, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return approved, hashlib.sha256(payload).hexdigest()


def seed_system_data(db: Database, benchmark_seed_path: Path) -> SeedResult:
    approved, seed_hash = _normalized_approved(benchmark_seed_path)
    now = int(time.time() * 1000)
    with db.begin() as con:
        for code, name, category in STANDARD_CHANNELS:
            con.execute(text('''
                INSERT INTO channels(code,name,category,enabled,created_at,updated_at)
                VALUES(:code,:name,:category,1,:now,:now)
                ON CONFLICT(code) DO UPDATE SET
                    name=excluded.name, category=excluded.category, enabled=1, updated_at=excluded.updated_at
            '''), {'code': code, 'name': name, 'category': category, 'now': now})
        for row in approved:
            params = {
                'category_code': row['category_code'], 'region_level': row['region_level'],
                'region_code': row.get('region_code') or '', 'metric_code': row['metric_code'],
                'low_value': str(row['low_value']), 'mid_value': None if row.get('mid_value') is None else str(row['mid_value']),
                'high_value': str(row['high_value']), 'unit': row['unit'], 'source_name': row['source_name'],
                'source_url': row['source_url'], 'source_date': row['source_date'],
                'confidence_level': row['confidence_level'], 'review_status': 'APPROVED',
                'valid_from': row['valid_from'], 'valid_to': row.get('valid_to'), 'now': now,
            }
            con.execute(text('''
                INSERT INTO industry_benchmarks(
                    category_code,region_level,region_code,metric_code,low_value,mid_value,high_value,unit,
                    source_name,source_url,source_date,confidence_level,review_status,valid_from,valid_to,created_at,updated_at
                ) VALUES(
                    :category_code,:region_level,:region_code,:metric_code,:low_value,:mid_value,:high_value,:unit,
                    :source_name,:source_url,:source_date,:confidence_level,:review_status,:valid_from,:valid_to,:now,:now
                )
                ON CONFLICT(category_code,region_level,region_code,metric_code,valid_from) DO UPDATE SET
                    low_value=excluded.low_value, mid_value=excluded.mid_value, high_value=excluded.high_value,
                    unit=excluded.unit, source_name=excluded.source_name, source_url=excluded.source_url,
                    source_date=excluded.source_date, confidence_level=excluded.confidence_level,
                    review_status=excluded.review_status, valid_to=excluded.valid_to, updated_at=excluded.updated_at
            '''), params)
        con.execute(text('''
            INSERT INTO schema_meta(key,value,updated_at) VALUES('benchmark_seed_sha256',:value,:now)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        '''), {'value': seed_hash, 'now': now})
    return SeedResult(channel_count=len(STANDARD_CHANNELS), benchmark_count=len(approved), seed_sha256=seed_hash)
