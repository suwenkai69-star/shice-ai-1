from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class WechatIdentity:
    openid: str
    unionid: str | None = None


class AuthError(ValueError):
    pass


class AuthConfigurationError(RuntimeError):
    pass


def _is_production() -> bool:
    env = os.getenv("SHICE_ENV", "local").strip().lower()
    return env == "production" or os.getenv("VERCEL", "").strip() == "1"


class AuthProvider(Protocol):
    def exchange_code(self, code: str) -> WechatIdentity: ...


class DevAuthProvider:
    """Development-only provider used when WeChat credentials are not configured."""

    def exchange_code(self, code: str) -> WechatIdentity:
        if not code.startswith("dev-"):
            raise AuthError("invalid dev code")
        return WechatIdentity(openid=f"dev:{code}", unionid=None)


class WechatAuthProvider:
    endpoint = "https://api.weixin.qq.com/sns/jscode2session"

    def __init__(
        self,
        appid: str,
        secret: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.appid = appid
        self.secret = secret
        self.client = client or httpx.Client(timeout=10.0)

    def exchange_code(self, code: str) -> WechatIdentity:
        if not code:
            raise AuthError("wechat code is required")
        try:
            response = self.client.get(
                self.endpoint,
                params={
                    "appid": self.appid,
                    "secret": self.secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
            payload = response.json()
        except Exception as exc:  # network/JSON failures are authentication failures at this boundary
            raise AuthError("wechat code exchange failed") from exc
        if response.status_code != 200 or payload.get("errcode"):
            raise AuthError(payload.get("errmsg") or "wechat code exchange failed")
        openid = payload.get("openid")
        if not openid:
            raise AuthError("wechat response missing openid")
        return WechatIdentity(openid=openid, unionid=payload.get("unionid"))


def get_auth_provider() -> AuthProvider:
    appid = os.getenv("WECHAT_APPID", "").strip()
    secret = os.getenv("WECHAT_SECRET", "").strip()
    if appid and secret:
        return WechatAuthProvider(appid, secret)
    if _is_production():
        raise AuthConfigurationError("WeChat login is not configured")
    return DevAuthProvider()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


class TokenService:
    def __init__(self, secret: str | None = None, ttl_seconds: int = 24 * 3600) -> None:
        resolved = secret or os.getenv("MINI_TOKEN_SECRET")
        if not resolved and _is_production():
            raise AuthConfigurationError("MINI_TOKEN_SECRET is required in production")
        self.secret = (resolved or "dev-only-change-me").encode("utf-8")
        self.ttl_seconds = int(ttl_seconds)

    def issue(self, user_id: int) -> str:
        now = int(time.time())
        payload = {"uid": int(user_id), "iat": now, "exp": now + self.ttl_seconds}
        body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signature = _b64encode(hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{signature}"

    def verify(self, token: str) -> int:
        try:
            body, signature = token.split(".", 1)
            expected = _b64encode(hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                raise AuthError("invalid token signature")
            payload = json.loads(_b64decode(body).decode("utf-8"))
            uid = int(payload["uid"])
            exp = int(payload["exp"])
        except AuthError:
            raise
        except Exception as exc:
            raise AuthError("invalid token") from exc
        if exp < int(time.time()):
            raise AuthError("token expired")
        return uid
