from __future__ import annotations

import hashlib
from pathlib import Path
from sqlalchemy import text

from .database import Database

_ROOT = Path(__file__).resolve().parent / 'postgres'


class CloudSchemaError(RuntimeError):
    pass


def load_initial_migration_sql() -> str:
    parts = [p.read_text(encoding='utf-8') for p in sorted(_ROOT.glob('*.sql'))]
    return '\n'.join(parts)


def initial_migration_sha256() -> str:
    return hashlib.sha256(load_initial_migration_sql().encode('utf-8')).hexdigest()


def assert_cloud_schema(db: Database) -> None:
    with db.connect() as con:
        rows = con.execute(
            text("SELECT key,value FROM schema_meta WHERE key IN ('schema_version','cloud_schema_version')")
        ).mappings().all()
    values = {str(r['key']): str(r['value']) for r in rows}
    if values.get('schema_version') != '3' or values.get('cloud_schema_version') != '1':
        raise CloudSchemaError('DATABASE_MIGRATION_REQUIRED')
