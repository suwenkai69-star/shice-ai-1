from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ai.provider import TextProviderUnavailable
from ai.text_service import AITextService
from mini.deps import current_store, get_database

router = APIRouter()


class ChatRequest(BaseModel):
    question: str
    date: str


@router.post("/ai/chat")
def ai_chat(payload: ChatRequest, authorization: str | None = Header(default=None)):
    store = current_store(authorization)
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="请先输入你想问的问题")
    try:
        date.fromisoformat(payload.date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc
    try:
        return AITextService(get_database()).chat(int(store["id"]), question, payload.date)
    except TextProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail="AI文字服务还没有配置，请稍后再试") from exc
