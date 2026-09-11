from __future__ import annotations

CLOUD_SCHEMA_VERSION = 1

STANDARD_CHANNELS = (
    ('WECHAT_PAY', '微信支付', 'OFFLINE'),
    ('ALIPAY', '支付宝', 'OFFLINE'),
    ('CASH', '现金', 'OFFLINE'),
    ('MEITUAN_DELIVERY', '美团外卖', 'DELIVERY'),
    ('MEITUAN_FLASH', '美团闪购', 'DELIVERY'),
    ('JD_DELIVERY', '京东外卖', 'DELIVERY'),
    ('DOUYIN_LOCAL', '抖音生活服务', 'LOCAL_LIFE'),
    ('MEITUAN_DEALS', '美团团购', 'LOCAL_LIFE'),
    ('OTHER', '其他', 'OTHER'),
    ('TAOBAO_FLASH', '淘宝闪购', 'DELIVERY'),
)

UPLOAD_STATUSES = (
    'UPLOADED','PROCESSING','NEEDS_CONFIRMATION','CONFIRMED','IMPORTED','FAILED','REJECTED'
)

_REQUIRED = {
    'schema_meta','users','stores','channels','import_files','import_batches','raw_import_rows',
    'daily_channel_sales','settlements','platform_fees','payments','reconciliation_matches',
    'mini_users','store_profiles','daily_store_sales_totals','store_cost_profiles','cost_entries',
    'industry_benchmarks','profit_snapshots','upload_documents','upload_drafts','extraction_jobs',
    'extracted_fields','document_import_links','store_data_sources','daily_data_status','anomaly_events',
    'action_items','action_executions','action_verifications','reminders','ai_conversations','ai_messages',
}


def required_cloud_tables() -> set[str]:
    return set(_REQUIRED)
