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
    org_b_app_id: str = ""
    org_b_app_secret: str = ""
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
    model_primary: str = ""
    model_secondary: str = ""
    api_key: str = ""
    api_base: str | None = None
    temperature: float = 0.3
    max_tokens: int = 2000
    timeout: int = 60
    max_retries: int = 2
    fallback: LLMFallbackSettings = Field(default_factory=LLMFallbackSettings)


# ============================================
# region 任务模型配置
# ============================================
class TaskLLMSettings(BaseModel):
    """任务模型配置（意图识别/工具调用专用）"""
    enabled: bool = False
    provider: str = "minimax"
    model: str = "MiniMax-M2.5"
    api_key: str = ""
    api_base: str | None = None
    temperature: float = 0.1
    max_tokens: int = 1000
    timeout: int = 30
# endregion
# ============================================


class PromptSettings(BaseModel):
    role: str = ""
    capabilities: str = ""
    constraints: str = ""
    output_format: str = ""


class ToolSettings(BaseModel):
    max_iterations: int = 5
    parallel_calls: bool = False


class UserIdentitySettings(BaseModel):
    """用户身份管理配置"""
    auto_match: bool = True
    """是否自动匹配身份"""
    
    match_field: str = "主办律师"
    """匹配字段名，支持逗号分隔多字段，如 "主办律师,协办律师"，顺序搜索直到找到匹配为止"""
    
    min_confidence: float = 0.8
    """最小匹配置信度"""
    
    prompt_bind_on_fail: bool = True
    """匹配失败时是否提示绑定"""


class UserCacheSettings(BaseModel):
    """用户缓存配置"""
    ttl_hours: int = 24
    """缓存有效期（小时）"""
    
    max_size: int = 1000
    """最大缓存条目数"""


class UserSettings(BaseModel):
    """用户管理配置"""
    identity: UserIdentitySettings = Field(default_factory=UserIdentitySettings)
    cache: UserCacheSettings = Field(default_factory=UserCacheSettings)


class HearingReminderSettings(BaseModel):
    """开庭日提醒配置"""
    enabled: bool = True
    """是否启用开庭日提醒"""
    
    reminder_chat_id: str = ""
    """提醒接收者 chat_id"""
    
    reminder_offsets: list[int] = Field(default_factory=lambda: [7, 3, 1, 0])
    """提醒提前天数列表（默认 7/3/1/0 天）"""
    
    scan_hour: int = 8
    """每日扫描时间（小时）"""
    
    scan_minute: int = 0
    """每日扫描时间（分钟）"""


class MidtermMemorySettings(BaseModel):
    """中期记忆配置（SQLite + 可选上下文注入）"""
    sqlite_path: str = "workspace/memory/midterm_memory.sqlite3"
    inject_to_llm: bool = False
    llm_recent_limit: int = 6
    llm_max_chars: int = 240


class AgentSettings(BaseModel):
    """Agent 核心行为配置"""
    name: str = "feishu-case-assistant"
    prompt: PromptSettings = Field(default_factory=PromptSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    midterm_memory: MidtermMemorySettings = Field(default_factory=MidtermMemorySettings)


class FilePipelineSettings(BaseModel):
    enabled: bool = False
    max_bytes: int = 5 * 1024 * 1024
    timeout_seconds: int = 12
    metrics_enabled: bool = True


class FileExtractorSettings(BaseModel):
    enabled: bool = False
    provider: str = "none"
    api_key: str | None = None
    api_base: str | None = None
    mineru_path: str = "/v1/convert"
    llm_path: str = "/v1/document/convert"
    auth_style: str = "bearer"
    api_key_header: str = "X-API-Key"
    api_key_prefix: str = "Bearer "
    fail_open: bool = True


class FileContextSettings(BaseModel):
    injection_enabled: bool = False
    max_chars: int = 2000
    max_tokens: int = 500


class UsageLogSettings(BaseModel):
    enabled: bool = False
    path: str = "workspace/usage/usage_log-{date}.jsonl"
    fail_open: bool = True
    model_pricing_path: str = ""
    model_pricing_json: str = ""


class ABRoutingSettings(BaseModel):
    enabled: bool = False
    ratio: float = 0.0
    model_a: str | None = None
    model_b: str | None = None


class CostMonitorSettings(BaseModel):
    alert_hourly_threshold: float = 5.0
    alert_daily_threshold: float = 50.0
    circuit_breaker_enabled: bool = False


class OCRSettings(BaseModel):
    enabled: bool = False
    provider: str = "none"
    api_key: str | None = None
    api_base: str | None = None
    mineru_path: str = "/v1/convert"
    llm_path: str = "/v1/document/convert"
    auth_style: str = "bearer"
    api_key_header: str = "X-API-Key"
    api_key_prefix: str = "Bearer "


class ASRSettings(BaseModel):
    enabled: bool = False
    provider: str = "none"
    api_key: str | None = None
    api_base: str | None = None
    mineru_path: str = "/v1/convert"
    llm_path: str = "/v1/document/convert"
    auth_style: str = "bearer"
    api_key_header: str = "X-API-Key"
    api_key_prefix: str = "Bearer "


class CleanupSettings(BaseModel):
    interval_seconds: int = 300
    enabled: bool = True


class SessionSettings(BaseModel):
    max_rounds: int = 5
    ttl_minutes: int = 30
    max_context_tokens: int = 4000
    cleanup: CleanupSettings = Field(default_factory=CleanupSettings)


class RedisStateStoreSettings(BaseModel):
    dsn: str = ""
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str | None = None
    key_prefix: str = "omniagent:state:"
    socket_timeout_seconds: float = 1.0


class StateStoreSettings(BaseModel):
    backend: str = "memory"
    redis: RedisStateStoreSettings = Field(default_factory=RedisStateStoreSettings)


class WebhookDedupSettings(BaseModel):
    enabled: bool = True
    ttl_seconds: int = 300
    max_size: int = 10000


class WebhookFilterSettings(BaseModel):
    allowed_message_types: list[str] = Field(default_factory=lambda: ["text"])
    private_chat_only: bool = True
    ignore_bot_message: bool = True


class WebhookEventSettings(BaseModel):
    enabled_types: list[str] = Field(
        default_factory=lambda: [
            "im.message.receive_v1",
            "im.chat.access_event.bot_p2p_chat_entered_v1",
            "im.chat.member.bot.added_v1",
            "drive.file.bitable_field_changed_v1",
            "drive.file.bitable_record_changed_v1",
            "calendar.calendar.changed_v4",
            "calendar.calendar.event.changed_v4",
        ]
    )


class WebhookChunkAssemblerSettings(BaseModel):
    enabled: bool = False
    window_seconds: float = 3.0
    stale_window_seconds: float = 10.0
    max_segments: int = 5
    max_chars: int = 500


class WebhookSettings(BaseModel):
    path: str = "/feishu/webhook"
    dedup: WebhookDedupSettings = Field(default_factory=WebhookDedupSettings)
    filter: WebhookFilterSettings = Field(default_factory=WebhookFilterSettings)
    events: WebhookEventSettings = Field(default_factory=WebhookEventSettings)
    chunk_assembler: WebhookChunkAssemblerSettings = Field(default_factory=WebhookChunkAssemblerSettings)


class AutomationNotifySettings(BaseModel):
    enabled: bool = False
    api_key: str = ""


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
    card_enabled: bool = True
    reaction_enabled: bool = True
    query_card_v2_enabled: bool = False
    reply_personalization_enabled: bool = False


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
    reminder_scheduler_enabled: bool = False
    crud_delete_enabled: bool = False
    automation_dry_run: bool = True
    reminder_scan_enabled: bool = False
    reminder_scan_interval_minutes: int = 60
    daily_digest_enabled: bool = False
    daily_digest_schedule: str = "09:00"
    daily_digest_timezone: str = "Asia/Shanghai"
    llm: LLMSettings = Field(default_factory=LLMSettings)
    task_llm: TaskLLMSettings = Field(default_factory=TaskLLMSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    user: UserSettings = Field(default_factory=UserSettings)
    hearing_reminder: HearingReminderSettings = Field(default_factory=HearingReminderSettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    state_store: StateStoreSettings = Field(default_factory=StateStoreSettings)
    file_pipeline: FilePipelineSettings = Field(default_factory=FilePipelineSettings)
    file_extractor: FileExtractorSettings = Field(default_factory=FileExtractorSettings)
    file_context: FileContextSettings = Field(default_factory=FileContextSettings)
    usage_log: UsageLogSettings = Field(default_factory=UsageLogSettings)
    ab_routing: ABRoutingSettings = Field(default_factory=ABRoutingSettings)
    cost_monitor: CostMonitorSettings = Field(default_factory=CostMonitorSettings)
    ocr: OCRSettings = Field(default_factory=OCRSettings)
    asr: ASRSettings = Field(default_factory=ASRSettings)
    webhook: WebhookSettings = Field(default_factory=WebhookSettings)
    automation_notify: AutomationNotifySettings = Field(default_factory=AutomationNotifySettings)
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


def _parse_env_override(env_key: str, env_value: str) -> Any:
    """按变量名解析环境变量覆盖值。"""
    if env_key == "HEARING_REMINDER_OFFSETS":
        parts = [item.strip() for item in env_value.split(",") if item.strip()]
        offsets: list[int] = []
        for item in parts:
            try:
                offsets.append(int(item))
            except ValueError as exc:
                raise ValueError(
                    f"Invalid HEARING_REMINDER_OFFSETS item: {item!r}. "
                    "Expected comma-separated integers, e.g. 7,3,1,0"
                ) from exc
        return offsets
    return env_value


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """
    应用环境变量覆盖
    
    优先级: 显式环境变量 > config.yaml > 默认值
    """
    mapping = {
        "LLM_PROVIDER": ["llm", "provider"],
        "LLM_MODEL": ["llm", "model"],
        "MODEL_PRIMARY": ["llm", "model_primary"],
        "MODEL_SECONDARY": ["llm", "model_secondary"],
        "FEISHU_BOT_APP_ID": ["feishu", "app_id"],
        "FEISHU_BOT_APP_SECRET": ["feishu", "app_secret"],
        "FEISHU_BOT_ORG_B_APP_ID": ["feishu", "org_b_app_id"],
        "FEISHU_BOT_ORG_B_APP_SECRET": ["feishu", "org_b_app_secret"],
        "FEISHU_BOT_VERIFICATION_TOKEN": ["feishu", "verification_token"],
        "FEISHU_BOT_ENCRYPT_KEY": ["feishu", "encrypt_key"],
        "MCP_SERVER_BASE": ["mcp", "base_url"],
        "POSTGRES_DSN": ["postgres", "dsn"],
        "REMINDER_SCHEDULER_ENABLED": ["reminder_scheduler_enabled"],
        "CRUD_DELETE_ENABLED": ["crud_delete_enabled"],
        "AUTOMATION_DRY_RUN": ["automation_dry_run"],
        "REMINDER_SCAN_ENABLED": ["reminder_scan_enabled"],
        "REMINDER_SCAN_INTERVAL_MINUTES": ["reminder_scan_interval_minutes"],
        "DAILY_DIGEST_ENABLED": ["daily_digest_enabled"],
        "DAILY_DIGEST_SCHEDULE": ["daily_digest_schedule"],
        "DAILY_DIGEST_TIMEZONE": ["daily_digest_timezone"],
        "POSTGRES_MIN_SIZE": ["postgres", "min_size"],
        "POSTGRES_MAX_SIZE": ["postgres", "max_size"],
        "POSTGRES_TIMEOUT": ["postgres", "timeout"],
        "USER_IDENTITY_AUTO_MATCH": ["user", "identity", "auto_match"],
        "USER_IDENTITY_MATCH_FIELD": ["user", "identity", "match_field"],
        "USER_IDENTITY_MIN_CONFIDENCE": ["user", "identity", "min_confidence"],
        "USER_IDENTITY_PROMPT_BIND_ON_FAIL": ["user", "identity", "prompt_bind_on_fail"],
        "USER_CACHE_TTL_HOURS": ["user", "cache", "ttl_hours"],
        "USER_CACHE_MAX_SIZE": ["user", "cache", "max_size"],
        "LLM_API_KEY": ["llm", "api_key"],
        "LLM_API_BASE": ["llm", "api_base"],
        "LLM_FALLBACK_ENABLED": ["llm", "fallback", "enabled"],
        "LLM_FALLBACK_PROVIDER": ["llm", "fallback", "provider"],
        "LLM_FALLBACK_MODEL": ["llm", "fallback", "model"],
        "LLM_FALLBACK_API_KEY": ["llm", "fallback", "api_key"],
        "LLM_FALLBACK_API_BASE": ["llm", "fallback", "api_base"],
        "MCP_BASE_URL": ["mcp", "base_url"],
        "TASK_LLM_ENABLED": ["task_llm", "enabled"],
        "TASK_LLM_MODEL": ["task_llm", "model"],
        "TASK_LLM_API_KEY": ["task_llm", "api_key"],
        "TASK_LLM_API_BASE": ["task_llm", "api_base"],
        "CARD_ENABLED": ["reply", "card_enabled"],
        "REACTION_ENABLED": ["reply", "reaction_enabled"],
        "QUERY_CARD_V2_ENABLED": ["reply", "query_card_v2_enabled"],
        "REPLY_PERSONALIZATION_ENABLED": ["reply", "reply_personalization_enabled"],
        "HEARING_REMINDER_ENABLED": ["hearing_reminder", "enabled"],
        "HEARING_REMINDER_CHAT_ID": ["hearing_reminder", "reminder_chat_id"],
        "HEARING_REMINDER_OFFSETS": ["hearing_reminder", "reminder_offsets"],
        "HEARING_REMINDER_SCAN_HOUR": ["hearing_reminder", "scan_hour"],
        "HEARING_REMINDER_SCAN_MINUTE": ["hearing_reminder", "scan_minute"],
        "CHUNK_ASSEMBLER_ENABLED": ["webhook", "chunk_assembler", "enabled"],
        "CHUNK_ASSEMBLER_WINDOW_SECONDS": ["webhook", "chunk_assembler", "window_seconds"],
        "CHUNK_ASSEMBLER_STALE_WINDOW_SECONDS": ["webhook", "chunk_assembler", "stale_window_seconds"],
        "AUTOMATION_NOTIFY_ENABLED": ["automation_notify", "enabled"],
        "AUTOMATION_NOTIFY_API_KEY": ["automation_notify", "api_key"],
        "MIDTERM_MEMORY_SQLITE_PATH": ["agent", "midterm_memory", "sqlite_path"],
        "MIDTERM_MEMORY_INJECT_TO_LLM": ["agent", "midterm_memory", "inject_to_llm"],
        "MIDTERM_MEMORY_LLM_RECENT_LIMIT": ["agent", "midterm_memory", "llm_recent_limit"],
        "MIDTERM_MEMORY_LLM_MAX_CHARS": ["agent", "midterm_memory", "llm_max_chars"],
        "STATE_STORE_BACKEND": ["state_store", "backend"],
        "STATE_STORE_REDIS_DSN": ["state_store", "redis", "dsn"],
        "STATE_STORE_REDIS_HOST": ["state_store", "redis", "host"],
        "STATE_STORE_REDIS_PORT": ["state_store", "redis", "port"],
        "STATE_STORE_REDIS_DB": ["state_store", "redis", "db"],
        "STATE_STORE_REDIS_PASSWORD": ["state_store", "redis", "password"],
        "STATE_STORE_REDIS_KEY_PREFIX": ["state_store", "redis", "key_prefix"],
        "STATE_STORE_REDIS_SOCKET_TIMEOUT_SECONDS": ["state_store", "redis", "socket_timeout_seconds"],
        "FILE_PIPELINE_ENABLED": ["file_pipeline", "enabled"],
        "FILE_PIPELINE_MAX_BYTES": ["file_pipeline", "max_bytes"],
        "FILE_PIPELINE_TIMEOUT_SECONDS": ["file_pipeline", "timeout_seconds"],
        "FILE_PIPELINE_METRICS_ENABLED": ["file_pipeline", "metrics_enabled"],
        "FILE_EXTRACTOR_ENABLED": ["file_extractor", "enabled"],
        "FILE_EXTRACTOR_PROVIDER": ["file_extractor", "provider"],
        "FILE_EXTRACTOR_API_KEY": ["file_extractor", "api_key"],
        "FILE_EXTRACTOR_API_BASE": ["file_extractor", "api_base"],
        "FILE_EXTRACTOR_MINERU_PATH": ["file_extractor", "mineru_path"],
        "FILE_EXTRACTOR_LLM_PATH": ["file_extractor", "llm_path"],
        "FILE_EXTRACTOR_AUTH_STYLE": ["file_extractor", "auth_style"],
        "FILE_EXTRACTOR_API_KEY_HEADER": ["file_extractor", "api_key_header"],
        "FILE_EXTRACTOR_API_KEY_PREFIX": ["file_extractor", "api_key_prefix"],
        "FILE_EXTRACTOR_FAIL_OPEN": ["file_extractor", "fail_open"],
        "FILE_CONTEXT_INJECTION_ENABLED": ["file_context", "injection_enabled"],
        "FILE_CONTEXT_MAX_CHARS": ["file_context", "max_chars"],
        "FILE_CONTEXT_MAX_TOKENS": ["file_context", "max_tokens"],
        "USAGE_LOG_ENABLED": ["usage_log", "enabled"],
        "USAGE_LOG_PATH": ["usage_log", "path"],
        "USAGE_LOG_FAIL_OPEN": ["usage_log", "fail_open"],
        "USAGE_MODEL_PRICING_PATH": ["usage_log", "model_pricing_path"],
        "USAGE_MODEL_PRICING_JSON": ["usage_log", "model_pricing_json"],
        "AB_ROUTING_ENABLED": ["ab_routing", "enabled"],
        "AB_ROUTING_RATIO": ["ab_routing", "ratio"],
        "AB_ROUTING_MODEL_A": ["ab_routing", "model_a"],
        "AB_ROUTING_MODEL_B": ["ab_routing", "model_b"],
        "COST_ALERT_HOURLY_THRESHOLD": ["cost_monitor", "alert_hourly_threshold"],
        "COST_ALERT_DAILY_THRESHOLD": ["cost_monitor", "alert_daily_threshold"],
        "COST_CIRCUIT_BREAKER_ENABLED": ["cost_monitor", "circuit_breaker_enabled"],
        "OCR_ENABLED": ["ocr", "enabled"],
        "OCR_PROVIDER": ["ocr", "provider"],
        "OCR_API_KEY": ["ocr", "api_key"],
        "OCR_API_BASE": ["ocr", "api_base"],
        "OCR_API_ENDPOINT": ["ocr", "api_base"],
        "OCR_MINERU_PATH": ["ocr", "mineru_path"],
        "OCR_LLM_PATH": ["ocr", "llm_path"],
        "OCR_AUTH_STYLE": ["ocr", "auth_style"],
        "OCR_API_KEY_HEADER": ["ocr", "api_key_header"],
        "OCR_API_KEY_PREFIX": ["ocr", "api_key_prefix"],
        "ASR_ENABLED": ["asr", "enabled"],
        "ASR_PROVIDER": ["asr", "provider"],
        "ASR_API_KEY": ["asr", "api_key"],
        "ASR_API_BASE": ["asr", "api_base"],
        "ASR_API_ENDPOINT": ["asr", "api_base"],
        "ASR_MINERU_PATH": ["asr", "mineru_path"],
        "ASR_LLM_PATH": ["asr", "llm_path"],
        "ASR_AUTH_STYLE": ["asr", "auth_style"],
        "ASR_API_KEY_HEADER": ["asr", "api_key_header"],
        "ASR_API_KEY_PREFIX": ["asr", "api_key_prefix"],
    }
    for env_key, path in mapping.items():
        env_value = os.getenv(env_key)
        if env_value is not None and env_value != "":
            _set_nested(data, path, _parse_env_override(env_key, env_value))
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
