from __future__ import annotations

from typing import Protocol


class TextProviderUnavailable(RuntimeError):
    pass


class TextModelProvider(Protocol):
    def generate(self, system: str, user: str) -> str: ...


class DisabledTextProvider:
    def generate(self, system: str, user: str) -> str:
        raise TextProviderUnavailable("text model provider is not configured")


def get_text_provider() -> TextModelProvider:
    # Mini V1 keeps the provider replaceable. Deployment must inject a concrete
    # provider; local tests use deterministic fakes and never require a network call.
    return DisabledTextProvider()
