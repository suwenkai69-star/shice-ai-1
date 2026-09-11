from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from mini.deps import current_store, get_database, user_id_from_authorization
from services.action_engine import ActionEngine, ActionItem

router = APIRouter()


class ExecuteRequest(BaseModel):
    note: str | None = None


class RemindRequest(BaseModel):
    scheduled_at: int


def _serialize(action: ActionItem) -> dict:
    return {
        "id": action.id,
        "business_date": action.business_date,
        "anomaly_id": action.anomaly_id,
        "action_type": action.action_type,
        "title": action.title,
        "reason": action.reason,
        "expected_metric": action.expected_metric,
        "expected_direction": action.expected_direction,
        "priority": action.priority,
        "status": action.status,
        "steps": list(action.steps),
        "requires_user_confirmation": action.requires_user_confirmation,
    }


@router.get("/actions")
def list_actions(
    business_date: str | None = Query(default=None, alias="date"),
    authorization: str | None = Header(default=None),
):
    store = current_store(authorization)
    day = business_date or date.today().isoformat()
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc
    actions = ActionEngine(get_database()).generate(int(store["id"]), day)
    return {"date": day, "actions": [_serialize(action) for action in actions]}


@router.get("/actions/{action_id}")
def action_detail(action_id: int, authorization: str | None = Header(default=None)):
    store = current_store(authorization)
    try:
        action = ActionEngine(get_database()).get(action_id, int(store["id"]))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到这个待办") from exc
    return {"action": _serialize(action)}


@router.post("/actions/{action_id}/execute")
def execute_action(
    action_id: int,
    payload: ExecuteRequest,
    authorization: str | None = Header(default=None),
):
    store = current_store(authorization)
    try:
        action = ActionEngine(get_database()).execute(action_id, int(store["id"]), note=payload.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到这个待办") from exc
    return {"action": _serialize(action)}


@router.post("/actions/{action_id}/skip")
def skip_action(action_id: int, authorization: str | None = Header(default=None)):
    store = current_store(authorization)
    try:
        action = ActionEngine(get_database()).skip(action_id, int(store["id"]))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到这个待办") from exc
    return {"action": _serialize(action)}


@router.post("/actions/{action_id}/remind")
def remind_action(
    action_id: int,
    payload: RemindRequest,
    authorization: str | None = Header(default=None),
):
    user_id = user_id_from_authorization(authorization)
    store = current_store(authorization)
    engine = ActionEngine(get_database())
    try:
        reminder = engine.remind(action_id, user_id, int(store["id"]), payload.scheduled_at)
        action = engine.get(action_id, int(store["id"]))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到这个待办") from exc
    return {"action": _serialize(action), "reminder": reminder}

class GenerateCopyRequest(BaseModel):
    output_type: str


@router.post("/actions/{action_id}/generate-copy")
def generate_action_copy(
    action_id: int,
    payload: GenerateCopyRequest,
    authorization: str | None = Header(default=None),
):
    from ai.provider import TextProviderUnavailable
    from ai.text_service import AITextService

    store = current_store(authorization)
    try:
        return AITextService(get_database()).generate_copy(int(store["id"]), action_id, payload.output_type)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到这个待办") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="暂不支持这种文案类型") from exc
    except TextProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail="AI文字服务还没有配置，请稍后再试") from exc
