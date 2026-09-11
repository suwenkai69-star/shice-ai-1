from __future__ import annotations

from pydantic import BaseModel, Field


class WechatLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256)


class StoreCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category_code: str = Field(min_length=1, max_length=64)


class StorePatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category_code: str | None = Field(default=None, min_length=1, max_length=64)
    city_code: str | None = Field(default=None, max_length=64)
    owner_work_mode: str | None = Field(default=None, max_length=64)
