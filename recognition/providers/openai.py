from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from recognition.provider import RecognitionDocument, RecognitionField


class OpenAIRecognitionError(RuntimeError):
    pass


_SUPPORTED_FIELD_CODES = (
    "BUSINESS_DATE",
    "GROSS_SALES",
    "CUSTOMER_PAID",
    "ORDER_COUNT",
    "REFUND_AMOUNT",
    "REFUND_COUNT",
    "MERCHANT_DISCOUNT",
    "PLATFORM_SUBSIDY",
    "PLATFORM_FEE",
)

_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "platform": {"type": "string"},
        "page_type": {"type": "string"},
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_code": {"type": "string"},
                    "value": {"type": ["string", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "raw_text": {"type": ["string", "null"]},
                },
                "required": ["field_code", "value", "confidence", "raw_text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["platform", "page_type", "fields"],
    "additionalProperties": False,
}


class OpenAIRecognitionProvider:
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        api_key: str,
        model: str,
        client: httpx.Client | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenAI API key is required")
        if not model.strip():
            raise ValueError("OpenAI recognition model is required")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self.timeout_seconds = float(timeout_seconds)

    @staticmethod
    def _mime(image_bytes: bytes) -> str:
        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if image_bytes.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
            return "image/webp"
        return "image/jpeg"

    @classmethod
    def _data_url(cls, image_bytes: bytes) -> str:
        if not image_bytes:
            raise OpenAIRecognitionError("empty image")
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{cls._mime(image_bytes)};base64,{encoded}"

    @staticmethod
    def _output_text(payload: dict[str, Any]) -> str:
        for item in payload.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    return part["text"]
        raise OpenAIRecognitionError("recognition response contains no output_text")

    @staticmethod
    def _parse_result(text: str, model: str) -> RecognitionDocument:
        try:
            payload = json.loads(text)
        except Exception as exc:
            raise OpenAIRecognitionError("recognition response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise OpenAIRecognitionError("recognition response must be an object")
        platform = payload.get("platform")
        page_type = payload.get("page_type")
        fields = payload.get("fields")
        if not isinstance(platform, str) or not platform.strip():
            raise OpenAIRecognitionError("recognition response missing platform")
        if not isinstance(page_type, str) or not page_type.strip():
            raise OpenAIRecognitionError("recognition response missing page_type")
        if not isinstance(fields, list):
            raise OpenAIRecognitionError("recognition response fields must be a list")

        parsed: list[RecognitionField] = []
        for item in fields:
            if not isinstance(item, dict):
                raise OpenAIRecognitionError("recognition field must be an object")
            code = item.get("field_code")
            value = item.get("value")
            raw_text = item.get("raw_text")
            confidence = item.get("confidence")
            if code not in _SUPPORTED_FIELD_CODES:
                continue
            if value is not None and not isinstance(value, str):
                raise OpenAIRecognitionError(f"invalid value for {code}")
            if raw_text is not None and not isinstance(raw_text, str):
                raise OpenAIRecognitionError(f"invalid raw_text for {code}")
            try:
                conf = float(confidence)
            except (TypeError, ValueError) as exc:
                raise OpenAIRecognitionError(f"invalid confidence for {code}") from exc
            if not 0 <= conf <= 1:
                raise OpenAIRecognitionError(f"invalid confidence for {code}")
            parsed.append(RecognitionField(code, value, conf, raw_text))

        return RecognitionDocument(
            platform=platform.strip(),
            page_type=page_type.strip(),
            fields=tuple(parsed),
            provider="openai-responses",
            model_version=model,
        )

    def recognize(self, image_bytes: bytes) -> RecognitionDocument:
        prompt = (
            "你在识别餐饮门店经营后台截图。只提取画面中能直接读到的经营事实，不推测缺失数字。"
            "platform 优先使用 MEITUAN_DELIVERY、TAOBAO_FLASH、JD_DELIVERY、DOUYIN_LOCAL、"
            "MEITUAN_DEALS、WECHAT_PAY、ALIPAY、POS；无法确认时写 UNKNOWN_PLATFORM。"
            "当前页面是日经营报表时 page_type 写 DAILY_REPORT，否则写 UNKNOWN_PAGE。"
            "fields 只返回这些字段：" + ",".join(_SUPPORTED_FIELD_CODES) + "。"
            "金额去掉货币符号和千分位后输出字符串；日期输出 YYYY-MM-DD；计数字段输出整数文本。"
            "confidence 表示你对该字段文字和语义映射的把握，范围 0 到 1。"
        )
        body = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": self._data_url(image_bytes), "detail": "high"},
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "restaurant_dashboard_extraction",
                    "strict": True,
                    "schema": _RESPONSE_SCHEMA,
                }
            },
        }
        try:
            response = self.client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise OpenAIRecognitionError("OpenAI recognition request failed") from exc
        if not isinstance(payload, dict):
            raise OpenAIRecognitionError("OpenAI recognition response is invalid")
        return self._parse_result(self._output_text(payload), self.model)
