from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class PersistenceConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class PersistenceSettings:
    environment: str
    backend: Literal['sqlite', 'postgresql']
    sqlite_path: Path
    database_url: str | None


def load_persistence_settings(sqlite_path: str | Path) -> PersistenceSettings:
    path = Path(sqlite_path)
    env = os.getenv('SHICE_ENV', 'local').strip().lower() or 'local'
    database_url = os.getenv('DATABASE_URL', '').strip() or None
    is_production = env == 'production' or os.getenv('VERCEL', '').strip() == '1'
    if database_url:
        if database_url.startswith('postgresql://') or database_url.startswith('postgresql+psycopg://'):
            return PersistenceSettings(env, 'postgresql', path, database_url)
        raise PersistenceConfigError('DATABASE_URL must use postgresql:// or postgresql+psycopg://')
    if is_production:
        raise PersistenceConfigError('DATABASE_URL is required in production')
    return PersistenceSettings(env, 'sqlite', path, None)
