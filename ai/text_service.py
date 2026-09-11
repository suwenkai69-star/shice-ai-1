from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from persistence.database import DatabaseTarget, coerce_database

import ai.provider as provider_module
from ai.provider import TextProviderUnavailable
from repositories.action_repository import ActionRepository
from services.ai_context_service import AIContext, AIContextService
from services.sufficiency_gate import SufficiencyGate


SUPPORTED_COPY_TYPES = {
    "WECHAT_GROUP": "微信群文案",
    "MOMENTS": "朋友圈文案",
    "XIAOHONGSHU": "小红书文案",
    "DOUYIN_CAPTION": "抖音文案",
    "ACTIVITY_PLAN": "活动方案",
}


def classify_question(question: str) -> str:
    text = (question or "").strip()
    if any(word in text for word in ("人工", "排班", "员工")):
        return "LABOR_DIAGNOSIS"
    if any(word in text for word in ("利润", "赚", "成本")):
        return "PROFIT_DIAGNOSIS"
    if any(word in text for word in ("到账", "对账", "结算", "少给", "没对上")):
        return "RECONCILIATION_DIAGNOSIS"
    if any(word in text for word in ("营业额", "卖得", "订单", "客单", "生意")):
        return "SALES_DIAGNOSIS"
    return "GENERAL"


class AITextService:
    SYSTEM_PROMPT = (
        "你是食策AI的餐饮经营解释助手。只使用给你的结构化上下文回答。"
        "事实、估算和建议必须区分；不得编造数字，不得把估算说成实际，不得自行做正式财务口径决定。"
        "如果涉及高风险经营动作，只提供建议和步骤，必须由老板确认。使用简单中文。"
    )

    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.contexts = AIContextService(self.db)
        self.gate = SufficiencyGate()
        self.actions = ActionRepository(self.db)

    @staticmethod
    def _context_payload(context: AIContext) -> dict:
        return {
            "facts": context.facts,
            "estimates": context.estimates,
            "missing_data": list(context.missing_data),
            "anomalies": list(context.anomalies),
            "reconciliation": context.reconciliation,
            "recent_actions": list(context.recent_actions),
            "recent_verifications": list(context.recent_verifications),
            "benchmarks_used": list(context.benchmarks_used),
        }

    @staticmethod
    def _next_action(question_type: str) -> dict:
        mapping = {
            "LABOR_DIAGNOSIS": {"type": "ADD_LABOR_DATA", "label": "补人工数据"},
            "SALES_DIAGNOSIS": {"type": "UPLOAD_SALES_DATA", "label": "上传今天营业数据"},
            "PROFIT_DIAGNOSIS": {"type": "COMPLETE_COST_DATA", "label": "补成本数据"},
            "RECONCILIATION_DIAGNOSIS": {"type": "UPLOAD_SETTLEMENT_PAYMENT", "label": "补结算/到账数据"},
        }
        return mapping.get(question_type, {"type": "UPLOAD_DATA", "label": "补经营数据"})

    def chat(self, store_id: int, question: str, business_date: str) -> dict:
        qtype = classify_question(question)
        context = self.contexts.build(store_id, question, business_date)
        gate = self.gate.check(qtype, context)
        if not gate.sufficient:
            return {
                "status": "NEEDS_DATA",
                "question_type": qtype,
                "missing_data": list(gate.missing_data),
                "message": "现在还判断不了，先补充这些数据后我再帮你看。",
                "next_action": self._next_action(qtype),
            }
        payload = self._context_payload(context)
        user_prompt = json.dumps(
            {"question": question, "business_date": business_date, "context": payload},
            ensure_ascii=False,
            sort_keys=True,
        )
        answer = provider_module.get_text_provider().generate(self.SYSTEM_PROMPT, user_prompt)
        return {
            "status": "ANSWER",
            "question_type": qtype,
            "answer": answer,
            "data_scope": {"date": business_date, "has_estimates": bool(context.estimates)},
        }

    def generate_copy(self, store_id: int, action_id: int, output_type: str) -> dict:
        output_type = output_type.upper()
        if output_type not in SUPPORTED_COPY_TYPES:
            raise ValueError("unsupported output_type")
        action = self.actions.get_action(action_id, store_id)
        context = self.contexts.build(store_id, action["title"], action["business_date"])
        system = (
            self.SYSTEM_PROMPT
            + "你现在只生成执行草稿，不自动发布。不要承诺结果，不要编造优惠、价格或门店事实。"
        )
        user = json.dumps(
            {
                "output_type": output_type,
                "output_label": SUPPORTED_COPY_TYPES[output_type],
                "action": {
                    "title": action["title"],
                    "reason": action["reason"],
                    "expected_metric": action["expected_metric"],
                },
                "context": self._context_payload(context),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        content = provider_module.get_text_provider().generate(system, user)
        return {
            "status": "DRAFT",
            "output_type": output_type,
            "content": content,
            "auto_published": False,
            "requires_user_review": True,
        }
