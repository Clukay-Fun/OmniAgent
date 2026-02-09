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


def _set_nested(data: dict[str, Any], keys: list[str], value: Any) -> None:
    current = data
    for key in keys[:-1]:
        current = current.setdefault(key, {})
    current[keys[-1]] = value


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
        "AUTOMATION_STATUS_FIELD": ["automation", "status_field"],
        "AUTOMATION_ERROR_FIELD": ["automation", "error_field"],
    }
    for env_key, path in mapping.items():
        env_value = os.getenv(env_key)
        if env_value is not None and env_value != "":
            _set_nested(data, path, env_value)
    return data


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
