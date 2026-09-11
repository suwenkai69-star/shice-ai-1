from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class RecognitionField:
    field_code: str
    value: str | None
    confidence: float
    raw_text: str | None = None

@dataclass(frozen=True)
class RecognitionDocument:
    platform: str
    page_type: str
    fields: tuple[RecognitionField, ...]
    provider: str = "unknown"
    model_version: str | None = None

class RecognitionProvider(Protocol):
    def recognize(self, image_bytes: bytes) -> RecognitionDocument: ...
