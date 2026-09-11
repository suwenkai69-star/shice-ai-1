from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from persistence.database import DatabaseTarget, coerce_database



class ActionRepository:
    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path

    def _connect(self):
        return self.db.compat_connect()

    def get_store_id_for_action(self, action_id: int) -> int | None:
        with self._connect() as con:
            row = con.execute("SELECT store_id FROM action_items WHERE id=?", (action_id,)).fetchone()
        return int(row["store_id"]) if row is not None else None

    def get_action(self, action_id: int, store_id: int) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM action_items WHERE id=? AND store_id=?",
                (action_id, store_id),
            ).fetchone()
        if row is None:
            raise KeyError("action not found")
        return dict(row)

    def create_action(
        self,
        store_id: int,
        business_date: str,
        anomaly_id: int | None,
        action_type: str,
        title: str,
        reason: str,
        expected_metric: str,
        expected_direction: str,
        priority: int,
    ) -> dict[str, Any]:
        now = int(time.time() * 1000)
        with self._connect() as con:
            con.execute(
                """INSERT OR IGNORE INTO action_items(
                    store_id,business_date,anomaly_id,action_type,title,reason,expected_metric,
                    expected_direction,priority,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,'PENDING',?,?)""",
                (
                    store_id, business_date, anomaly_id, action_type, title, reason,
                    expected_metric, expected_direction, priority, now, now,
                ),
            )
            row = con.execute(
                """SELECT * FROM action_items
                   WHERE store_id=? AND business_date=? AND COALESCE(anomaly_id,-1)=COALESCE(?,-1)
                     AND action_type=?""",
                (store_id, business_date, anomaly_id, action_type),
            ).fetchone()
            con.commit()
        assert row is not None
        return dict(row)

    def mark_executed(
        self,
        action_id: int,
        store_id: int,
        executed_at: int,
        note: str | None = None,
    ) -> dict[str, Any]:
        now = int(time.time() * 1000)
        with self._connect() as con:
            action = con.execute(
                "SELECT * FROM action_items WHERE id=? AND store_id=?",
                (action_id, store_id),
            ).fetchone()
            if action is None:
                raise KeyError("action not found")
            con.execute(
                """INSERT INTO action_executions(
                    action_id,executed_at,execution_note,baseline_metric_code,baseline_metric_value,created_at
                ) VALUES(?,?,?,?,NULL,?)
                ON CONFLICT(action_id) DO UPDATE SET
                    executed_at=excluded.executed_at,
                    execution_note=excluded.execution_note""",
                (action_id, executed_at, note, action["expected_metric"], now),
            )
            con.execute(
                "UPDATE action_items SET status='WAITING_VERIFICATION',updated_at=? WHERE id=? AND store_id=?",
                (now, action_id, store_id),
            )
            row = con.execute(
                """SELECT a.*,e.execution_note,e.executed_at,e.baseline_metric_value
                   FROM action_items a LEFT JOIN action_executions e ON e.action_id=a.id
                   WHERE a.id=? AND a.store_id=?""",
                (action_id, store_id),
            ).fetchone()
            con.commit()
        assert row is not None
        return dict(row)

    def mark_skipped(self, action_id: int, store_id: int) -> dict[str, Any]:
        now = int(time.time() * 1000)
        with self._connect() as con:
            cur = con.execute(
                "UPDATE action_items SET status='SKIPPED',updated_at=? WHERE id=? AND store_id=?",
                (now, action_id, store_id),
            )
            if cur.rowcount != 1:
                raise KeyError("action not found")
            row = con.execute("SELECT * FROM action_items WHERE id=?", (action_id,)).fetchone()
            con.commit()
        assert row is not None
        return dict(row)

    def schedule_reminder(
        self,
        action_id: int,
        user_id: int,
        store_id: int,
        scheduled_at: int,
    ) -> dict[str, Any]:
        now = int(time.time() * 1000)
        with self._connect() as con:
            action = con.execute(
                "SELECT id FROM action_items WHERE id=? AND store_id=?",
                (action_id, store_id),
            ).fetchone()
            if action is None:
                raise KeyError("action not found")
            cur = con.execute(
                """INSERT INTO reminders(
                    user_id,store_id,action_id,reminder_type,scheduled_at,status,created_at,updated_at
                ) VALUES(?,?,?,'ACTION_FOLLOWUP',?,'PENDING',?,?)""",
                (user_id, store_id, action_id, scheduled_at, now, now),
            )
            con.execute(
                "UPDATE action_items SET status='REMIND',updated_at=? WHERE id=? AND store_id=?",
                (now, action_id, store_id),
            )
            row = con.execute("SELECT * FROM reminders WHERE id=?", (cur.lastrowid,)).fetchone()
            con.commit()
        assert row is not None
        return dict(row)

    def get_execution(self, action_id: int) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM action_executions WHERE action_id=?",
                (action_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_verification(
        self,
        action_id: int,
        verification_date: str,
        before_value: str | None,
        after_value: str | None,
        change_rate: str | None,
        result: str,
        confidence: str,
        explanation: str,
    ) -> dict[str, Any]:
        now = int(time.time() * 1000)
        with self._connect() as con:
            execution = con.execute(
                "SELECT id FROM action_executions WHERE action_id=?",
                (action_id,),
            ).fetchone()
            if execution is None:
                raise ValueError("action has not been executed")
            con.execute(
                "UPDATE action_executions SET baseline_metric_value=? WHERE action_id=?",
                (before_value, action_id),
            )
            con.execute(
                """INSERT INTO action_verifications(
                    action_id,verification_date,before_value,after_value,change_rate,
                    result,confidence,explanation,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(action_id,verification_date) DO UPDATE SET
                    before_value=excluded.before_value,
                    after_value=excluded.after_value,
                    change_rate=excluded.change_rate,
                    result=excluded.result,
                    confidence=excluded.confidence,
                    explanation=excluded.explanation""",
                (
                    action_id, verification_date, before_value, after_value, change_rate,
                    result, confidence, explanation, now,
                ),
            )
            if result != "INSUFFICIENT_DATA":
                con.execute(
                    "UPDATE action_items SET status='VERIFIED',updated_at=? WHERE id=?",
                    (now, action_id),
                )
            else:
                con.execute(
                    "UPDATE action_items SET status='WAITING_VERIFICATION',updated_at=? WHERE id=?",
                    (now, action_id),
                )
            row = con.execute(
                "SELECT * FROM action_verifications WHERE action_id=? AND verification_date=?",
                (action_id, verification_date),
            ).fetchone()
            con.commit()
        assert row is not None
        return dict(row)

    def list_verification_candidates(self, store_id: int, verification_date: str) -> list[dict[str, Any]]:
        """Executed prior-day actions that still need a verification attempt for this date."""
        with self._connect() as con:
            rows = con.execute(
                """SELECT a.*
                   FROM action_items a
                   JOIN action_executions e ON e.action_id=a.id
                   LEFT JOIN action_verifications v
                     ON v.action_id=a.id AND v.verification_date=?
                   WHERE a.store_id=?
                     AND a.business_date<?
                     AND a.status IN ('WAITING_VERIFICATION','REMIND')
                     AND v.id IS NULL
                   ORDER BY a.business_date DESC,a.priority ASC,a.id ASC""",
                (verification_date, store_id, verification_date),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_verification(self, store_id: int, before_date: str | None = None) -> dict[str, Any] | None:
        sql = """SELECT v.*,a.store_id,a.business_date,a.title,a.expected_metric,a.expected_direction
                 FROM action_verifications v
                 JOIN action_items a ON a.id=v.action_id
                 WHERE a.store_id=?"""
        params: list[Any] = [store_id]
        if before_date is not None:
            sql += " AND v.verification_date<=?"
            params.append(before_date)
        sql += " ORDER BY v.verification_date DESC,v.id DESC LIMIT 1"
        with self._connect() as con:
            row = con.execute(sql, params).fetchone()
        return dict(row) if row else None

    def count_pending(self, store_id: int, business_date: str | None = None) -> int:
        sql = """SELECT COUNT(*) FROM action_items
                 WHERE store_id=? AND status IN ('PENDING','REMIND','WAITING_VERIFICATION')"""
        params: list[Any] = [store_id]
        if business_date is not None:
            sql += " AND business_date<=?"
            params.append(business_date)
        with self._connect() as con:
            return int(con.execute(sql, params).fetchone()[0])

    def list_due_reminders(self, now: int) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT r.*,a.title AS action_title,a.reason AS action_reason,m.wechat_openid
                   FROM reminders r
                   JOIN action_items a ON a.id=r.action_id
                   LEFT JOIN mini_users m ON m.user_id=r.user_id
                   WHERE r.status='PENDING' AND r.scheduled_at<=? AND r.error_message IS NULL
                   ORDER BY r.scheduled_at,r.id""",
                (now,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_reminder_delivery(
        self,
        reminder_id: int,
        status: str,
        now: int,
        error_message: str | None = None,
        template_id: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,)).fetchone()
            if row is None:
                raise KeyError("reminder not found")
            if status == "SENT":
                con.execute(
                    """UPDATE reminders SET status='SENT',sent_at=?,error_message=NULL,
                       wechat_template_id=COALESCE(?,wechat_template_id),updated_at=? WHERE id=?""",
                    (now, template_id, now, reminder_id),
                )
            elif status == "FAILED":
                con.execute(
                    "UPDATE reminders SET status='FAILED',error_message=?,updated_at=? WHERE id=?",
                    (error_message or "notification provider failed", now, reminder_id),
                )
            elif status == "SKIPPED":
                # Keep PENDING so the reminder remains visible in-app, but retain
                # the skip reason and exclude it from implicit scheduler retries.
                con.execute(
                    "UPDATE reminders SET error_message=?,updated_at=? WHERE id=?",
                    (error_message or "notification not configured", now, reminder_id),
                )
            else:
                raise ValueError("invalid delivery status")
            updated = con.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,)).fetchone()
            con.commit()
        assert updated is not None
        return dict(updated)

    def list_recent_actions(self, store_id: int, limit: int = 5) -> list[dict[str, Any]]:
        limit = max(0, min(20, int(limit)))
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM action_items WHERE store_id=?
                   ORDER BY business_date DESC,priority ASC,id DESC LIMIT ?""",
                (store_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recent_verifications(self, store_id: int, limit: int = 5) -> list[dict[str, Any]]:
        limit = max(0, min(20, int(limit)))
        with self._connect() as con:
            rows = con.execute(
                """SELECT v.*,a.business_date,a.title,a.expected_metric,a.expected_direction
                   FROM action_verifications v
                   JOIN action_items a ON a.id=v.action_id
                   WHERE a.store_id=?
                   ORDER BY v.verification_date DESC,v.id DESC LIMIT ?""",
                (store_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]
