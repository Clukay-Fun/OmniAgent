"""
描述: Feishu Agent 全局配置加载器
主要功能:
    - 统一管理应用配置 (Settings)
    - 支持 YAML 文件加载与环境变量覆盖 (Env Override)
    - 提供 Pydantic 类型校验
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


# region 配置模型定义
class ServerSettings(BaseModel):
    """服务器配置"""
    host: str = "0.0.0.0"
    port: int = 8080
    workers: int = 1
    debug: bool = False


class FeishuMessageSettings(BaseModel):
    reply_timeout: int = 30
    use_reply_mode: bool = True


class FeishuSettings(BaseModel):
    """飞书开放平台配置"""
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
    """MCP Server 连接配置"""
    base_url: str = "http://localhost:8081"
    request: MCPRequestSettings = Field(default_factory=MCPRequestSettings)


class PostgresSettings(BaseModel):
    """PostgreSQL 数据库配置"""
    dsn: str = ""
    min_size: int = 1
    max_size: int = 5
    timeout: int = 10


class LLMFallbackSettings(BaseModel):
    enabled: bool = False
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    api_key: str | None = None
    api_base: str | None = None


class LLMSettings(BaseModel):
    """LLM 模型配置"""
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
    """Agent 核心行为配置"""
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
    guide: str = '目前仅支持案件/文档查询，可试试："找一下李四的案子" 或 "1月28号有什么庭要开"。'
    small_talk: str = "你好！我可以帮你查询案件或文档。"
    thanks: str = "不客气！需要查询案件或文档随时告诉我。"
    goodbye: str = "好的，如需查询随时找我。"


class ReplyCaseListSettings(BaseModel):
    title: str = "📌 案件查询结果（共 {count} 条）"
    item: str = (
        "{index}️⃣ {client} vs {opponent}｜{cause}\n"
        "   • 案号：{case_number}\n"
        "   • 法院：{court}\n"
        "   • 程序：{stage}\n"
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
    user_rpm: int = 30
    global_rpm: int = 300
    max_concurrency: int = 10


class HealthDependency(BaseModel):
    name: str
    url: str
    timeout: int = 5


class HealthSettings(BaseModel):
    path: str = "/health"
    check_dependencies: bool = True
    dependencies: list[HealthDependency] = Field(default_factory=list)


class Settings(BaseModel):
    """全局配置聚合根"""
    server: ServerSettings = Field(default_factory=ServerSettings)
    feishu: FeishuSettings = Field(default_factory=FeishuSettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    webhook: WebhookSettings = Field(default_factory=WebhookSettings)
    reply: ReplySettings = Field(default_factory=ReplySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    health: HealthSettings = Field(default_factory=HealthSettings)
# endregion


# region 配置加载逻辑


def _expand_env(value: Any) -> Any:
    """递归展开配置中的环境变量占位符 (${VAR} 或 ${VAR:-default})"""
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
    """读取 YAML 配置文件"""
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
    """
    应用环境变量覆盖
    
    优先级: 显式环境变量 > config.yaml > 默认值
    """
    mapping = {
        "LLM_PROVIDER": ["llm", "provider"],
        "LLM_MODEL": ["llm", "model"],
        "FEISHU_BOT_APP_ID": ["feishu", "app_id"],
        "FEISHU_BOT_APP_SECRET": ["feishu", "app_secret"],
        "FEISHU_BOT_VERIFICATION_TOKEN": ["feishu", "verification_token"],
        "FEISHU_BOT_ENCRYPT_KEY": ["feishu", "encrypt_key"],
        "MCP_SERVER_BASE": ["mcp", "base_url"],
        "POSTGRES_DSN": ["postgres", "dsn"],
        "POSTGRES_MIN_SIZE": ["postgres", "min_size"],
        "POSTGRES_MAX_SIZE": ["postgres", "max_size"],
        "POSTGRES_TIMEOUT": ["postgres", "timeout"],
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
    """加载并验证完整配置"""
    path = Path(config_path or os.getenv("CONFIG_PATH", "config.yaml"))
    data = _load_yaml(path)
    data = _apply_env_overrides(data)
    return Settings.model_validate(data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取单例配置对象 (LRU Cache)"""
    return load_settings()
# endregion
