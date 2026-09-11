from __future__ import annotations

from notifications.provider import NotificationProvider, SendResult
from repositories.action_repository import ActionRepository
from persistence.database import DatabaseTarget


class ReminderService:
    def __init__(self, database: DatabaseTarget, provider: NotificationProvider):
        self.repo = ActionRepository(database)
        self.provider = provider

    def dispatch_due(self, now: int) -> list[SendResult]:
        results: list[SendResult] = []
        for reminder in self.repo.list_due_reminders(now):
            try:
                result = self.provider.send(reminder)
            except Exception as exc:
                result = SendResult(int(reminder["id"]), "FAILED", str(exc) or exc.__class__.__name__)
            if result.status not in {"SENT", "FAILED", "SKIPPED"}:
                result = SendResult(int(reminder["id"]), "FAILED", f"invalid provider status: {result.status}")
            self.repo.update_reminder_delivery(
                reminder_id=int(reminder["id"]),
                status=result.status,
                now=now,
                error_message=result.error,
            )
            results.append(result)
        return results
