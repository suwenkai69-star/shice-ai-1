from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from mini import auth as auth_module
from mini.auth import AuthError, AuthConfigurationError, TokenService
from mini.deps import get_database
from mini.models import StoreCreateRequest, StorePatchRequest, WechatLoginRequest
from repositories.mini_store_repository import MiniStoreRepository

router = APIRouter()


def _token_service() -> TokenService:
    return TokenService()


def _user_id_from_auth(authorization: str | None) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        return _token_service().verify(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail="登录已失效，请重新进入") from exc


@router.post("/auth/wechat")
def wechat_login(payload: WechatLoginRequest):
    try:
        identity = auth_module.get_auth_provider().exchange_code(payload.code)
        repo = MiniStoreRepository(get_database())
        user = repo.create_or_get_user(identity.openid, identity.unionid)
        token = _token_service().issue(int(user["user_id"]))
        store = repo.get_current_store(int(user["user_id"]))
        return {
            "token": token,
            "user": {"id": int(user["user_id"])},
            "has_store": store is not None,
            "store": store,
        }
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail="登录服务尚未配置") from exc
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/store")
def create_store(payload: StoreCreateRequest, authorization: str | None = Header(default=None)):
    user_id = _user_id_from_auth(authorization)
    repo = MiniStoreRepository(get_database())
    try:
        store = repo.create_store(user_id, payload.name, payload.category_code)
    except ValueError as exc:
        if "already has a store" in str(exc):
            raise HTTPException(status_code=409, detail="当前账号已经建立门店") from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"store": store}


@router.get("/store")
def get_store(authorization: str | None = Header(default=None)):
    user_id = _user_id_from_auth(authorization)
    store = MiniStoreRepository(get_database()).get_current_store(user_id)
    if store is None:
        raise HTTPException(status_code=404, detail="还没有建立门店")
    return {"store": store}


@router.patch("/store")
def patch_store(payload: StorePatchRequest, authorization: str | None = Header(default=None)):
    user_id = _user_id_from_auth(authorization)
    values = payload.model_dump(exclude_none=True)
    try:
        store = MiniStoreRepository(get_database()).update_store(user_id, values)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="还没有建立门店") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"store": store}


from mini.profit_routes import router as profit_router
router.include_router(profit_router)

from mini.manual_routes import router as manual_router
router.include_router(manual_router)

from mini.upload_routes import router as upload_router
router.include_router(upload_router)

from mini.data_status_routes import router as data_status_router
router.include_router(data_status_router)

from mini.issue_routes import router as issue_router
router.include_router(issue_router)

from mini.reconciliation_routes import router as reconciliation_router
router.include_router(reconciliation_router)

from mini.trend_routes import router as trend_router
router.include_router(trend_router)

from mini.today_routes import router as today_router
router.include_router(today_router)

from mini.action_routes import router as action_router
router.include_router(action_router)

from mini.ai_routes import router as ai_router
router.include_router(ai_router)

from mini.reminder_routes import router as reminder_router
router.include_router(reminder_router)
