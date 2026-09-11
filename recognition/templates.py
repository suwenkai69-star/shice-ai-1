from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True)
class TemplateDefinition:
    code: str
    platform: str
    page_type: str
    data_type: str
    channel_code: str | None
    report_scope: str
    cumulative: bool
    required_fields: tuple[str, ...]
    allowed_fields: tuple[str, ...]
    label_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)

_COMMON = ("BUSINESS_DATE","GROSS_SALES","CUSTOMER_PAID","ORDER_COUNT","REFUND_AMOUNT","REFUND_COUNT","MERCHANT_DISCOUNT","PLATFORM_SUBSIDY","PLATFORM_FEE")

def _sales(code, platform, channel, scope="CHANNEL"):
    return TemplateDefinition(code, platform, "DAILY_REPORT", "SALES", channel, scope, True,
                              ("BUSINESS_DATE","GROSS_SALES"), _COMMON, {})

TEMPLATES = {
    ("MEITUAN_DELIVERY","DAILY_REPORT"): _sales("MEITUAN_DELIVERY_DAILY_V1","MEITUAN_DELIVERY","MEITUAN_DELIVERY"),
    ("TAOBAO_FLASH","DAILY_REPORT"): _sales("TAOBAO_FLASH_DAILY_V1","TAOBAO_FLASH","TAOBAO_FLASH"),
    ("JD_DELIVERY","DAILY_REPORT"): _sales("JD_DELIVERY_DAILY_V1","JD_DELIVERY","JD_DELIVERY"),
    ("DOUYIN_LOCAL","DAILY_REPORT"): _sales("DOUYIN_LOCAL_LIFE_DAILY_V1","DOUYIN_LOCAL","DOUYIN_LOCAL"),
    ("MEITUAN_DEALS","DAILY_REPORT"): _sales("MEITUAN_DEALS_DAILY_V1","MEITUAN_DEALS","MEITUAN_DEALS"),
    ("WECHAT_PAY","DAILY_REPORT"): _sales("WECHAT_PAY_DAILY_V1","WECHAT_PAY","WECHAT_PAY"),
    ("ALIPAY","DAILY_REPORT"): _sales("ALIPAY_DAILY_V1","ALIPAY","ALIPAY"),
    ("POS","DAILY_REPORT"): _sales("POS_DAILY_TOTAL_V1","POS",None,"WHOLE_STORE"),
}

def get_template(platform: str, page_type: str) -> TemplateDefinition | None:
    return TEMPLATES.get((platform, page_type))

def get_template_by_code(code: str) -> TemplateDefinition | None:
    for template in TEMPLATES.values():
        if template.code == code:
            return template
    return None
