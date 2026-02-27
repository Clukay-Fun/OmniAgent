"""
描述: MCP Server 全局配置加载器
主要功能:
    - 统一管理 MCP Server 配置
    - 支持 YAML 文件加载与环境变量覆盖
    - 提供 Bitable 与 Doc 等业务配置模型
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")

REQUIRED_AGENT_MCP_TOOLS: tuple[str, ...] = (
    "feishu.v1.bitable.list_tables",
    "feishu.v1.bitable.search",
    "feishu.v1.bitable.search_exact",
    "feishu.v1.bitable.search_keyword",
    "feishu.v1.bitable.search_person",
    "feishu.v1.bitable.search_date_range",
    "feishu.v1.bitable.search_advanced",
    "feishu.v1.bitable.record.get",
    "feishu.v1.bitable.record.create",
    "feishu.v1.bitable.record.update",
    "feishu.v1.bitable.record.delete",
    "feishu.v1.doc.search",
)


# region 基础配置模型
class ServerSettings(BaseModel):
    """服务监听配置"""
    host: str = "0.0.0.0"
    port: int = 8081
    workers: int = 1
    debug: bool = False


class TokenSettings(BaseModel):
    refresh_ahead_seconds: int = 300


class RequestSettings(BaseModel):
    timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0


class FeishuSettings(BaseModel):
    """飞书开放平台配置"""
    app_id: str = ""
    app_secret: str = ""
    api_base: str = "https://open.feishu.cn/open-apis"
    token: TokenSettings = Field(default_factory=TokenSettings)
    request: RequestSettings = Field(default_factory=RequestSettings)


class BitableSearchSettings(BaseModel):
    searchable_fields: list[str] = Field(default_factory=list)
    max_records: int = 100
    default_limit: int = 20


class BitableSettings(BaseModel):
    """多维表格 (Bitable) 业务配置"""
    domain: str = "your-company"
    default_app_token: str = ""
    default_table_id: str = ""
    default_view_id: str | None = None
    field_mapping: dict[str, str] = Field(default_factory=dict)
    search: BitableSearchSettings = Field(default_factory=BitableSearchSettings)


class DocSearchSettings(BaseModel):
    default_folder_token: str | None = None
    preview_length: int = 200
    default_limit: int = 10


class DocSettings(BaseModel):
    """云文档 (Doc) 业务配置"""
    search: DocSearchSettings = Field(default_factory=DocSearchSettings)


class CalendarSettings(BaseModel):
    """飞书日历配置"""
    default_calendar_id: str = ""
    timezone: str = "Asia/Shanghai"
    default_duration_minutes: int = 30


class ToolsSettings(BaseModel):
    enabled: list[str] = Field(default_factory=list)


class LoggingFileSettings(BaseModel):
    enabled: bool = False
    path: str = "logs/mcp-server.log"
    max_size_mb: int = 100
    backup_count: int = 5


class LoggingOutputSettings(BaseModel):
    console: bool = True
    file: LoggingFileSettings = Field(default_factory=LoggingFileSettings)


class LoggingSettings(BaseModel):
    """日志系统配置"""
    level: str = "INFO"
    format: str = "json"
    output: LoggingOutputSettings = Field(default_factory=LoggingOutputSettings)


class SecuritySettings(BaseModel):
    verify_source: bool = False
    allowed_ips: list[str] = Field(default_factory=list)


class AutomationSettings(BaseModel):
    """自动化模块配置"""

    enabled: bool = False
    verification_token: str = ""
    encrypt_key: str = ""
    storage_dir: str = "automation_data"
    rules_file: str = "automation_rules.yaml"
    event_ttl_seconds: int = 604800
    business_ttl_seconds: int = 604800
    max_dedupe_keys: int = 50000
    scan_page_size: int = 100
    max_scan_pages: int = 50
    trigger_on_new_record_event: bool = False
    trigger_on_new_record_scan: bool = True
    trigger_on_new_record_scan_requires_checkpoint: bool = True
    new_record_scan_max_trigger_per_run: int = 50
    sync_deletions_enabled: bool = True
    sync_deletions_max_per_run: int = 200
    poller_enabled: bool = False
    poller_interval_seconds: float = 60.0
    action_max_retries: int = 1
    action_retry_delay_seconds: float = 0.5
    dead_letter_file: str = "automation_data/dead_letters.jsonl"
    run_log_file: str = "automation_data/run_logs.jsonl"
    delay_queue_file: str = "automation_data/delay_queue.jsonl"
    delay_scheduler_enabled: bool = True
    delay_scheduler_interval_seconds: float = 5.0
    delay_task_retention_seconds: float = 86400.0
    delay_max_seconds: float = 2592000.0
    cron_queue_file: str = "automation_data/cron_queue.jsonl"
    cron_scheduler_enabled: bool = True
    cron_poll_interval_seconds: float = 30.0
    cron_max_consecutive_failures: int = 3
    schema_sync_enabled: bool = True
    schema_poller_enabled: bool = False
    schema_sync_interval_seconds: float = 300.0
    schema_sync_event_driven: bool = True
    schema_cache_file: str = "automation_data/schema_cache.json"
    schema_runtime_state_file: str = "automation_data/schema_runtime_state.json"
    schema_webhook_enabled: bool = False
    schema_webhook_url: str = ""
    schema_webhook_secret: str = ""
    schema_webhook_timeout_seconds: float = 5.0
    schema_webhook_drill_enabled: bool = False
    schema_policy_on_field_added: str = "auto_map_if_same_name"
    schema_policy_on_field_removed: str = "auto_remove"
    schema_policy_on_field_renamed: str = "warn_only"
    schema_policy_on_field_type_changed: str = "warn_only"
    schema_policy_on_trigger_field_removed: str = "disable_rule"
    webhook_enabled: bool = False
    webhook_api_key: str = ""
    webhook_signature_secret: str = ""
    webhook_timestamp_tolerance_seconds: int = 300
    notify_webhook_url: str = ""
    notify_api_key: str = ""
    notify_timeout_seconds: float = 5.0
    http_allowed_domains: list[str] = Field(default_factory=list)
    http_timeout_seconds: float = 10.0
    status_write_enabled: bool = False
    status_field: str = "自动化_执行状态"
    error_field: str = "自动化_最近错误"


class Settings(BaseModel):
    """MCP Server 配置聚合根"""
    server: ServerSettings = Field(default_factory=ServerSettings)
    feishu: FeishuSettings = Field(default_factory=FeishuSettings)
    bitable: BitableSettings = Field(default_factory=BitableSettings)
    doc: DocSettings = Field(default_factory=DocSettings)
    calendar: CalendarSettings = Field(default_factory=CalendarSettings)
    tools: ToolsSettings = Field(default_factory=ToolsSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)
# endregion


# region 配置加载逻辑


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            expr = match.group(1)
            if ":-" in expr:
                key, default = expr.split(":-", 1)
                return os.getenv(key, default)
            return os.getenv(expr, "")

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(val) for key, val in value.items()}
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _expand_env(data)


def _extract_enabled_tools(config_data: dict[str, Any]) -> set[str]:
    tools_data = config_data.get("tools")
    if not isinstance(tools_data, dict):
        return set()
    enabled = tools_data.get("enabled")
    if not isinstance(enabled, list):
        return set()
    return {str(name).strip() for name in enabled if str(name).strip()}


def _set_nested(data: dict[str, Any], keys: list[str], value: Any) -> None:
    current = data
    for key in keys[:-1]:
        current = current.setdefault(key, {})
    current[keys[-1]] = value


def _parse_env_override(env_key: str, env_value: str) -> Any:
    if env_key == "AUTOMATION_HTTP_ALLOWED_DOMAINS":
        return [item.strip() for item in env_value.split(",") if item.strip()]
    return env_value


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "FEISHU_DATA_APP_ID": ["feishu", "app_id"],
        "FEISHU_DATA_APP_SECRET": ["feishu", "app_secret"],
        "BITABLE_DOMAIN": ["bitable", "domain"],
        "BITABLE_APP_TOKEN": ["bitable", "default_app_token"],
        "BITABLE_TABLE_ID": ["bitable", "default_table_id"],
        "BITABLE_VIEW_ID": ["bitable", "default_view_id"],
        "DOC_FOLDER_TOKEN": ["doc", "search", "default_folder_token"],
        "FEISHU_CALENDAR_ID": ["calendar", "default_calendar_id"],
        "FEISHU_CALENDAR_TIMEZONE": ["calendar", "timezone"],
        "FEISHU_CALENDAR_DEFAULT_DURATION_MINUTES": ["calendar", "default_duration_minutes"],
        "AUTOMATION_ENABLED": ["automation", "enabled"],
        "FEISHU_EVENT_VERIFY_TOKEN": ["automation", "verification_token"],
        "FEISHU_EVENT_ENCRYPT_KEY": ["automation", "encrypt_key"],
        "AUTOMATION_STORAGE_DIR": ["automation", "storage_dir"],
        "AUTOMATION_RULES_FILE": ["automation", "rules_file"],
        "AUTOMATION_EVENT_TTL_SECONDS": ["automation", "event_ttl_seconds"],
        "AUTOMATION_BUSINESS_TTL_SECONDS": ["automation", "business_ttl_seconds"],
        "AUTOMATION_MAX_DEDUPE_KEYS": ["automation", "max_dedupe_keys"],
        "AUTOMATION_SCAN_PAGE_SIZE": ["automation", "scan_page_size"],
        "AUTOMATION_MAX_SCAN_PAGES": ["automation", "max_scan_pages"],
        "AUTOMATION_TRIGGER_ON_NEW_RECORD_EVENT": ["automation", "trigger_on_new_record_event"],
        "AUTOMATION_TRIGGER_ON_NEW_RECORD_SCAN": ["automation", "trigger_on_new_record_scan"],
        "AUTOMATION_TRIGGER_ON_NEW_RECORD_SCAN_REQUIRES_CHECKPOINT": [
            "automation",
            "trigger_on_new_record_scan_requires_checkpoint",
        ],
        "AUTOMATION_NEW_RECORD_SCAN_MAX_TRIGGER_PER_RUN": [
            "automation",
            "new_record_scan_max_trigger_per_run",
        ],
        "AUTOMATION_SYNC_DELETIONS_ENABLED": ["automation", "sync_deletions_enabled"],
        "AUTOMATION_SYNC_DELETIONS_MAX_PER_RUN": ["automation", "sync_deletions_max_per_run"],
        "AUTOMATION_POLLER_ENABLED": ["automation", "poller_enabled"],
        "AUTOMATION_POLLER_INTERVAL_SECONDS": ["automation", "poller_interval_seconds"],
        "AUTOMATION_ACTION_MAX_RETRIES": ["automation", "action_max_retries"],
        "AUTOMATION_ACTION_RETRY_DELAY_SECONDS": ["automation", "action_retry_delay_seconds"],
        "AUTOMATION_DEAD_LETTER_FILE": ["automation", "dead_letter_file"],
        "AUTOMATION_RUN_LOG_FILE": ["automation", "run_log_file"],
        "AUTOMATION_DELAY_QUEUE_FILE": ["automation", "delay_queue_file"],
        "AUTOMATION_DELAY_SCHEDULER_ENABLED": ["automation", "delay_scheduler_enabled"],
        "AUTOMATION_DELAY_SCHEDULER_INTERVAL_SECONDS": ["automation", "delay_scheduler_interval_seconds"],
        "AUTOMATION_DELAY_TASK_RETENTION_SECONDS": ["automation", "delay_task_retention_seconds"],
        "AUTOMATION_DELAY_MAX_SECONDS": ["automation", "delay_max_seconds"],
        "AUTOMATION_CRON_QUEUE_FILE": ["automation", "cron_queue_file"],
        "AUTOMATION_CRON_SCHEDULER_ENABLED": ["automation", "cron_scheduler_enabled"],
        "AUTOMATION_CRON_POLL_INTERVAL_SECONDS": ["automation", "cron_poll_interval_seconds"],
        "AUTOMATION_CRON_MAX_CONSECUTIVE_FAILURES": ["automation", "cron_max_consecutive_failures"],
        "AUTOMATION_SCHEMA_SYNC_ENABLED": ["automation", "schema_sync_enabled"],
        "AUTOMATION_SCHEMA_POLLER_ENABLED": ["automation", "schema_poller_enabled"],
        "AUTOMATION_SCHEMA_SYNC_INTERVAL_SECONDS": ["automation", "schema_sync_interval_seconds"],
        "AUTOMATION_SCHEMA_SYNC_EVENT_DRIVEN": ["automation", "schema_sync_event_driven"],
        "AUTOMATION_SCHEMA_CACHE_FILE": ["automation", "schema_cache_file"],
        "AUTOMATION_SCHEMA_RUNTIME_STATE_FILE": ["automation", "schema_runtime_state_file"],
        "AUTOMATION_SCHEMA_WEBHOOK_ENABLED": ["automation", "schema_webhook_enabled"],
        "AUTOMATION_SCHEMA_WEBHOOK_URL": ["automation", "schema_webhook_url"],
        "AUTOMATION_SCHEMA_WEBHOOK_SECRET": ["automation", "schema_webhook_secret"],
        "AUTOMATION_SCHEMA_WEBHOOK_TIMEOUT_SECONDS": ["automation", "schema_webhook_timeout_seconds"],
        "AUTOMATION_SCHEMA_WEBHOOK_DRILL_ENABLED": ["automation", "schema_webhook_drill_enabled"],
        "AUTOMATION_SCHEMA_POLICY_ON_FIELD_ADDED": ["automation", "schema_policy_on_field_added"],
        "AUTOMATION_SCHEMA_POLICY_ON_FIELD_REMOVED": ["automation", "schema_policy_on_field_removed"],
        "AUTOMATION_SCHEMA_POLICY_ON_FIELD_RENAMED": ["automation", "schema_policy_on_field_renamed"],
        "AUTOMATION_SCHEMA_POLICY_ON_FIELD_TYPE_CHANGED": ["automation", "schema_policy_on_field_type_changed"],
        "AUTOMATION_SCHEMA_POLICY_ON_TRIGGER_FIELD_REMOVED": [
            "automation",
            "schema_policy_on_trigger_field_removed",
        ],
        "AUTOMATION_WEBHOOK_ENABLED": ["automation", "webhook_enabled"],
        "AUTOMATION_WEBHOOK_API_KEY": ["automation", "webhook_api_key"],
        "AUTOMATION_WEBHOOK_SIGNATURE_SECRET": ["automation", "webhook_signature_secret"],
        "AUTOMATION_WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS": [
            "automation",
            "webhook_timestamp_tolerance_seconds",
        ],
        "AUTOMATION_NOTIFY_WEBHOOK_URL": ["automation", "notify_webhook_url"],
        "AUTOMATION_NOTIFY_API_KEY": ["automation", "notify_api_key"],
        "AUTOMATION_NOTIFY_TIMEOUT_SECONDS": ["automation", "notify_timeout_seconds"],
        "AUTOMATION_HTTP_ALLOWED_DOMAINS": ["automation", "http_allowed_domains"],
        "AUTOMATION_HTTP_TIMEOUT_SECONDS": ["automation", "http_timeout_seconds"],
        "AUTOMATION_STATUS_WRITE_ENABLED": ["automation", "status_write_enabled"],
        "AUTOMATION_STATUS_FIELD": ["automation", "status_field"],
        "AUTOMATION_ERROR_FIELD": ["automation", "error_field"],
    }
    for env_key, path in mapping.items():
        env_value = os.getenv(env_key)
        if env_value is not None and env_value != "":
            _set_nested(data, path, _parse_env_override(env_key, env_value))
    return data


def check_tool_config_consistency(
    settings: Settings,
    runtime_config_path: str | None = None,
    example_config_path: str | None = None,
    required_tools: set[str] | None = None,
) -> dict[str, Any]:
    required = set(required_tools or REQUIRED_AGENT_MCP_TOOLS)
    runtime_tools = {str(name).strip() for name in settings.tools.enabled if str(name).strip()}
    runtime_missing = sorted(required - runtime_tools)

    runtime_path = Path(runtime_config_path or os.getenv("CONFIG_PATH", "config.yaml"))
    example_path = Path(example_config_path or "config.yaml.example")

    example_exists = example_path.exists()
    example_tools = _extract_enabled_tools(_load_yaml(example_path)) if example_exists else set()
    example_missing = sorted(required - example_tools) if example_exists else []

    return {
        "required_tools": sorted(required),
        "runtime_config_path": str(runtime_path),
        "runtime_enabled": sorted(runtime_tools),
        "runtime_missing": runtime_missing,
        "example_config_path": str(example_path),
        "example_exists": example_exists,
        "example_enabled": sorted(example_tools),
        "example_missing": example_missing,
        "ok": not runtime_missing and (not example_exists or not example_missing),
    }


def load_settings(config_path: str | None = None) -> Settings:
    path = Path(config_path or os.getenv("CONFIG_PATH", "config.yaml"))
    data = _load_yaml(path)
    data = _apply_env_overrides(data)
    return Settings.model_validate(data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取单例配置对象"""
    return load_settings()
# endregion
