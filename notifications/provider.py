from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class SendResult:
    reminder_id: int
    status: str
    error: str | None = None


class NotificationProvider(Protocol):
    def send(self, reminder: dict[str, Any]) -> SendResult: ...
