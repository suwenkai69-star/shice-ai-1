from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from mini.auth import AuthError, TokenService
from repositories.mini_store_repository import MiniStoreRepository
from persistence.database import Database, build_runtime_database


def get_db_path() -> Path:
    import app as app_module
    return Path(app_module.DB_PATH)


def get_database() -> Database:
    import app as app_module
    current_path = Path(app_module.DB_PATH)
    runtime = getattr(app_module.app.state, "database", None)
    if isinstance(runtime, Database):
        if runtime.backend == "postgresql":
            return runtime
        if runtime.sqlite_path == current_path:
            return runtime
    return build_runtime_database(current_path)


def user_id_from_authorization(authorization: str | None) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        return TokenService().verify(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail="登录已失效，请重新进入") from exc


def current_store(authorization: str | None) -> dict:
    user_id = user_id_from_authorization(authorization)
    store = MiniStoreRepository(get_database()).get_current_store(user_id)
    if store is None:
        raise HTTPException(status_code=404, detail="还没有建立门店")
    return store
