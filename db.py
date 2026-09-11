from __future__ import annotations

from pathlib import Path

from db_v2 import get_schema_version, migrate_database as migrate_v2
from db_v3 import ensure_v3_additive_schema, migrate_v2_to_v3

SCHEMA_VERSION = 3


def migrate_database(db_path: str | Path) -> int:
    """Upgrade a supported database through V2 and then additive V3."""
    version = get_schema_version(db_path)
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"database has newer schema version {version}; refusing downgrade to {SCHEMA_VERSION}"
        )
    if version < 2:
        migrate_v2(db_path)
        version = get_schema_version(db_path)
    if version == 2:
        version = migrate_v2_to_v3(db_path)
        ensure_v3_additive_schema(db_path)
        return version
    if version == 3:
        ensure_v3_additive_schema(db_path)
        return 3
    raise RuntimeError(f"unsupported schema version {version}")
