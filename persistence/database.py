from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ContextManager, TypeAlias

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

from .config import load_persistence_settings

_IDENTITY_TABLES = {
    "users","stores","channels","import_files","import_batches","raw_import_rows",
    "daily_channel_sales","settlements","platform_fees","payments","reconciliation_matches",
    "mini_users","daily_store_sales_totals","cost_entries","industry_benchmarks","profit_snapshots",
    "upload_documents","upload_drafts","extraction_jobs","extracted_fields","document_import_links",
    "store_data_sources","daily_data_status","anomaly_events","action_items","action_executions",
    "action_verifications","reminders","ai_conversations","ai_messages",
}


def _qmark_to_named(sql: str, params: Any) -> tuple[str, dict[str, Any]]:
    if params is None:
        return sql, {}
    if isinstance(params, dict):
        return sql, params
    values = list(params) if isinstance(params, (tuple, list)) else [params]
    if '?' not in sql:
        return sql, {f'p{i}': v for i, v in enumerate(values)}
    parts = sql.split('?')
    if len(parts) - 1 != len(values):
        raise ValueError('SQL parameter count mismatch')
    out = parts[0]
    named: dict[str, Any] = {}
    for i, value in enumerate(values):
        key = f'p{i}'
        out += f':{key}' + parts[i + 1]
        named[key] = value
    return out, named


class CompatRow:
    def __init__(self, mapping):
        self._mapping = dict(mapping)
        self._keys = list(self._mapping.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._mapping[self._keys[key]]
        return self._mapping[key]

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)

    def keys(self):
        return self._mapping.keys()

    def items(self):
        return self._mapping.items()

    def values(self):
        return self._mapping.values()

    def get(self, key, default=None):
        return self._mapping.get(key, default)


class CompatResult:
    def __init__(self, result, prefetched=None):
        self._result = result
        self._prefetched = prefetched

    @property
    def rowcount(self):
        return getattr(self._result, 'rowcount', -1)

    @property
    def lastrowid(self):
        if self._prefetched is not None:
            try:
                return self._prefetched['id']
            except Exception:
                try:
                    return self._prefetched[0]
                except Exception:
                    return None
        return getattr(self._result, 'lastrowid', None)

    def fetchone(self):
        if self._prefetched is not None:
            row, self._prefetched = self._prefetched, None
            return row
        if not self._result.returns_rows:
            return None
        raw = self._result.mappings().fetchone()
        return CompatRow(raw) if raw is not None else None

    def fetchall(self):
        rows = []
        if self._prefetched is not None:
            rows.append(self._prefetched)
            self._prefetched = None
        if self._result.returns_rows:
            rows.extend(CompatRow(r) for r in self._result.mappings().all())
        return rows

    def scalar(self):
        if self._prefetched is not None:
            try:
                return next(iter(self._prefetched.values()))
            finally:
                self._prefetched = None
        return self._result.scalar()


class NoopResult:
    rowcount = -1
    lastrowid = None
    def fetchone(self): return None
    def fetchall(self): return []
    def scalar(self): return None


class CompatConnection:
    """Small DB-API-shaped bridge used while legacy persistence is moved to SQLAlchemy.

    It deliberately translates the existing qmark SQL at the boundary, so PostgreSQL can
    execute the same already-tested business persistence while modules are migrated in focused steps.
    New cloud persistence code should use SQLAlchemy named parameters directly.
    """
    def __init__(self, db: 'Database'):
        self.db = db
        self._con: Connection | None = None
        self._tx = None

    def _open(self):
        if self._con is None:
            self._con = self.db.engine.connect()
            self._tx = self._con.begin()
        return self

    def __enter__(self):
        return self._open()

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._tx is not None and self._tx.is_active:
                if exc_type is None:
                    self._tx.commit()
                else:
                    self._tx.rollback()
        finally:
            if self._con is not None:
                self._con.close()
        return False

    def execute(self, sql: str, params: Any = None) -> CompatResult:
        self._open()
        assert self._con is not None
        if sql.strip().upper() in {'BEGIN', 'BEGIN IMMEDIATE'}:
            return NoopResult()
        original = sql
        if self.db.backend == 'postgresql':
            sql = re.sub(r'^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+', 'INSERT INTO ', sql, flags=re.I)
            was_ignore = bool(re.match(r'^\s*INSERT\s+OR\s+IGNORE', original, flags=re.I))
            if was_ignore and 'ON CONFLICT' not in sql.upper():
                sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
        sql_named, named = _qmark_to_named(sql, params)
        if self.db.backend == 'postgresql' and re.match(r'^\s*INSERT\s+INTO', sql_named, flags=re.I):
            m = re.match(r'^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)', sql_named, flags=re.I)
            table = m.group(1).lower() if m else ''
            if table in _IDENTITY_TABLES and 'RETURNING' not in sql_named.upper():
                sql_named = sql_named.rstrip().rstrip(';') + ' RETURNING id'
        result = self._con.execute(text(sql_named), named)
        prefetched = None
        if self.db.backend == 'postgresql' and result.returns_rows and re.match(r'^\s*INSERT', sql_named, flags=re.I):
            raw = result.mappings().fetchone()
            prefetched = CompatRow(raw) if raw is not None else None
        return CompatResult(result, prefetched=prefetched)

    def close(self):
        try:
            if self._tx is not None and self._tx.is_active:
                self._tx.rollback()
        finally:
            if self._con is not None:
                self._con.close()
            self._con = None
            self._tx = None

    def commit(self):
        if self._tx is not None and self._tx.is_active:
            self._tx.commit()
            if self._con is not None:
                self._tx = self._con.begin()

    def rollback(self):
        if self._tx is not None and self._tx.is_active:
            self._tx.rollback()
            if self._con is not None:
                self._tx = self._con.begin()


class Database:
    def __init__(self, engine: Engine, backend: str, sqlite_path: Path | None = None):
        self.engine = engine
        self._backend = backend
        self.sqlite_path = sqlite_path

    @classmethod
    def sqlite(cls, path: str | Path) -> 'Database':
        resolved = Path(path)
        engine = create_engine(f'sqlite:///{resolved}', future=True)
        @event.listens_for(engine, 'connect')
        def _fk_on(dbapi_connection, _connection_record):
            cur = dbapi_connection.cursor(); cur.execute('PRAGMA foreign_keys=ON'); cur.close()
        return cls(engine, 'sqlite', resolved)

    @classmethod
    def postgres(cls, url: str) -> 'Database':
        normalized = url
        if normalized.startswith('postgresql://'):
            normalized = 'postgresql+psycopg://' + normalized[len('postgresql://'):]
        engine = create_engine(normalized, future=True, poolclass=NullPool, pool_pre_ping=True)
        return cls(engine, 'postgresql', None)

    @property
    def backend(self) -> str:
        return self._backend

    def begin(self) -> ContextManager[Connection]:
        return self.engine.begin()

    def connect(self) -> ContextManager[Connection]:
        return self.engine.connect()

    def compat_connect(self) -> CompatConnection:
        return CompatConnection(self)

    def dispose(self) -> None:
        self.engine.dispose()


DatabaseTarget: TypeAlias = Database | str | Path


def coerce_database(target: DatabaseTarget) -> Database:
    if isinstance(target, Database):
        return target
    path = Path(target)
    from db import migrate_database
    migrate_database(path)
    return Database.sqlite(path)


def build_runtime_database(sqlite_path: str | Path) -> Database:
    settings = load_persistence_settings(sqlite_path)
    if settings.backend == 'sqlite':
        return Database.sqlite(settings.sqlite_path)
    assert settings.database_url is not None
    return Database.postgres(settings.database_url)
