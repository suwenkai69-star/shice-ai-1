from __future__ import annotations
from pydantic import BaseModel

class ExtractionFieldPatch(BaseModel):
    field_code: str
    confirmed_value: str | None = None
    excluded: bool = False

class ExtractionFieldsPatchRequest(BaseModel):
    fields: list[ExtractionFieldPatch]
