"""
Feishu Agent configuration loader.
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


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    workers: int = 1
    debug: bool = False


class FeishuMessageSettings(BaseModel):
    reply_timeout: int = 30
    use_reply_mode: bool = True


class FeishuSettings(BaseModel):
    app_id: str = ""
    app_secret: str = ""
    verification_token: str = ""
    encrypt_key: str | None = None
    api_base: str = "https://open.feishu.cn/open-apis"
    message: FeishuMessageSettings = Field(default_factory=FeishuMessageSettings)


class MCPRequestSettings(BaseModel):
    timeout: int = 30
    max_retries: int = 2
    retry_delay: float = 1.0


class MCPSettings(BaseModel):
    base_url: str = "http://localhost:8081"
    request: MCPRequestSettings = Field(default_factory=MCPRequestSettings)


class LLMFallbackSettings(BaseModel):
    enabled: bool = False
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    api_key: str | None = None
    api_base: str | None = None


class LLMSettings(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    api_base: str | None = None
    temperature: float = 0.3
    max_tokens: int = 2000
    timeout: int = 60
    max_retries: int = 2
    fallback: LLMFallbackSettings = Field(default_factory=LLMFallbackSettings)


class PromptSettings(BaseModel):
    role: str = ""
    capabilities: str = ""
    constraints: str = ""
    output_format: str = ""


class ToolSettings(BaseModel):
    max_iterations: int = 5
    parallel_calls: bool = False


class AgentSettings(BaseModel):
    name: str = "feishu-case-assistant"
    prompt: PromptSettings = Field(default_factory=PromptSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)


class CleanupSettings(BaseModel):
    interval_seconds: int = 300
    enabled: bool = True


class SessionSettings(BaseModel):
    max_rounds: int = 5
    ttl_minutes: int = 30
    max_context_tokens: int = 4000
    cleanup: CleanupSettings = Field(default_factory=CleanupSettings)


class WebhookDedupSettings(BaseModel):
    enabled: bool = True
    ttl_seconds: int = 300
    max_size: int = 10000


class WebhookFilterSettings(BaseModel):
    allowed_message_types: list[str] = Field(default_factory=lambda: ["text"])
    private_chat_only: bool = True
    ignore_bot_message: bool = True


class WebhookSettings(BaseModel):
    path: str = "/feishu/webhook"
    dedup: WebhookDedupSettings = Field(default_factory=WebhookDedupSettings)
    filter: WebhookFilterSettings = Field(default_factory=WebhookFilterSettings)


class ReplyTemplateSettings(BaseModel):
    no_result: str = "未找到相关记录，请尝试调整查询条件。"
    error: str = "抱歉，处理请求时遇到问题：{message}"
    timeout: str = "思考超时，请简化问题后重试。"
    welcome: str = "你好！我是案件助手。"


class ReplyCaseListSettings(BaseModel):
    title: str = "📅 {period}庭审安排（共 {count} 场）"
    item: str = (
        "{index}️⃣ {client} vs {opponent} | {cause}\n"
        "   • 案号：{case_number}\n"
        "   • 时间：{hearing_date}\n"
        "   • 法院：{court}\n"
        "   • 🔗 查看详情：{record_url}"
    )


class ReplySettings(BaseModel):
    templates: ReplyTemplateSettings = Field(default_factory=ReplyTemplateSettings)
    case_list: ReplyCaseListSettings = Field(default_factory=ReplyCaseListSettings)


class LoggingFileSettings(BaseModel):
    enabled: bool = False
    path: str = "logs/feishu-agent.log"
    max_size_mb: int = 100
    backup_count: int = 5


class LoggingOutputSettings(BaseModel):
    console: bool = True
    file: LoggingFileSettings = Field(default_factory=LoggingFileSettings)


class LoggingMaskSettings(BaseModel):
    enabled: bool = True
    fields: list[str] = Field(default_factory=list)


class LoggingSettings(BaseModel):
    level: str = "INFO"
    format: str = "json"
    output: LoggingOutputSettings = Field(default_factory=LoggingOutputSettings)
    mask: LoggingMaskSettings = Field(default_factory=LoggingMaskSettings)


class RateLimitSettings(BaseModel):
    enabled: bool = True
    user_rpm: int = 20
    global_rpm: int = 100
    max_concurrency: int = 3


class HealthDependency(BaseModel):
    name: str
    url: str
    timeout: int = 5


class HealthSettings(BaseModel):
    path: str = "/health"
    check_dependencies: bool = True
    dependencies: list[HealthDependency] = Field(default_factory=list)


class Settings(BaseModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    feishu: FeishuSettings = Field(default_factory=FeishuSettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    webhook: WebhookSettings = Field(default_factory=WebhookSettings)
    reply: ReplySettings = Field(default_factory=ReplySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    health: HealthSettings = Field(default_factory=HealthSettings)


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
        "FEISHU_APP_ID": ["feishu", "app_id"],
        "FEISHU_APP_SECRET": ["feishu", "app_secret"],
        "FEISHU_VERIFICATION_TOKEN": ["feishu", "verification_token"],
        "FEISHU_ENCRYPT_KEY": ["feishu", "encrypt_key"],
        "MCP_SERVER_BASE": ["mcp", "base_url"],
        "LLM_API_KEY": ["llm", "api_key"],
        "LLM_API_BASE": ["llm", "api_base"],
        "LLM_FALLBACK_API_KEY": ["llm", "fallback", "api_key"],
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
    return load_settings()
