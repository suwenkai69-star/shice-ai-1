from __future__ import annotations

import os
from typing import Any

import httpx

from notifications.provider import SendResult


class WechatSubscriptionProvider:
    token_endpoint = "https://api.weixin.qq.com/cgi-bin/token"
    send_endpoint = "https://api.weixin.qq.com/cgi-bin/message/subscribe/send"

    def __init__(
        self,
        appid: str | None = None,
        secret: str | None = None,
        template_id: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.appid = (appid if appid is not None else os.getenv("WECHAT_APPID", "")).strip()
        self.secret = (secret if secret is not None else os.getenv("WECHAT_SECRET", "")).strip()
        self.template_id = (template_id if template_id is not None else os.getenv("WECHAT_SUBSCRIBE_TEMPLATE_ID", "")).strip()
        self.client = client or httpx.Client(timeout=10.0)

    def _configured(self, reminder: dict[str, Any]) -> tuple[bool, str | None]:
        if not self.appid or not self.secret or not self.template_id:
            return False, "wechat not configured"
        if not str(reminder.get("wechat_openid") or "").strip():
            return False, "wechat openid unavailable"
        return True, None

    def _access_token(self) -> str:
        response = self.client.get(
            self.token_endpoint,
            params={"grant_type": "client_credential", "appid": self.appid, "secret": self.secret},
        )
        payload = response.json()
        token = payload.get("access_token") if response.status_code == 200 else None
        if not token:
            raise RuntimeError(payload.get("errmsg") or "wechat access token failed")
        return str(token)

    def send(self, reminder: dict[str, Any]) -> SendResult:
        reminder_id = int(reminder["id"])
        configured, reason = self._configured(reminder)
        if not configured:
            return SendResult(reminder_id, "SKIPPED", reason)
        try:
            token = self._access_token()
            # Template field names are template-specific. Mini V1 uses the
            # configured reminder template contract with a single short thing field.
            payload = {
                "touser": reminder["wechat_openid"],
                "template_id": self.template_id,
                "page": "pages/actions/index",
                "data": {"thing1": {"value": str(reminder.get("action_title") or "经营建议待复查")[:20]}},
            }
            response = self.client.post(self.send_endpoint, params={"access_token": token}, json=payload)
            body = response.json()
            if response.status_code != 200 or int(body.get("errcode", 0)) != 0:
                return SendResult(reminder_id, "FAILED", str(body.get("errmsg") or "wechat send failed"))
            return SendResult(reminder_id, "SENT")
        except Exception as exc:
            return SendResult(reminder_id, "FAILED", str(exc) or exc.__class__.__name__)
