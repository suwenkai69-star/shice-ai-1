from __future__ import annotations
from recognition.provider import RecognitionDocument, RecognitionField

class FakeRecognitionProvider:
    def __init__(self, payload: dict):
        self.payload = payload
    def recognize(self, image_bytes: bytes) -> RecognitionDocument:
        fields=[]
        for code, item in (self.payload.get("fields") or {}).items():
            if isinstance(item, dict):
                value=item.get("value")
                confidence=float(item.get("confidence",0))
                raw_text=item.get("raw_text", None)
            else:
                value=item; confidence=1.0; raw_text=None
            fields.append(RecognitionField(code, None if value is None else str(value), confidence, raw_text or (None if value is None else str(value))))
        return RecognitionDocument(
            platform=self.payload.get("platform") or "UNKNOWN_PLATFORM",
            page_type=self.payload.get("page_type") or "UNKNOWN_PAGE",
            fields=tuple(fields), provider="fake", model_version="fake-v1",
        )
