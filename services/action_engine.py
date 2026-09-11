from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from persistence.database import DatabaseTarget, coerce_database

from repositories.action_repository import ActionRepository
from services.anomaly_engine import Anomaly, AnomalyEngine


@dataclass(frozen=True)
class ActionItem:
    id: int
    store_id: int
    business_date: str
    anomaly_id: int | None
    action_type: str
    title: str
    reason: str
    expected_metric: str
    expected_direction: str
    priority: int
    status: str
    steps: tuple[str, ...]
    requires_user_confirmation: bool = True


_TEMPLATES = {
    "RECONCILIATION_DIFFERENCE": {
        "action_type": "CHECK_RECONCILIATION",
        "title": "核对这笔暂未对上的款项",
        "expected_metric": "RECONCILIATION_DIFF",
        "expected_direction": "DOWN",
        "steps": ("打开对应平台结算记录", "核对结算单号和应结金额", "再核对银行/支付到账记录", "确认后回到食策AI标记已处理"),
    },
    "AOV_LOW": {
        "action_type": "AOV_RECOVERY",
        "title": "先调整低价订单结构",
        "expected_metric": "AOV",
        "expected_direction": "UP",
        "steps": ("先看低价套餐/优惠订单是否变多", "优先主推更适合双人或加购的组合", "不要一次性全店涨价", "执行后明天再看客单价变化"),
    },
    "LABOR_HIGH": {
        "action_type": "LABOR_EFFICIENCY",
        "title": "检查低效时段排班",
        "expected_metric": "LABOR_COST_RATE",
        "expected_direction": "DOWN",
        "steps": ("先确认低效时段", "只调整低峰排班，不直接大幅减员", "保留高峰承载能力", "第二天复查人工效率和营业额"),
    },
    "SALES_LOW": {
        "action_type": "SALES_RECOVERY",
        "title": "先找营业额下降发生在哪",
        "expected_metric": "SALES",
        "expected_direction": "UP",
        "steps": ("先看订单数和客单价谁在下降", "再看主要渠道和时段", "只针对最明显的一处做小调整", "第二天按同口径复查"),
    },
}


class ActionEngine:
    VERSION = "ACTION_V1"

    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.repo = ActionRepository(self.db)
        self.anomalies = AnomalyEngine(self.db)

    @staticmethod
    def _template(issue: Anomaly) -> dict:
        return _TEMPLATES.get(
            issue.anomaly_type,
            {
                "action_type": "REVIEW_ISSUE",
                "title": "先把这个问题看清楚",
                "expected_metric": issue.metric_code,
                "expected_direction": "STABLE",
                "steps": ("查看问题详情和数据依据", "如果数据不足先补数据", "确认后再决定是否调整经营动作"),
            },
        )

    @classmethod
    def _from_row(cls, row: dict) -> ActionItem:
        anomaly_type = row.get("anomaly_type")
        steps = ()
        if anomaly_type:
            fake = type("IssueRef", (), {"anomaly_type": anomaly_type, "metric_code": row["expected_metric"]})()
            steps = tuple(cls._template(fake)["steps"])
        else:
            for template in _TEMPLATES.values():
                if template["action_type"] == row["action_type"]:
                    steps = tuple(template["steps"])
                    break
        if not steps:
            steps = ("查看数据依据", "确认后再执行", "执行后回到食策AI标记完成")
        return ActionItem(
            id=int(row["id"]),
            store_id=int(row["store_id"]),
            business_date=str(row["business_date"]),
            anomaly_id=int(row["anomaly_id"]) if row.get("anomaly_id") is not None else None,
            action_type=str(row["action_type"]),
            title=str(row["title"]),
            reason=str(row["reason"]),
            expected_metric=str(row["expected_metric"]),
            expected_direction=str(row["expected_direction"]),
            priority=int(row["priority"]),
            status=str(row["status"]),
            steps=steps,
        )

    def generate(self, store_id: int, business_date: str) -> list[ActionItem]:
        self.anomalies.detect(store_id, business_date)
        issues = self.anomalies.top_issues(store_id, business_date, 3)
        actions: list[ActionItem] = []
        for priority, issue in enumerate(issues, start=1):
            template = self._template(issue)
            row = self.repo.create_action(
                store_id=store_id,
                business_date=business_date,
                anomaly_id=issue.id,
                action_type=template["action_type"],
                title=template["title"],
                reason=issue.user_message,
                expected_metric=template["expected_metric"],
                expected_direction=template["expected_direction"],
                priority=priority,
            )
            enriched = dict(row)
            enriched["anomaly_type"] = issue.anomaly_type
            actions.append(self._from_row(enriched))
        return actions[:3]

    def get(self, action_id: int, store_id: int) -> ActionItem:
        return self._from_row(self.repo.get_action(action_id, store_id))

    def execute(self, action_id: int, store_id: int, executed_at: int | None = None, note: str | None = None) -> ActionItem:
        row = self.repo.mark_executed(
            action_id,
            store_id,
            executed_at=executed_at if executed_at is not None else int(time.time() * 1000),
            note=note,
        )
        return self._from_row(row)

    def skip(self, action_id: int, store_id: int) -> ActionItem:
        return self._from_row(self.repo.mark_skipped(action_id, store_id))

    def remind(self, action_id: int, user_id: int, store_id: int, scheduled_at: int) -> dict:
        return self.repo.schedule_reminder(action_id, user_id, store_id, scheduled_at)
