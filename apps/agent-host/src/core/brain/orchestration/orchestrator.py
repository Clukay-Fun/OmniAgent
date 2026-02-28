"""
描述: Agent 核心编排层
主要功能:
    - 整合意图识别 (IntentParser) 与 技能路由 (SkillRouter)
    - 统一处理用户消息生命周期
    - 管理长期记忆与上下文
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
import logging
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, ContextManager as TypingContextManager, Mapping, cast

import yaml

from src.utils.observability.logger import set_request_context, clear_request_context, generate_request_id
from src.utils.observability.metrics import (
    record_chitchat_guard,
    record_file_pipeline,
    record_intent_parse,
    record_request,
    record_usage_log_write,
    set_token_budget,
    set_active_sessions,
)
from src.core.foundation.progress.batch_progress import BatchProgressEmitter, BatchProgressEvent, BatchProgressPhase
from src.core.foundation.progress.processing_status import ProcessingStatus, ProcessingStatusEmitter, ProcessingStatusEvent

from src.core.runtime.state.session import SessionManager
from src.core.understanding.intent import IntentParser, IntentResult, SkillMatch, load_skills_config
from src.core.understanding.router import (
    SkillRouter,
    SkillContext,
    SkillResult,
    ContextManager,
    ModelRouter,
    RoutingDecision,
)
from src.core.brain.l0 import L0RuleEngine
from src.core.runtime.planner import PlannerEngine, PlannerOutput
from src.core.runtime.state import ConversationStateManager, create_state_store
from src.core.runtime.state.models import OperationEntry, OperationExecutionStatus, PendingActionState
from src.core.runtime.state.midterm_memory_store import RuleSummaryExtractor, SQLiteMidtermMemoryStore
from src.core.capabilities.skills import (
    QuerySkill,
    SummarySkill,
    ReminderSkill,
    ChitchatSkill,
    CreateSkill,
    UpdateSkill,
    DeleteSkill,
    KnowledgeQASkill,
)
from src.core.capabilities.skills.actions.data_writer import DataWriter
from src.core.expression.soul import SoulManager
from src.core.runtime.memory import MemoryManager
from src.core.expression.response.models import RenderedResponse
from src.core.expression.response.renderer import ResponseRenderer
from src.config import Settings
from src.infra.llm.client import LLMClient
from src.infra.mcp.client import MCPClient
from src.utils.parsing.time_parser import parse_time_range
from src.infra.vector import load_vector_config, EmbeddingClient, ChromaStore, VectorMemoryManager
from src.skills_market import load_market_skills
from src.core.foundation.telemetry.usage_logger import UsageLogger, UsageRecord, now_iso
from src.core.foundation.telemetry.usage_cost import compute_usage_cost, load_model_pricing
from src.core.foundation.telemetry.cost_monitor import CostMonitorConfig, configure_cost_monitor
from src.core.foundation.common.errors import (
    PendingActionExpiredError,
    PendingActionNotFoundError,
    get_user_message_by_code,
    get_user_message as get_core_user_message,
)

logger = logging.getLogger(__name__)


def _resolve_assistant_name(skills_config: dict[str, Any] | None) -> str:
    configured_name = ""
    if isinstance(skills_config, dict):
        raw = skills_config.get("assistant_name")
        if isinstance(raw, str):
            configured_name = raw.strip()
    return configured_name or "小敬"


def _resolve_chitchat_allow_llm(skills_config: dict[str, Any] | None) -> bool:
    if not isinstance(skills_config, dict):
        return False
    chitchat = skills_config.get("chitchat")
    if not isinstance(chitchat, dict):
        chitchat = skills_config.get("skills", {}).get("chitchat", {})
    if not isinstance(chitchat, dict):
        return False
    return bool(chitchat.get("allow_llm", False))


def _load_casual_responses() -> list[str]:
    casual_path = Path("config/messages/zh-CN/casual.yaml")
    if not casual_path.exists():
        casual_path = Path("config/responses/casual.yaml")
    if not casual_path.exists():
        return []
    try:
        payload = yaml.safe_load(casual_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    if isinstance(payload, list):
        return [str(item).strip() for item in payload if str(item).strip()]
    if isinstance(payload, dict):
        responses = payload.get("responses")
        if isinstance(responses, list):
            return [str(item).strip() for item in responses if str(item).strip()]
    return []


def _build_outbound_from_skill_result(
    result: SkillResult,
    assistant_name: str = "小敬",
    query_card_v2_enabled: bool = False,
) -> dict[str, Any]:
    """将 SkillResult 转成统一 outbound 结构。"""
    renderer = ResponseRenderer(assistant_name=assistant_name, query_card_v2_enabled=query_card_v2_enabled)
    rendered = renderer.render(result)
    return rendered.to_dict()


def _ensure_minimal_outbound(
    reply: dict[str, Any],
    assistant_name: str,
) -> dict[str, Any]:
    text_value = str(reply.get("text") or "请稍后重试。")

    outbound = reply.get("outbound")
    if not isinstance(outbound, dict):
        outbound = {}

    raw_fallback = outbound.get("text_fallback")
    text_fallback = raw_fallback.strip() if isinstance(raw_fallback, str) else ""
    if not text_fallback:
        text_fallback = text_value

    blocks = outbound.get("blocks")
    paragraph_exists = isinstance(blocks, list) and any(
        isinstance(block, dict) and block.get("type") == "paragraph" for block in blocks
    )
    if not paragraph_exists:
        blocks = [{"type": "paragraph", "content": {"text": text_fallback}}]

    meta = outbound.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    meta["assistant_name"] = str(meta.get("assistant_name") or assistant_name)
    meta["skill_name"] = str(meta.get("skill_name") or "fallback")

    normalized_outbound: dict[str, Any] = {
        "text_fallback": text_fallback,
        "blocks": blocks,
        "meta": meta,
    }
    card_template = outbound.get("card_template")
    if isinstance(card_template, dict):
        normalized_outbound["card_template"] = card_template

    reply["outbound"] = normalized_outbound
    return reply


# region 核心编排器
class AgentOrchestrator:
    """
    Agent 编排器

    核心流程:
        1. 接收用户消息
        2. 调用 IntentParser 解析意图
        3. 通过 SkillRouter 路由到对应技能
        4. 返回格式化结果
    """
    
    _DATE_PATTERN = re.compile(
        r"(?:\d{4}年)?\d{1,2}月\d{1,2}[日号]?|\d{4}-\d{1,2}-\d{1,2}"
    )

    def __init__(
        self,
        settings: Settings,
        session_manager: SessionManager,
        mcp_client: MCPClient,
        llm_client: LLMClient,
        data_writer: DataWriter,
        skills_config_path: str = "config/skills.yaml",
    ) -> None:
        """
        初始化编排器

        参数:
            settings: 应用配置对象
            session_manager: 会话管理器
            mcp_client: MCP 客户端实例
            llm_client: LLM 客户端实例
            skills_config_path: 技能配置文件路径
        """
        self._settings = settings
        self._sessions = session_manager
        self._mcp = mcp_client
        self._llm = llm_client
        if data_writer is None:
            raise ValueError("AgentOrchestrator requires an injected data_writer")
        self._data_writer: DataWriter = data_writer
        self._context_trim_tokens = max(256, min(int(settings.session.max_context_tokens), 3800))
        set_token_budget("session_context", self._context_trim_tokens)

        # ============================================
        # region 任务模型初始化（意图识别/工具调用专用）
        # ============================================
        if settings.task_llm.enabled and settings.task_llm.api_key:
            from src.config import LLMSettings as _LLMSettings
            _task_llm_cfg = _LLMSettings(
                provider=settings.task_llm.provider,
                model=settings.task_llm.model,
                api_key=settings.task_llm.api_key,
                api_base=settings.task_llm.api_base,
                temperature=settings.task_llm.temperature,
                max_tokens=settings.task_llm.max_tokens,
                timeout=settings.task_llm.timeout,
            )
            self._task_llm = LLMClient(_task_llm_cfg)
            logger.info(
                "任务模型已启用: %s",
                settings.task_llm.model,
                extra={"event_code": "orchestrator.task_llm.enabled"},
            )
        else:
            self._task_llm = self._llm
            logger.info(
                "任务模型未启用，统一使用默认模型",
                extra={"event_code": "orchestrator.task_llm.disabled"},
            )
        # endregion
        # ============================================
        
        self._skills_config_path = skills_config_path

        # 初始化 Postgres (Migrating to Stateless)
        self._db = None

        # 初始化向量记忆
        self._vector_config = load_vector_config()
        self._vector_top_k = int(
            (self._vector_config or {}).get("retrieval", {}).get("top_k", 5)
        )
        self._vector_fallback = (
            (self._vector_config or {}).get("embedding", {}).get("fallback", "keyword")
        )
        self._vector_memory = self._init_vector_memory(self._vector_config)

        # 初始化 Soul 与 Memory
        self._soul_manager = SoulManager()
        self._memory_manager = MemoryManager(vector_memory=self._vector_memory)
        self._memory_manager.cleanup_logs()
        self._midterm_extractor = RuleSummaryExtractor()
        midterm_cfg = settings.agent.midterm_memory
        self._midterm_memory_inject_to_llm = bool(midterm_cfg.inject_to_llm)
        self._midterm_memory_llm_recent_limit = max(1, int(midterm_cfg.llm_recent_limit))
        self._midterm_memory_llm_max_chars = max(80, int(midterm_cfg.llm_max_chars))
        self._file_context_enabled = bool(getattr(settings.file_context, "injection_enabled", False))
        self._file_context_max_chars = max(200, int(getattr(settings.file_context, "max_chars", 2000)))
        self._file_context_max_tokens = max(50, int(getattr(settings.file_context, "max_tokens", 500)))
        set_token_budget("file_context", self._file_context_max_tokens)
        self._midterm_memory_store: SQLiteMidtermMemoryStore | None = None
        try:
            self._midterm_memory_store = SQLiteMidtermMemoryStore(db_path=midterm_cfg.sqlite_path)
        except Exception as exc:
            logger.warning(
                "初始化中期记忆存储失败: %s",
                exc,
                extra={"event_code": "orchestrator.midterm_memory.init_failed"},
            )

        # 加载技能配置（含技能市场）
        self._skills_config = self._load_skills_config(skills_config_path)
        self._table_identity_fields = self._load_table_identity_fields(self._skills_config)
        self._assistant_name = _resolve_assistant_name(self._skills_config)
        self._chitchat_allow_llm = _resolve_chitchat_allow_llm(self._skills_config)
        self._casual_responses = _load_casual_responses()
        self._response_renderer = ResponseRenderer(
            assistant_name=self._assistant_name,
            query_card_v2_enabled=bool(getattr(settings.reply, "query_card_v2_enabled", False)),
        )
        self._reply_personalization_enabled = bool(getattr(settings.reply, "reply_personalization_enabled", False))
        self._usage_logger = UsageLogger(
            enabled=bool(getattr(settings.usage_log, "enabled", False)),
            path_template=str(getattr(settings.usage_log, "path", "workspace/usage/usage_log-{date}.jsonl")),
            fail_open=bool(getattr(settings.usage_log, "fail_open", True)),
            on_record_written=self._on_usage_record_written,
        )
        self._usage_model_pricing = load_model_pricing(
            model_pricing_path=str(getattr(settings.usage_log, "model_pricing_path", "") or ""),
            model_pricing_json=str(getattr(settings.usage_log, "model_pricing_json", "") or ""),
        )
        self._cost_monitor = configure_cost_monitor(
            CostMonitorConfig(
                hourly_threshold=float(getattr(settings.cost_monitor, "alert_hourly_threshold", 5.0)),
                daily_threshold=float(getattr(settings.cost_monitor, "alert_daily_threshold", 50.0)),
                circuit_breaker_enabled=bool(getattr(settings.cost_monitor, "circuit_breaker_enabled", False)),
            )
        )
        primary_model = str(getattr(settings.llm, "model_primary", "") or "").strip() or str(getattr(settings.llm, "model", "")).strip()
        secondary_model = str(getattr(settings.llm, "model_secondary", "") or "").strip()
        model_a = str(getattr(settings.ab_routing, "model_a", "") or "").strip()
        model_b = str(getattr(settings.ab_routing, "model_b", "") or "").strip()
        self._model_router = ModelRouter(
            enabled=bool(getattr(settings.ab_routing, "enabled", False)),
            ratio=float(getattr(settings.ab_routing, "ratio", 0.0)),
            primary_model=primary_model,
            model_a=model_a or primary_model,
            model_b=model_b or secondary_model,
        )

        # LLM 超时配置
        self._llm_timeout = float(self._skills_config.get("intent", {}).get("llm_timeout", 10))
        
        # 初始化意图解析器
        self._intent_parser = IntentParser(
            skills_config=self._skills_config,
            llm_client=self._task_llm,
        )
        
        # 初始化技能路由器
        max_hops = self._skills_config.get("chain", {}).get(
            "max_hops",
            self._skills_config.get("routing", {}).get("max_hops", 2),
        )
        self._router = SkillRouter(
            skills_config=self._skills_config,
            max_hops=max_hops,
            llm_client=getattr(self, "_task_llm", self._llm),
        )
        
        # 初始化上下文管理器
        self._context_manager = ContextManager()

        # 初始化会话状态管理（内存 + TTL，可替换为 Redis）
        state_store = create_state_store(settings)
        self._state_manager = ConversationStateManager(
            store=state_store,
            default_ttl_seconds=max(int(settings.session.ttl_minutes * 60), 60),
            pending_delete_ttl_seconds=300,
            pagination_ttl_seconds=600,
            last_result_ttl_seconds=600,
            active_record_ttl_seconds=max(int(settings.session.ttl_minutes * 60), 60),
            pending_action_ttl_seconds=300,
        )

        # 初始化 L0 规则层
        l0_rules = self._load_l0_rules(skills_config_path)
        self._l0_engine = L0RuleEngine(
            state_manager=self._state_manager,
            l0_rules=l0_rules,
            skills_config=self._skills_config,
        )

        # 初始化 L1 Planner
        planner_cfg = self._skills_config.get("planner", {}) if isinstance(self._skills_config, dict) else {}
        planner_enabled = bool(planner_cfg.get("enabled", True))
        self._planner_confidence_threshold = float(planner_cfg.get("confidence_threshold", 0.65))
        planner_llm_timeout_seconds = float(planner_cfg.get("llm_timeout_seconds", 4.0))
        planner_fast_path_confidence = float(planner_cfg.get("fast_path_confidence", 0.8))
        scenarios_dir = self._resolve_planner_scenarios_dir(
            skills_config_path,
            str(planner_cfg.get("scenarios_dir", "config/scenarios")),
        )
        self._planner = PlannerEngine(
            llm_client=self._task_llm,
            scenarios_dir=scenarios_dir,
            enabled=planner_enabled,
            llm_timeout_seconds=planner_llm_timeout_seconds,
            fast_path_confidence=planner_fast_path_confidence,
        )
        
        # 注册技能
        self._register_skills()

    def _register_skills(self) -> None:
        """注册并初始化所有技能"""
        skills = [
            QuerySkill(
                mcp_client=self._mcp,
                settings=self._settings,
                llm_client=self._task_llm,
                skills_config=self._skills_config,
                data_writer=self._data_writer,
            ),
            CreateSkill(
                mcp_client=self._mcp,
                llm_client=self._task_llm,
                settings=self._settings,
                skills_config=self._skills_config,
                data_writer=self._data_writer,
            ),
            UpdateSkill(
                mcp_client=self._mcp,
                llm_client=self._task_llm,
                settings=self._settings,
                skills_config=self._skills_config,
                data_writer=self._data_writer,
            ),
            DeleteSkill(
                mcp_client=self._mcp,
                llm_client=self._task_llm,
                settings=self._settings,
                skills_config=self._skills_config,
                data_writer=self._data_writer,
            ),
            SummarySkill(llm_client=self._llm, skills_config=self._skills_config),
            ReminderSkill(mcp_client=self._mcp, skills_config=self._skills_config),
            KnowledgeQASkill(llm_client=self._llm, mcp_client=self._mcp, skills_config=self._skills_config),
            ChitchatSkill(skills_config=self._skills_config, llm_client=self._llm),
        ]
        if getattr(self, "_market_skills", None):
            skills.extend(self._market_skills)
        self._router.register_all(skills)
        logger.info(
            "技能注册完成",
            extra={
                "event_code": "orchestrator.skills.registered",
                "skills": self._router.list_skills(),
            },
        )

    def _init_vector_memory(self, config: dict[str, Any] | None) -> VectorMemoryManager | None:
        if not config:
            logger.info(
                "未找到向量配置，已禁用向量记忆",
                extra={"event_code": "orchestrator.vector.disabled_no_config"},
            )
            return None

        store_cfg = config.get("vector_store", {})
        store_type = store_cfg.get("type", "chroma")
        if store_type != "chroma":
            logger.warning(
                "不支持的向量存储类型: %s",
                store_type,
                extra={"event_code": "orchestrator.vector.unsupported_store"},
            )
            return None

        embedding_cfg = config.get("embedding", {})
        chroma_cfg = config.get("chroma", {})
        if not embedding_cfg or not chroma_cfg:
            logger.warning(
                "向量配置不完整，已禁用向量记忆",
                extra={"event_code": "orchestrator.vector.invalid_config"},
            )
            return None
        if not embedding_cfg.get("api_key") or not embedding_cfg.get("api_base"):
            logger.warning(
                "缺少 Embedding API 配置，已禁用向量记忆",
                extra={"event_code": "orchestrator.vector.embedding_config_missing"},
            )
            return None

        store = ChromaStore(
            persist_path=chroma_cfg.get("persist_path", "./workspace/chroma"),
            collection_prefix=chroma_cfg.get("collection_prefix", "memory_vectors_"),
        )
        if not store.is_available:
            logger.warning(
                "Chroma 不可用，已禁用向量记忆",
                extra={"event_code": "orchestrator.vector.chroma_unavailable"},
            )
            return None

        embedder = EmbeddingClient(embedding_cfg)
        return VectorMemoryManager(
            store=store,
            embedder=embedder,
            top_k=self._vector_top_k,
            fallback=self._vector_fallback,
        )

    def _load_skills_config(self, config_path: str) -> dict[str, Any]:
        base_config = load_skills_config(config_path)
        self._market_skills = []
        market_defs: dict[str, Any] = {}

        market_cfg = base_config.get("skills_market", {})
        if market_cfg.get("enabled", False):
            try:
                market_skills, market_defs = load_market_skills(
                    market_cfg,
                    config_path=config_path,
                    dependencies={
                        "mcp_client": self._mcp,
                        "llm_client": self._llm,
                        "settings": self._settings,
                        "skills_config": base_config,
                    },
                )
                builtin_names = {"QuerySkill", "SummarySkill", "ReminderSkill", "ChitchatSkill"}
                self._market_skills = [
                    skill for skill in market_skills if skill.name not in builtin_names
                ]
                market_defs = {
                    name: cfg for name, cfg in market_defs.items() if name not in builtin_names
                }
            except Exception as exc:
                logger.warning(
                    "加载技能市场失败: %s",
                    exc,
                    extra={"event_code": "orchestrator.market.load_failed"},
                )

        return self._merge_skills_config(base_config, market_defs)

    def _merge_skills_config(
        self,
        base_config: dict[str, Any],
        market_defs: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(base_config)
        skills_registry: dict[str, Any] = {}

        if isinstance(base_config.get("skills"), dict):
            skills_registry.update(base_config.get("skills", {}))
        else:
            builtin_map = {
                "query": "QuerySkill",
                "create": "CreateSkill",
                "update": "UpdateSkill",
                "delete": "DeleteSkill",
                "summary": "SummarySkill",
                "reminder": "ReminderSkill",
                "chitchat": "ChitchatSkill",
            }
            for key, default_name in builtin_map.items():
                cfg = base_config.get(key)
                if not isinstance(cfg, dict):
                    continue
                normalized = dict(cfg)
                normalized.setdefault("name", default_name)
                if key == "chitchat" and "keywords" not in normalized:
                    normalized["keywords"] = normalized.get("whitelist", [])
                skills_registry[key] = normalized

        for key, cfg in (market_defs or {}).items():
            if key in skills_registry:
                logger.warning(
                    "技能市场键已存在，跳过覆盖: %s",
                    key,
                    extra={"event_code": "orchestrator.market.duplicate_key"},
                )
                continue
            skills_registry[key] = cfg

        if skills_registry:
            merged["skills"] = skills_registry
        return merged

    def _load_table_identity_fields(self, skills_config: dict[str, Any] | None) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        if not isinstance(skills_config, dict):
            return mapping
        raw = skills_config.get("table_identity_fields")
        table_cfg = raw if isinstance(raw, dict) else {}
        for table_name, cfg in table_cfg.items():
            name = str(table_name or "").strip()
            if not name or not isinstance(cfg, dict):
                continue
            fields_raw = cfg.get("fields")
            fields = [str(item).strip() for item in fields_raw if str(item).strip()] if isinstance(fields_raw, list) else []
            if fields:
                mapping[name] = fields
        return mapping

    def _load_l0_rules(self, skills_config_path: str) -> dict[str, Any]:
        """加载 L0 规则配置。"""
        config_path = Path(skills_config_path)
        if not config_path.is_absolute():
            config_path = Path.cwd() / config_path
        l0_path = config_path.parent / "engine" / "l0_rules.yaml"
        if not l0_path.exists():
            l0_path = config_path.parent / "l0_rules.yaml"
        if not l0_path.exists():
            logger.info(
                "未找到 L0 规则文件: %s",
                l0_path,
                extra={"event_code": "orchestrator.l0_rules.not_found"},
            )
            return {}
        try:
            with l0_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning(
                "加载 L0 规则失败: %s",
                exc,
                extra={"event_code": "orchestrator.l0_rules.load_failed"},
            )
            return {}

    def _resolve_planner_scenarios_dir(self, skills_config_path: str, scenarios_dir: str) -> str:
        """解析 planner 场景目录为绝对路径。"""
        path = Path(scenarios_dir)
        if path.is_absolute():
            return str(path)

        config_path = Path(skills_config_path)
        if not config_path.is_absolute():
            config_path = Path.cwd() / config_path
        base_dir = config_path.parent.parent if config_path.parent.name == "config" else config_path.parent
        return str((base_dir / path).resolve())

    def _build_intent_from_planner(self, plan: PlannerOutput) -> IntentResult | None:
        """将 Planner 输出映射为路由层意图结果。"""
        intent_to_skill = {
            "query_all": "QuerySkill",
            "query_view": "QuerySkill",
            "query_my_cases": "QuerySkill",
            "query_person": "QuerySkill",
            "query_exact": "QuerySkill",
            "query_date_range": "QuerySkill",
            "query_advanced": "QuerySkill",
            "create_record": "CreateSkill",
            "update_record": "UpdateSkill",
            "close_record": "UpdateSkill",
            "delete_record": "DeleteSkill",
            "create_reminder": "ReminderSkill",
            "list_reminders": "ReminderSkill",
            "cancel_reminder": "ReminderSkill",
            "business_qa": "KnowledgeQASkill",
            "out_of_scope": "ChitchatSkill",
        }
        skill = intent_to_skill.get(plan.intent)
        if not skill:
            return None
        return IntentResult(
            skills=[
                SkillMatch(
                    name=skill,
                    score=max(0.0, min(1.0, float(plan.confidence))),
                    reason=f"planner:{plan.intent}",
                )
            ],
            is_chain=False,
            requires_llm_confirm=False,
            method="planner",
        )

    async def handle_message(
        self,
        user_id: str,
        text: str,
        chat_id: str | None = None,
        chat_type: str | None = None,
        user_profile: Any = None,
        file_markdown: str = "",
        file_provider: str = "none",
        status_emitter: ProcessingStatusEmitter | None = None,
        force_chitchat: bool = False,
    ) -> dict[str, Any]:
        """
        处理用户消息

        参数:
            user_id: 用户 ID
            text: 用户输入文本
            chat_id: 群组 ID (可选)
            chat_type: 会话类型 (可选)
            user_profile: 用户档案 (可选)

        返回:
            回复内容（type, text, card 等）
        """

        
        # 设置请求上下文（用于结构化日志）
        request_id = generate_request_id()
        set_request_context(request_id=request_id, user_id=user_id)
        
        start_time = time.perf_counter()
        status = "success"
        usage_skill = "unknown"
        usage_source = "text"
        usage_business_metadata: dict[str, Any] = {}
        close_semantic = ""
        close_profile = ""
        route_decision = RoutingDecision(
            model_selected=str(getattr(getattr(self, "_llm", None), "model_name", "")),
            route_label="primary_default",
            complexity="medium",
            reason="default",
            in_ab_bucket=False,
            metadata={
                "route_label": "primary_default",
                "complexity": "medium",
                "route_reason": "default",
            },
        )
        reply: dict[str, Any] = {
            "type": "text",
            "text": "请稍后重试。",
        }
        
        try:
            cost_monitor = getattr(self, "_cost_monitor", None)
            if cost_monitor is not None:
                allowed, guidance = cost_monitor.check_call_allowed("llm")
                if not allowed:
                    status = "success"
                    return {
                        "type": "text",
                        "text": guidance or "当前服务预算已达到阈值，请稍后再试。",
                    }

            # 清理过期会话和上下文
            self._sessions.cleanup_expired()
            self._context_manager.cleanup_expired()
            self._state_manager.cleanup_expired()
            
            # 更新活跃会话指标
            active_count = max(self._context_manager.active_count(), self._state_manager.active_count())
            set_active_sessions(active_count)
            
            # 记录用户消息
            self._sessions.add_message(user_id, "user", text)
            self._trim_session_context(user_id)

            model_router = getattr(self, "_model_router", None)
            if model_router is not None and hasattr(model_router, "decide"):
                route_decision = model_router.decide(user_id=user_id, query=text)
            route_context = {
                "model_selected": route_decision.model_selected,
                "route_label": route_decision.route_label,
                "complexity": route_decision.complexity,
                "route_reason": route_decision.reason,
            }
            task_route_context = getattr(getattr(self, "_task_llm", None), "route_context", None)
            llm_route_context = getattr(getattr(self, "_llm", None), "route_context", None)
            task_ctx = cast(
                TypingContextManager[Any],
                task_route_context(**route_context) if callable(task_route_context) else nullcontext(),
            )
            llm_ctx = cast(
                TypingContextManager[Any],
                llm_route_context(**route_context) if callable(llm_route_context) else nullcontext(),
            )
            with task_ctx, llm_ctx:
                # Step 0: L0 规则硬约束
                l0_decision = self._l0_engine.evaluate(user_id, text)
                if l0_decision.handled:
                    reply = l0_decision.reply or {
                        "type": "text",
                        "text": "请换一种说法再试试。",
                    }
                    status = "success"
                else:
                    # Step 1: L0 强制技能 或 L1 Planner/IntentParser
                    llm_context: dict[str, str] | None = None
                    planner_output: PlannerOutput | None = None
                    planner_applied = False
                    should_execute = True
                    intent: IntentResult | None = None

                    if force_chitchat:
                        usage_skill = "ChitchatSkill"
                        intent = IntentResult(
                            skills=[
                                SkillMatch(
                                    name="ChitchatSkill",
                                    score=1.0,
                                    reason="forced by caller",
                                )
                            ],
                            is_chain=False,
                            requires_llm_confirm=False,
                            method="channel_policy",
                        )
                    elif l0_decision.force_skill:
                        usage_skill = l0_decision.force_skill
                        intent = IntentResult(
                            skills=[
                                SkillMatch(
                                    name=l0_decision.force_skill,
                                    score=1.0,
                                    reason="L0 rule matched",
                                )
                            ],
                            is_chain=False,
                            requires_llm_confirm=False,
                            method="l0",
                        )
                        logger.info(
                            "L0 规则强制指定意图",
                            extra={
                                "event_code": "orchestrator.intent.forced_by_l0",
                                "query": text,
                                "intent": intent.to_dict(),
                            },
                        )
                    elif l0_decision.intent_hint == "chitchat":
                        usage_skill = "ChitchatSkill"
                        if not self._chitchat_allow_llm:
                            record_chitchat_guard("blocked")
                            reply = {
                                "type": "text",
                                "text": self._pick_casual_response(),
                            }
                            status = "success"
                            should_execute = False
                        else:
                            intent = IntentResult(
                                skills=[
                                    SkillMatch(
                                        name="ChitchatSkill",
                                        score=1.0,
                                        reason="L0 chitchat hint",
                                    )
                                ],
                                is_chain=False,
                                requires_llm_confirm=False,
                                method="l0_hint",
                            )
                    else:
                        llm_context = await self._build_llm_context(
                            user_id,
                            query=text,
                            file_markdown=file_markdown,
                            file_provider=file_provider,
                        )
                        await self._emit_processing_status(
                            status_emitter,
                            ProcessingStatus.THINKING,
                            user_id,
                            chat_id,
                            chat_type,
                        )

                        planner_start = time.perf_counter()
                        planner_output = await self._planner.plan(text, user_profile=user_profile)
                        planner_duration = time.perf_counter() - planner_start

                        if planner_output and planner_output.intent == "clarify_needed":
                            reply = {
                                "type": "text",
                                "text": planner_output.clarify_question or "请再具体描述一下您的需求。",
                            }
                            status = "success"
                            should_execute = False
                        else:
                            if planner_output and planner_output.confidence >= self._planner_confidence_threshold:
                                intent = self._build_intent_from_planner(planner_output)
                                if intent:
                                    planner_applied = True
                                    record_intent_parse("planner", planner_duration)
                                    logger.info(
                                        "Planner 意图解析完成",
                                        extra={
                                            "event_code": "orchestrator.intent.parsed_planner",
                                            "query": text,
                                            "intent": intent.to_dict(),
                                            "planner": planner_output.to_context(),
                                        },
                                    )

                            if intent is None:
                                intent_start = time.perf_counter()
                                intent = await self._intent_parser.parse(text, llm_context=llm_context)
                                record_intent_parse(intent.method, time.perf_counter() - intent_start)
                                logger.info(
                                    "意图解析完成",
                                    extra={
                                        "event_code": "orchestrator.intent.parsed",
                                        "query": text,
                                        "intent": intent.to_dict(),
                                    },
                                )

                    if should_execute:
                        # Step 2: 构建执行上下文
                        prev_context = self._context_manager.get(user_id)
                        state_last_result = self._state_manager.get_last_result_payload(user_id)
                        state_last_skill = self._state_manager.get_last_skill(user_id)
                        active_table = self._state_manager.get_active_table(user_id)
                        active_record = self._state_manager.get_active_record(user_id)
                        pending_action = self._state_manager.get_pending_action(user_id)
                        if l0_decision.force_skill:
                            extra = dict(l0_decision.force_extra or {})
                        else:
                            extra = await self._build_extra(text, user_id, llm_context)
                            if planner_applied and planner_output:
                                extra["planner_plan"] = planner_output.to_context()

                        if active_table.get("table_id"):
                            extra["active_table_id"] = active_table.get("table_id")
                        if active_table.get("table_name"):
                            extra["active_table_name"] = active_table.get("table_name")
                        if active_record and active_record.record:
                            extra["active_record"] = active_record.record
                        if pending_action:
                            extra["pending_action"] = {
                                "action": pending_action.action,
                                "payload": pending_action.payload,
                            }

                        extra["chat_id"] = chat_id
                        extra["chat_type"] = chat_type
                        extra["user_profile"] = user_profile  # 添加用户档案

                        # 注入当前目标表的身份归属字段（供 QuerySkill "我的XXX" 使用）
                        _resolved_table_name = str(
                            extra.get("table_name")
                            or extra.get("active_table_name")
                            or (
                                planner_output.params.get("table_name")
                                if planner_output and isinstance(getattr(planner_output, "params", None), dict)
                                else ""
                            )
                            or ""
                        ).strip()
                        if _resolved_table_name:
                            _tif = self._table_identity_fields.get(_resolved_table_name, [])
                            if _tif:
                                extra["table_identity_fields"] = list(_tif)

                        extra["complexity"] = route_decision.complexity
                        extra["route_label"] = route_decision.route_label
                        extra["model_selected"] = route_decision.model_selected
                        usage_source = str(extra.get("usage_source") or "text")

                        force_last_result = l0_decision.force_last_result if l0_decision.force_skill else None
                        context = SkillContext(
                            query=text,
                            user_id=user_id,
                            last_result=force_last_result if force_last_result is not None else (state_last_result or (prev_context.last_result if prev_context else None)),
                            last_skill=state_last_skill or (prev_context.last_skill if prev_context else None),
                            extra=extra,
                        )

                        # Step 3: 路由并执行技能
                        if intent is None:
                            result = SkillResult(
                                success=False,
                                skill_name="fallback",
                                message="未能识别意图",
                                reply_text="抱歉，我没理解您的意思。您可以试试：查所有案件、我的案件、查案号 XXX。",
                            )
                        else:
                            assert intent is not None
                            await self._emit_processing_status(
                                status_emitter,
                                ProcessingStatus.SEARCHING,
                                user_id,
                                chat_id,
                                chat_type,
                            )
                            result = await self._router.route(intent, context)
                        usage_skill = result.skill_name
                        close_semantic = str((result.data or {}).get("close_semantic") or "")
                        close_profile = str((result.data or {}).get("close_profile") or "")
                        usage_business_metadata = self._extract_usage_business_metadata(result.data or {})

                        # Step 4: 更新上下文（保存结果供后续链式调用）
                        if result.success and result.data:
                            self._context_manager.update_result(user_id, result.skill_name, result.data)
                            self._context_manager.set(user_id, context.with_result(result.skill_name, result.data))

                        # Step 4.1: 同步会话状态机（L0 使用）
                        self._sync_state_after_result(user_id, text, result)

                        if not result.success:
                            status = "failure"

                        # Step 5: 构建回复
                        reply = result.to_reply()
                        rendered = self._response_renderer.render(result)
                        rendered = self._maybe_apply_reply_personalization(user_id=user_id, user_text=text, rendered=rendered)
                        reply["outbound"] = rendered.to_dict()

                        # Step 6: 写入记忆（日志 + 关键偏好）
                        self._record_memory(
                            user_id,
                            text,
                            reply.get("text", ""),
                            result.skill_name,
                            result.data or {},
                            context.extra,
                        )
            
        except asyncio.TimeoutError:
            status = "timeout"
            logger.warning(
                "消息处理超时",
                extra={"event_code": "orchestrator.request.timeout"},
            )
            reply = {
                "type": "text",
                "text": self._settings.reply.templates.error.format(message="请求超时，请稍后重试"),
            }
        except ConnectionError as e:
            status = "connection_error"
            logger.error(
                "连接异常: %s",
                e,
                extra={"event_code": "orchestrator.request.connection_error"},
            )
            reply = {
                "type": "text",
                "text": self._settings.reply.templates.error.format(message="服务连接异常，请稍后重试"),
            }
        except Exception as e:
            status = "error"
            logger.error(
                "消息处理异常: %s",
                e,
                extra={"event_code": "orchestrator.request.error"},
                exc_info=True,
            )
            reply = {
                "type": "text",
                "text": self._settings.reply.templates.error.format(message="处理出错"),
            }
        finally:
            await self._emit_processing_status(
                status_emitter,
                ProcessingStatus.DONE,
                user_id,
                chat_id,
                chat_type,
                metadata={"status": status},
            )
            # 记录请求指标
            duration = time.perf_counter() - start_time
            record_request("handle_message", status)
            
            logger.info(
                "请求处理完成",
                extra={
                    "event_code": "orchestrator.request.completed",
                    "status": status,
                    "duration_ms": round(duration * 1000, 2),
                    "close_semantic": close_semantic,
                    "close_profile": close_profile,
                },
            )
            
            # 清除请求上下文
            clear_request_context()
        
        # 记录助手回复
        reply = _ensure_minimal_outbound(reply, assistant_name=self._assistant_name)
        self._sessions.add_message(user_id, "assistant", reply.get("text", ""))
        try:
            self._record_usage_log(
                user_id=user_id,
                conversation_id=chat_id or user_id,
                skill_name=usage_skill,
                usage_source=usage_source,
                route_decision=route_decision,
                business_metadata=usage_business_metadata,
            )
        except Exception:
            pass

        return reply

    def clear_user_conversation(self, user_id: str) -> None:
        """清空指定用户的会话上下文与状态。"""

        if not user_id:
            return

        try:
            if hasattr(self._sessions, "clear_user"):
                self._sessions.clear_user(user_id)
        except Exception as exc:
            logger.warning("清理会话消息失败: %s", exc)

        try:
            self._context_manager.clear(user_id)
        except Exception as exc:
            logger.warning("清理上下文失败: %s", exc)

        try:
            self._state_manager.clear_user(user_id)
        except Exception as exc:
            logger.warning("清理会话状态失败: %s", exc)

    async def _emit_processing_status(
        self,
        emitter: ProcessingStatusEmitter | None,
        status: ProcessingStatus,
        user_id: str,
        chat_id: str | None,
        chat_type: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if emitter is None:
            return
        try:
            maybe_awaitable = emitter(
                ProcessingStatusEvent(
                    status=status,
                    user_id=user_id,
                    chat_id=chat_id,
                    chat_type=chat_type,
                    metadata=metadata or {},
                )
            )
            if asyncio.iscoroutine(maybe_awaitable):
                await maybe_awaitable
        except Exception:
            logger.warning(
                "处理状态事件发送失败",
                extra={"event_code": "orchestrator.processing_status.emit_failed", "status": status.value},
            )

    @staticmethod
    def _expired_callback_response() -> dict[str, Any]:
        return {
            "status": "expired",
            "text": get_core_user_message(PendingActionExpiredError()),
        }

    @staticmethod
    def _missing_pending_action_response() -> dict[str, Any]:
        return {
            "status": "expired",
            "text": get_core_user_message(PendingActionNotFoundError()),
        }

    async def handle_card_action_callback(
        self,
        user_id: str,
        callback_action: str,
        callback_value: Mapping[str, Any] | None = None,
        batch_progress_emitter: BatchProgressEmitter | None = None,
    ) -> dict[str, Any]:
        callback = str(callback_action or "").strip().lower()
        value = dict(callback_value) if isinstance(callback_value, Mapping) else {}
        if callback in {"edit", "modify", "update_record_edit"}:
            edit_result = await self._handle_edit_callback(user_id=user_id, callback_value=value)
            if edit_result is not None:
                return edit_result

        pending = self._state_manager.get_pending_action(user_id)
        if pending is None:
            return self._missing_pending_action_response()

        action_name = pending.action

        if action_name == "query_list_navigation":
            query_callback_result = await self._handle_query_list_navigation_callback(
                user_id=user_id,
                pending_payload=pending.payload,
                callback_action=callback,
            )
            if query_callback_result is not None:
                return query_callback_result

        skill_name = {
            "create_record": "CreateSkill",
            "update_record": "UpdateSkill",
            "update_collect_fields": "UpdateSkill",
            "close_record": "UpdateSkill",
            "delete_record": "DeleteSkill",
            "create_reminder": "ReminderSkill",
            "batch_update_records": "UpdateSkill",
            "batch_close_records": "UpdateSkill",
            "batch_delete_records": "DeleteSkill",
        }.get(action_name)
        if not skill_name:
            return {"status": "processed", "text": "已处理"}

        expected_confirm = f"{action_name}_confirm"
        expected_cancel = f"{action_name}_cancel"
        expected_retry = f"{action_name}_retry"
        allowed_callbacks = {expected_confirm, expected_cancel}
        if action_name.startswith("batch_"):
            allowed_callbacks.add(expected_retry)

        if callback not in allowed_callbacks:
            logger.warning(
                "callback action mismatch",
                extra={
                    "event_code": "orchestrator.callback.action_mismatch",
                    "user_id": user_id,
                    "pending_action": action_name,
                    "callback_action": callback,
                },
            )
            return self._expired_callback_response()

        is_confirm = callback == expected_confirm
        is_cancel = callback == expected_cancel
        is_retry = callback == expected_retry and action_name.startswith("batch_")

        skill = self._router.get_skill(skill_name)
        if skill is None:
            return self._expired_callback_response()

        if action_name.startswith("batch_"):
            return await self._handle_batch_pending_action_callback(
                user_id=user_id,
                pending=pending,
                action_name=action_name,
                skill_name=skill_name,
                skill=skill,
                is_confirm=is_confirm,
                is_cancel=is_cancel,
                is_retry=is_retry,
                batch_progress_emitter=batch_progress_emitter,
            )

        operation_payloads = self._resolve_pending_action_payloads(pending)
        if not operation_payloads:
            operation_payloads = [pending.payload if isinstance(pending.payload, dict) else {}]
        if is_cancel and len(operation_payloads) > 1:
            operation_payloads = operation_payloads[:1]

        result = None
        sync_query = "确认" if is_confirm else "取消"
        operation_count = len(operation_payloads)
        for index, operation_payload in enumerate(operation_payloads, start=1):
            operation_action = self._resolve_operation_action_name(action_name, operation_payload)
            operation_query = self._build_pending_action_query(operation_action, is_confirm=is_confirm, is_cancel=is_cancel)
            sync_query = operation_query
            last_result: dict[str, Any] = {}
            if operation_action == "delete_record":
                last_result = {
                    "pending_delete": operation_payload,
                }
            context = SkillContext(
                query=operation_query,
                user_id=user_id,
                last_result=last_result,
                last_skill=skill_name,
                extra={
                    "pending_action": {
                        "action": operation_action,
                        "payload": operation_payload,
                    },
                    "callback_intent": "confirm" if is_confirm else "cancel",
                    "batch_operation": {
                        "index": index,
                        "total": operation_count,
                    } if operation_count > 1 else {},
                },
            )
            result = await skill.execute(context)
            if not result.success:
                break

        if result is None:
            return self._expired_callback_response()

        if result.success:
            if is_confirm and hasattr(self._state_manager, "confirm_pending_action"):
                self._state_manager.confirm_pending_action(user_id)
            if is_cancel and hasattr(self._state_manager, "cancel_pending_action"):
                self._state_manager.cancel_pending_action(user_id)
        self._sync_state_after_result(user_id, sync_query, result)
        rendered = self._response_renderer.render(result)
        rendered = self._maybe_apply_reply_personalization(user_id=user_id, user_text="", rendered=rendered)
        return {
            "status": "processed",
            "text": rendered.text_fallback,
            "outbound": rendered.to_dict(),
        }

    async def _handle_batch_pending_action_callback(
        self,
        *,
        user_id: str,
        pending: Any,
        action_name: str,
        skill_name: str,
        skill: Any,
        is_confirm: bool,
        is_cancel: bool,
        is_retry: bool,
        batch_progress_emitter: BatchProgressEmitter | None = None,
    ) -> dict[str, Any]:
        if is_cancel:
            if hasattr(self._state_manager, "cancel_pending_action"):
                self._state_manager.cancel_pending_action(user_id)
            cancel_result = SkillResult(
                success=True,
                skill_name=skill_name,
                data={"clear_pending_action": True},
                message="批量操作已取消",
                reply_text=get_user_message_by_code("batch_cancelled"),
            )
            self._sync_state_after_result(user_id, "取消", cancel_result)
            rendered = self._response_renderer.render(cancel_result)
            rendered = self._maybe_apply_reply_personalization(user_id=user_id, user_text="", rendered=rendered)
            return {
                "status": "processed",
                "text": rendered.text_fallback,
                "outbound": rendered.to_dict(),
            }

        batch_pending = self._coerce_batch_pending_state(action_name=action_name, pending=pending)
        operation_entries = list(batch_pending.operations)
        if not operation_entries:
            return self._expired_callback_response()

        if is_retry:
            remaining_ttl = float(batch_pending.expires_at or 0.0) - time.time()
            if remaining_ttl < 30:
                return self._expired_callback_response()
            self._reset_batch_retry_entries(operation_entries)

        pending_to_execute = [item for item in operation_entries if item.status == OperationExecutionStatus.PENDING]
        progress_enabled = batch_progress_emitter is not None and len(pending_to_execute) >= 3
        if progress_enabled:
            await self._emit_batch_progress(
                emitter=batch_progress_emitter,
                event=BatchProgressEvent(
                    phase=BatchProgressPhase.START,
                    user_id=user_id,
                    total=len(pending_to_execute),
                    metadata={
                        "action_name": action_name,
                        "is_retry": bool(is_retry),
                    },
                ),
            )

        last_result: SkillResult | None = None
        executed_total = 0
        executed_succeeded = 0
        executed_failed = 0
        for position, entry in enumerate(operation_entries):
            if entry.status != OperationExecutionStatus.PENDING:
                continue

            executed_total += 1

            operation_payload = dict(entry.payload)
            operation_action = self._resolve_operation_action_name(action_name, operation_payload)
            operation_query = self._build_pending_action_query(operation_action, is_confirm=is_confirm, is_cancel=False)
            context = SkillContext(
                query=operation_query,
                user_id=user_id,
                last_result={"pending_delete": operation_payload} if operation_action == "delete_record" else {},
                last_skill=skill_name,
                extra={
                    "pending_action": {
                        "action": operation_action,
                        "payload": operation_payload,
                    },
                    "callback_intent": "confirm",
                    "batch_operation": {
                        "index": position + 1,
                        "total": len(operation_entries),
                    },
                },
            )
            current = await skill.execute(context)
            last_result = current
            entry.executed_at = time.time()
            if current.success:
                entry.status = OperationExecutionStatus.SUCCEEDED
                entry.error_code = None
                entry.error_detail = None
                executed_succeeded += 1
                continue

            entry.status = OperationExecutionStatus.FAILED
            entry.error_code = self._extract_batch_operation_error_code(current)
            entry.error_detail = str(current.message or current.reply_text or "").strip() or None
            executed_failed += 1
            for remaining in operation_entries[position + 1 :]:
                if remaining.status == OperationExecutionStatus.PENDING:
                    remaining.status = OperationExecutionStatus.SKIPPED
            break

        if progress_enabled:
            await self._emit_batch_progress(
                emitter=batch_progress_emitter,
                event=BatchProgressEvent(
                    phase=BatchProgressPhase.COMPLETE,
                    user_id=user_id,
                    total=executed_total,
                    succeeded=executed_succeeded,
                    failed=executed_failed,
                    metadata={
                        "action_name": action_name,
                        "is_retry": bool(is_retry),
                    },
                ),
            )

        batch_pending.operations = operation_entries
        if hasattr(pending, "operations"):
            pending.operations = operation_entries

        if hasattr(self._state_manager, "update_pending_action_operations"):
            self._state_manager.update_pending_action_operations(user_id, batch_pending)

        succeeded = [item for item in operation_entries if item.status == OperationExecutionStatus.SUCCEEDED]
        failed = [item for item in operation_entries if item.status == OperationExecutionStatus.FAILED]
        remaining = [
            item
            for item in operation_entries
            if item.status in {OperationExecutionStatus.FAILED, OperationExecutionStatus.SKIPPED, OperationExecutionStatus.PENDING}
        ]
        total = len(operation_entries)

        if not failed and not remaining:
            if hasattr(self._state_manager, "confirm_pending_action"):
                self._state_manager.confirm_pending_action(user_id)
            success_result = SkillResult(
                success=True,
                skill_name=skill_name,
                data={"clear_pending_action": True},
                message="批量操作完成",
                reply_text=get_user_message_by_code("batch_all_succeeded", total=total),
            )
            self._sync_state_after_result(user_id, "确认", success_result)
            rendered = self._response_renderer.render(success_result)
            rendered = self._maybe_apply_reply_personalization(user_id=user_id, user_text="", rendered=rendered)
            return {
                "status": "processed",
                "text": rendered.text_fallback,
                "outbound": rendered.to_dict(),
            }

        failure_summary = "; ".join(
            [
                f"第{item.index + 1}条: {get_user_message_by_code(item.error_code or 'unknown_error')}"
                for item in failed
            ]
        )
        if succeeded:
            error_code = "batch_partial_success"
            text = get_user_message_by_code(
                error_code,
                total=total,
                succeeded=len(succeeded),
                failed=len(failed),
                failure_summary=failure_summary or "-",
            )
        else:
            error_code = "batch_all_failed"
            first_failed = failed[0] if failed else None
            first_error = get_user_message_by_code((first_failed.error_code if first_failed else "") or "unknown_error")
            text = get_user_message_by_code(
                error_code,
                total=total,
                first_error=first_error,
            )

        if remaining:
            text = f"{text}\n{get_user_message_by_code('batch_retry_available', remaining=len(remaining))}"

        payload = dict(batch_pending.payload) if isinstance(batch_pending.payload, dict) else {}
        payload["retry_available"] = True
        payload["retry_remaining"] = len(remaining)
        failure_result = SkillResult(
            success=False,
            skill_name=skill_name,
            data={
                "error_code": error_code,
                "pending_action": {
                    "action": action_name,
                    "payload": payload,
                    "operations": self._serialize_operation_entries(operation_entries),
                },
            },
            message=text,
            reply_text=text,
        )
        self._sync_state_after_result(user_id, "重试" if is_retry else "确认", failure_result)
        rendered = self._response_renderer.render(failure_result)
        rendered = self._maybe_apply_reply_personalization(user_id=user_id, user_text="", rendered=rendered)
        return {
            "status": "processed",
            "text": rendered.text_fallback,
            "outbound": rendered.to_dict(),
        }

    @staticmethod
    def _resolve_pending_action_payloads(pending: Any) -> list[dict[str, Any]]:
        iter_payloads = getattr(pending, "iter_operation_payloads", None)
        if callable(iter_payloads):
            try:
                data = iter_payloads()
            except Exception:
                data = []
            if isinstance(data, list):
                normalized = [dict(item) for item in data if isinstance(item, Mapping)]
                if normalized:
                    return normalized

        operations = getattr(pending, "operations", None)
        if isinstance(operations, list):
            normalized: list[dict[str, Any]] = []
            for item in operations:
                if isinstance(item, OperationEntry):
                    if item.status == OperationExecutionStatus.PENDING:
                        normalized.append(dict(item.payload))
                    continue
                if isinstance(item, Mapping):
                    normalized.append(dict(item))
            if normalized:
                return normalized

        payload_raw = getattr(pending, "payload", None)
        payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
        nested_operations = payload.get("operations")
        if isinstance(nested_operations, list):
            normalized = [dict(item) for item in nested_operations if isinstance(item, Mapping)]
            if normalized:
                return normalized

        if payload:
            return [payload]
        return []

    @staticmethod
    def _serialize_operation_entries(entries: list[OperationEntry]) -> list[dict[str, Any]]:
        return [
            {
                "index": int(entry.index),
                "payload": dict(entry.payload),
                "status": str(entry.status.value),
                "error_code": entry.error_code,
                "error_detail": entry.error_detail,
                "executed_at": entry.executed_at,
            }
            for entry in entries
        ]

    @staticmethod
    def _coerce_batch_pending_state(*, action_name: str, pending: Any) -> PendingActionState:
        if isinstance(pending, PendingActionState):
            return pending

        payload_raw = getattr(pending, "payload", None)
        payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
        operations_raw = getattr(pending, "operations", None)
        operations: list[OperationEntry] = []
        if isinstance(operations_raw, list):
            for index, item in enumerate(operations_raw):
                if isinstance(item, OperationEntry):
                    operations.append(item)
                    continue
                if not isinstance(item, Mapping):
                    continue
                payload_item = item.get("payload") if isinstance(item.get("payload"), Mapping) else item
                operations.append(
                    OperationEntry(
                        index=item.get("index", index),
                        payload=dict(payload_item) if isinstance(payload_item, Mapping) else {},
                        status=item.get("status", OperationExecutionStatus.PENDING.value),
                        error_code=item.get("error_code"),
                        error_detail=item.get("error_detail"),
                        executed_at=item.get("executed_at"),
                    )
                )

        now = time.time()
        created_at_raw = getattr(pending, "created_at", now)
        expires_at_raw = getattr(pending, "expires_at", now + 300)
        try:
            created_at = float(created_at_raw)
        except Exception:
            created_at = now
        try:
            expires_at = float(expires_at_raw)
        except Exception:
            expires_at = now + 300

        return PendingActionState(
            action=action_name,
            payload=payload,
            operations=operations,
            created_at=created_at,
            expires_at=expires_at,
        )

    @staticmethod
    def _reset_batch_retry_entries(entries: list[OperationEntry]) -> None:
        for entry in entries:
            if entry.status in {OperationExecutionStatus.FAILED, OperationExecutionStatus.SKIPPED}:
                entry.status = OperationExecutionStatus.PENDING
                entry.error_code = None
                entry.error_detail = None
                entry.executed_at = None

    @staticmethod
    def _extract_batch_operation_error_code(result: SkillResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        explicit = str(data.get("error_code") or "").strip()
        if explicit:
            return explicit

        text = f"{result.message}\n{result.reply_text}".lower()
        if any(token in text for token in ["recordidnotfound", "record not found", "不存在", "未找到"]):
            return "update_record_deleted"
        return "unknown_error"

    async def _emit_batch_progress(
        self,
        *,
        emitter: BatchProgressEmitter | None,
        event: BatchProgressEvent,
    ) -> None:
        if emitter is None:
            return
        try:
            maybe_awaitable = emitter(event)
            if asyncio.iscoroutine(maybe_awaitable):
                await maybe_awaitable
        except Exception:
            logger.warning(
                "批量进度消息发送失败",
                extra={
                    "event_code": "orchestrator.batch_progress.emit_failed",
                    "phase": event.phase.value,
                    "total": int(event.total),
                },
            )

    @staticmethod
    def _resolve_operation_action_name(action_name: str, operation_payload: Mapping[str, Any]) -> str:
        payload_action = str(operation_payload.get("action") or "").strip()
        if payload_action:
            return payload_action

        return {
            "batch_update_records": "update_record",
            "batch_close_records": "close_record",
            "batch_delete_records": "delete_record",
        }.get(action_name, action_name)

    @staticmethod
    def _build_pending_action_query(action_name: str, *, is_confirm: bool, is_cancel: bool) -> str:
        if is_cancel:
            return "取消"
        if is_confirm and action_name == "delete_record":
            return "确认删除"
        return "确认"

    async def _handle_edit_callback(self, user_id: str, callback_value: Mapping[str, Any]) -> dict[str, Any] | None:
        skill = self._router.get_skill("UpdateSkill")
        if skill is None:
            return self._expired_callback_response()

        record = self._resolve_record_from_callback(user_id=user_id, callback_value=callback_value)
        record_id = str(record.get("record_id") or "").strip()
        if not record_id:
            return self._expired_callback_response()

        context = SkillContext(
            query="修改该案件内容",
            user_id=user_id,
            last_result={"records": [record]},
            last_skill="UpdateSkill",
            extra={"active_record": record},
        )
        result = await skill.execute(context)
        self._sync_state_after_result(user_id, context.query, result)

        rendered = self._response_renderer.render(result)
        rendered = self._maybe_apply_reply_personalization(user_id=user_id, user_text="", rendered=rendered)
        return {
            "status": "processed",
            "text": rendered.text_fallback,
            "outbound": rendered.to_dict(),
        }

    def _resolve_record_from_callback(self, user_id: str, callback_value: Mapping[str, Any]) -> dict[str, Any]:
        record_id = str(callback_value.get("record_id") or "").strip()
        table_type = str(callback_value.get("table_type") or "").strip().lower()
        table_name_hint = self._table_name_from_type(table_type)

        active = self._state_manager.get_active_record(user_id)
        if active is not None and (not record_id or active.record_id == record_id):
            active_record = dict(active.record) if isinstance(active.record, dict) else {}
            active_record.setdefault("record_id", active.record_id)
            if active.table_id:
                active_record.setdefault("table_id", active.table_id)
            if active.table_name:
                active_record.setdefault("table_name", active.table_name)
            elif table_name_hint:
                active_record.setdefault("table_name", table_name_hint)
            return active_record

        last_result = self._state_manager.get_last_result(user_id)
        if last_result is not None and isinstance(last_result.records, list):
            for item in last_result.records:
                if not isinstance(item, dict):
                    continue
                current_record_id = str(item.get("record_id") or "").strip()
                if record_id and current_record_id != record_id:
                    continue
                resolved = dict(item)
                if record_id:
                    resolved.setdefault("record_id", record_id)
                if table_name_hint:
                    resolved.setdefault("table_name", table_name_hint)
                return resolved

        if record_id:
            fallback: dict[str, Any] = {
                "record_id": record_id,
                "fields_text": {
                    "项目ID": record_id,
                },
            }
            if table_name_hint:
                fallback["table_name"] = table_name_hint
            return fallback

        return {}

    def _table_name_from_type(self, table_type: str) -> str:
        mapping = {
            "case": "案件项目总库",
            "contracts": "合同管理表",
            "bidding": "招投标台账",
            "team_overview": "团队成员工作总览",
        }
        return mapping.get(table_type, "")

    async def _handle_query_list_navigation_callback(
        self,
        user_id: str,
        pending_payload: dict[str, Any],
        callback_action: str,
    ) -> dict[str, Any] | None:
        callbacks = pending_payload.get("callbacks") if isinstance(pending_payload, dict) else None
        if not isinstance(callbacks, dict):
            return self._expired_callback_response()

        callback_data_raw = callbacks.get(callback_action)
        callback_data = callback_data_raw if isinstance(callback_data_raw, dict) else None
        if callback_data is None:
            for key, value in callbacks.items():
                if str(key).strip().lower() == callback_action and isinstance(value, dict):
                    callback_data = value
                    break
        if callback_data is None:
            logger.warning(
                "query callback action mismatch",
                extra={
                    "event_code": "orchestrator.callback.query_action_mismatch",
                    "user_id": user_id,
                    "callback_action": callback_action,
                },
            )
            return self._expired_callback_response()

        kind = str(callback_data.get("kind") or "query").strip().lower()
        if kind == "no_more":
            return {
                "status": "processed",
                "text": str(callback_data.get("text") or "已经是最后一页了。"),
            }

        skill = self._router.get_skill("QuerySkill")
        if skill is None:
            return self._expired_callback_response()

        query = str(callback_data.get("query") or "").strip() or "下一页"
        extra: dict[str, Any] = {}
        extra_raw = callback_data.get("extra")
        if isinstance(extra_raw, dict):
            extra = dict(extra_raw)
        if kind == "pagination":
            pagination_raw = callback_data.get("pagination")
            if isinstance(pagination_raw, dict):
                extra["pagination"] = dict(pagination_raw)

        context = SkillContext(
            query=query,
            user_id=user_id,
            extra=extra,
        )
        result = await skill.execute(context)
        self._sync_state_after_result(user_id, query, result)
        rendered = self._response_renderer.render(result)
        rendered = self._maybe_apply_reply_personalization(user_id=user_id, user_text="", rendered=rendered)
        return {
            "status": "processed",
            "text": rendered.text_fallback,
            "outbound": rendered.to_dict(),
        }

    def _record_usage_log(
        self,
        user_id: str,
        conversation_id: str,
        skill_name: str,
        usage_source: str,
        route_decision: RoutingDecision,
        business_metadata: dict[str, Any] | None = None,
    ) -> None:
        usage_logger = getattr(self, "_usage_logger", None)
        if usage_logger is None:
            return
        usage = self._drain_latest_llm_usage()
        model = str((usage or {}).get("model") or getattr(getattr(self, "_llm", None), "model_name", ""))
        token_count = int((usage or {}).get("token_count") or 0)
        prompt_tokens = int((usage or {}).get("prompt_tokens") or 0)
        completion_tokens = int((usage or {}).get("completion_tokens") or 0)
        usage_cost = float((usage or {}).get("cost") or 0.0)
        estimated = bool((usage or {}).get("estimated", True))
        latency_ms = int((usage or {}).get("latency_ms") or 0)
        usage_metadata = (usage or {}).get("metadata") if isinstance(usage, dict) else None
        metadata = {
            "route_label": route_decision.route_label,
            "model_selected": route_decision.model_selected,
            "complexity": route_decision.complexity,
            "route_reason": route_decision.reason,
        }
        computed_cost, computed_estimated, warning_label = compute_usage_cost(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            token_count=token_count,
            pricing_map=getattr(self, "_usage_model_pricing", {}),
        )
        usage_cost = computed_cost
        estimated = computed_estimated
        if warning_label:
            metadata["cost_warning"] = warning_label
        if isinstance(usage_metadata, dict):
            metadata.update({str(k): str(v) for k, v in usage_metadata.items()})
        if latency_ms > 0:
            metadata["latency_ms"] = str(latency_ms)
        try:
            record = UsageRecord(
                ts=now_iso(),
                user_id=user_id,
                conversation_id=conversation_id,
                model=model,
                skill=str(skill_name or "unknown"),
                token_count=token_count,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=usage_cost,
                usage_source=str(usage_source or "text"),
                estimated=estimated,
                metadata=metadata,
                business_metadata=dict(business_metadata or {}),
            )
            ok = usage_logger.log(record)
            if not ok:
                self._on_usage_record_written(record)
            record_usage_log_write("ok" if ok else "noop")
        except Exception:
            record_usage_log_write("error")

    def _extract_usage_business_metadata(self, result_data: dict[str, Any]) -> dict[str, Any]:
        data = result_data if isinstance(result_data, dict) else {}
        close_semantic = str(data.get("close_semantic") or "").strip()
        close_profile = str(data.get("close_profile") or "").strip()
        if not close_semantic and not close_profile:
            return {}

        action_classification = "close_case" if close_semantic else ""
        output: dict[str, Any] = {
            "action_classification": action_classification,
            "close_semantic": close_semantic,
            "close_profile": close_profile or close_semantic,
        }
        return {key: value for key, value in output.items() if str(value).strip()}

    def _drain_latest_llm_usage(self) -> dict[str, Any] | None:
        usages: list[dict[str, Any]] = []
        for client in (getattr(self, "_llm", None), getattr(self, "_task_llm", None)):
            if client is None:
                continue
            consume = getattr(client, "consume_last_usage", None)
            if not callable(consume):
                continue
            try:
                data = consume()
            except Exception:
                data = None
            if isinstance(data, dict):
                usages.append(data)
        if not usages:
            return None
        return max(usages, key=lambda item: float(item.get("ts", 0.0)))

    def _on_usage_record_written(self, record: UsageRecord) -> None:
        try:
            self._cost_monitor.record_cost(
                skill=str(record.skill or "unknown"),
                cost=float(record.cost or 0.0),
                ts=str(record.ts or ""),
            )
        except Exception:
            logger.warning(
                "成本监控记录失败",
                extra={"event_code": "cost.monitor.record_failed"},
            )

    async def _build_extra(
        self,
        text: str,
        user_id: str,
        llm_context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        构建额外上下文数据（如 Soul、记忆、时间范围）

        参数:
            text: 用户输入文本
            user_id: 用户 ID
            llm_context: 预构建的 LLM 上下文 (可选)

        返回:
            包含上下文信息的字典
        """
        extra: dict[str, Any] = {}
        
        # Soul + Memory
        if llm_context is None:
            llm_context = await self._build_llm_context(user_id, query=text)

        context_data = llm_context or {}
        extra["soul_prompt"] = context_data.get("soul_prompt", "")
        extra["shared_memory"] = context_data.get("shared_memory", "")
        extra["user_memory"] = context_data.get("user_memory", "")
        extra["recent_logs"] = context_data.get("recent_logs", "")
        extra["usage_source"] = "file" if context_data.get("file_context", "") else "text"

        # 解析时间范围
        date_range = await self._resolve_time_range(text, llm_context)
        if date_range:
            extra["date_from"] = date_range.get("date_from")
            extra["date_to"] = date_range.get("date_to")
            if date_range.get("time_from"):
                extra["time_from"] = date_range.get("time_from")
            if date_range.get("time_to"):
                extra["time_to"] = date_range.get("time_to")
        
        return extra

    def _record_memory(
        self,
        user_id: str,
        user_text: str,
        reply_text: str,
        skill_name: str,
        result_data: dict[str, Any],
        extra: dict[str, Any],
    ) -> None:
        """写入对话日志与用户记忆（长期记忆 + 自动事件 + 偏好提取）"""
        try:
            self._memory_manager.append_daily_log(user_id, f"用户: {user_text}")
            self._memory_manager.append_daily_log(user_id, f"助手({skill_name}): {reply_text}")
        except Exception:
            return

        try:
            remembered = self._extract_memory_trigger(user_text)
            if remembered:
                self._memory_manager.remember_user(user_id, remembered)
        except Exception:
            pass

        # ============================================
        # region 自动偏好提取
        # ============================================
        try:
            if self._has_preference_signal(user_text):
                pref = self._extract_preference(user_text)
                if pref:
                    self._memory_manager.remember_user(user_id, f"[偏好] {pref}")
                    logger.info(
                        "已自动捕获用户偏好",
                        extra={
                            "event_code": "orchestrator.memory.preference_captured",
                            "target_user_id": user_id,
                            "preference": pref,
                        },
                    )
        except Exception:
            pass
        # endregion
        # ============================================

        self._record_event(user_id, skill_name, result_data, extra)
        self._record_midterm_memory(user_id, user_text, skill_name, result_data)

    def _trim_session_context(self, user_id: str) -> None:
        trim_method = getattr(self._sessions, "trim_context_to_token_budget", None)
        if not callable(trim_method):
            return

        max_tokens = int(getattr(self, "_context_trim_tokens", 3800))
        try:
            trim_method(
                user_id=user_id,
                max_tokens=max_tokens,
                keep_recent_messages=2,
            )
        except Exception as exc:
            logger.warning(
                "会话上下文裁剪失败: %s",
                exc,
                extra={"event_code": "orchestrator.session.trim_failed"},
            )

    def _record_midterm_memory(
        self,
        user_id: str,
        user_text: str,
        skill_name: str,
        result_data: dict[str, Any],
    ) -> None:
        store = getattr(self, "_midterm_memory_store", None)
        if store is None:
            return

        try:
            extractor = getattr(self, "_midterm_extractor", None)
            if extractor is None:
                return
            items = extractor.build_items(
                user_text=user_text,
                skill_name=skill_name,
                result_data=result_data,
            )
            if not items:
                return
            store.write_items(user_id=user_id, items=items)
        except Exception as exc:
            logger.warning(
                "写入中期记忆失败: %s",
                exc,
                extra={"event_code": "orchestrator.midterm_memory.write_failed"},
            )

    def _extract_memory_trigger(self, text: str) -> str | None:
        """识别并提取用户的长期记忆内容"""
        triggers = ["记住", "请记住", "帮我记住"]
        for trigger in triggers:
            if trigger in text:
                content = text.split(trigger, 1)[-1]
                content = content.strip("，。！？：； ")
                return content or None
        return None

    # ============================================
    # region 偏好信号检测与提取
    # ============================================
    _PREFERENCE_SIGNALS: list[tuple[str, str]] = [
        # (关键词/短语, 对应偏好描述)
        ("太长了", "偏好简洁回复"),
        ("简单点", "偏好简洁回复"),
        ("简短", "偏好简洁回复"),
        ("别啰嗦", "偏好简洁回复"),
        ("详细点", "偏好详细回复"),
        ("详细说", "偏好详细回复"),
        ("展开说", "偏好详细回复"),
        ("多说点", "偏好详细回复"),
        ("别加emoji", "不喜欢 emoji"),
        ("不要emoji", "不喜欢 emoji"),
        ("不要表情", "不喜欢 emoji"),
        ("加点表情", "喜欢 emoji 装饰"),
        ("说中文", "偏好中文回复"),
        ("用英文", "偏好英文回复"),
        ("每次都问", "希望减少重复确认"),
        ("不用确认", "希望跳过二次确认"),
        ("默认查", "常用默认表查询"),
    ]

    def _has_preference_signal(self, text: str) -> bool:
        """零成本关键词检测：用户文本是否包含偏好信号"""
        text_lower = text.lower()
        return any(signal in text_lower for signal, _ in self._PREFERENCE_SIGNALS)

    def _extract_preference(self, text: str) -> str | None:
        """从用户文本中提取偏好描述（规则匹配，无需 LLM）"""
        text_lower = text.lower()
        matched: list[str] = []
        for signal, pref in self._PREFERENCE_SIGNALS:
            if signal in text_lower and pref not in matched:
                matched.append(pref)
        return "；".join(matched) if matched else None

    def _extract_explicit_reply_preferences(self, user_text: str) -> dict[str, str]:
        text = str(user_text or "").strip().lower()
        if not text:
            return {}

        preferences: dict[str, str] = {}
        if any(token in text for token in ["专业", "正式", "严谨"]):
            preferences["tone"] = "professional"
        elif any(token in text for token in ["口语", "随意", "轻松", "亲切"]):
            preferences["tone"] = "friendly"

        if any(token in text for token in ["简短", "精简", "一句话", "太长了", "短一点"]):
            preferences["length"] = "short"
        elif any(token in text for token in ["详细", "展开", "多说", "长一点"]):
            preferences["length"] = "long"

        return preferences

    def _resolve_reply_preferences(self, user_id: str, explicit: dict[str, str]) -> dict[str, str]:
        session_prefs = self._state_manager.get_reply_preferences(user_id)
        tone = explicit.get("tone") or session_prefs.get("tone") or "professional"
        length = explicit.get("length") or session_prefs.get("length") or "medium"
        return {"tone": tone, "length": length}

    def _personalize_text(self, text: str, tone: str, length: str) -> str:
        body = str(text or "").strip()
        if not body:
            return text

        if length == "short":
            lines = [line.strip() for line in body.splitlines() if line.strip()]
            if len(lines) > 2:
                body = "\n".join(lines[:2])
        elif length == "long":
            if "如需我继续展开" not in body:
                body = f"{body}\n\n如需我继续展开某一条，请告诉我序号。"

        prefix = "处理结果如下："
        if tone == "friendly":
            prefix = "好的，我来帮你整理如下："

        if body.startswith(prefix):
            return body
        return f"{prefix}\n{body}"

    def _maybe_apply_reply_personalization(
        self,
        user_id: str,
        user_text: str,
        rendered: RenderedResponse,
    ) -> RenderedResponse:
        if not bool(getattr(self, "_reply_personalization_enabled", False)):
            return rendered
            
        if rendered.meta and rendered.meta.get("skill_name") == "ChitchatSkill":
            return rendered

        explicit = self._extract_explicit_reply_preferences(user_text)
        if explicit:
            self._state_manager.set_reply_preferences(user_id, explicit)
        prefs = self._resolve_reply_preferences(user_id, explicit)
        updated_text = self._personalize_text(
            rendered.text_fallback,
            tone=prefs.get("tone", "professional"),
            length=prefs.get("length", "medium"),
        )

        outbound = rendered.to_dict()
        outbound["text_fallback"] = updated_text
        blocks = outbound.get("blocks")
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "paragraph":
                    continue
                content = block.get("content")
                if not isinstance(content, dict):
                    content = {}
                    block["content"] = content
                content["text"] = updated_text
                break
        return RenderedResponse.from_outbound(outbound, updated_text)
    # endregion
    # ============================================

    def _record_event(
        self,
        user_id: str,
        skill_name: str,
        result_data: dict[str, Any],
        extra: dict[str, Any],
    ) -> None:
        """自动事件写入策略"""
        if skill_name == "QuerySkill":
            records = result_data.get("records", []) if result_data else []
            total = result_data.get("total") if result_data else None
            count = total if isinstance(total, int) else len(records)
            if count > 0:
                date_from = extra.get("date_from")
                date_to = extra.get("date_to")
                date_hint = ""
                if date_from or date_to:
                    date_hint = f"（时间范围：{date_from or '-'} ~ {date_to or '-'}）"
                event_text = f"事件: 查询案件共 {count} 条{date_hint}"
                self._memory_manager.append_daily_log(
                    user_id,
                    event_text,
                    vectorize=True,
                    metadata={
                        "type": "auto",
                        "created_at": datetime.now().isoformat(),
                        "source": "event",
                        "tags": f"skill:{skill_name}" if skill_name else "event",
                    },
                )

        if skill_name == "ReminderSkill":
            content = result_data.get("content") if result_data else None
            remind_time = result_data.get("remind_time") if result_data else None
            if content or remind_time:
                event_text = f"事件: 创建提醒 {content or ''} {remind_time or ''}".strip()
                self._memory_manager.append_daily_log(
                    user_id,
                    event_text,
                    vectorize=True,
                    metadata={
                        "type": "auto",
                        "created_at": datetime.now().isoformat(),
                        "source": "event",
                        "tags": f"skill:{skill_name}" if skill_name else "event",
                    },
                )

        if skill_name == "SummarySkill":
            total = result_data.get("total") if result_data else None
            count = total if isinstance(total, int) else 0
            if count > 0:
                event_text = f"事件: 已生成汇总（共 {count} 条）"
                self._memory_manager.append_daily_log(
                    user_id,
                    event_text,
                    vectorize=True,
                    metadata={
                        "type": "auto",
                        "created_at": datetime.now().isoformat(),
                        "source": "event",
                        "tags": f"skill:{skill_name}" if skill_name else "event",
                    },
                )

        if skill_name == "ChitchatSkill":
            response_type = result_data.get("type") if result_data else None
            if response_type:
                event_text = f"事件: 闲聊响应（{response_type}）"
                self._memory_manager.append_daily_log(
                    user_id,
                    event_text,
                    vectorize=True,
                    metadata={
                        "type": "auto",
                        "created_at": datetime.now().isoformat(),
                        "source": "event",
                        "tags": f"skill:{skill_name}" if skill_name else "event",
                    },
                )

    def _sync_state_after_result(self, user_id: str, query: str, result: Any) -> None:
        """将技能执行结果同步到会话状态机。"""
        try:
            data = result.data or {}
            skill_name = result.skill_name
            self._state_manager.set_last_skill(user_id, skill_name)

            pending_action_data = data.get("pending_action") if isinstance(data, dict) else None
            if isinstance(pending_action_data, dict):
                action = str(pending_action_data.get("action") or "").strip()
                payload = pending_action_data.get("payload")
                operations_raw = pending_action_data.get("operations")
                operations: list[dict[str, Any] | OperationEntry] = []
                if isinstance(operations_raw, list):
                    for item in operations_raw:
                        if isinstance(item, OperationEntry):
                            operations.append(item)
                        elif isinstance(item, dict):
                            operations.append(dict(item))
                ttl_seconds = None
                raw_ttl = pending_action_data.get("ttl_seconds")
                try:
                    if raw_ttl is not None and str(raw_ttl).strip() != "":
                        ttl_seconds = max(1, int(raw_ttl))
                except Exception:
                    ttl_seconds = None
                if action:
                    self._state_manager.set_pending_action(
                        user_id,
                        action=action,
                        payload=payload if isinstance(payload, dict) else {},
                        operations=operations,
                        ttl_seconds=ttl_seconds,
                    )
            if bool(data.get("clear_pending_action")):
                self._state_manager.clear_pending_action(user_id)

            if skill_name == "DeleteSkill":
                pending = data.get("pending_delete") if isinstance(data, dict) else None
                delete_records = data.get("records") if isinstance(data, dict) else None
                if isinstance(pending, dict) and pending.get("record_id"):
                    record_id = str(pending.get("record_id"))
                    summary = str(pending.get("case_no") or pending.get("record_summary") or "")
                    table_id = str(pending.get("table_id") or "").strip() or None
                    self._state_manager.set_pending_delete(user_id, record_id, summary, table_id=table_id)
                elif result.success:
                    self._state_manager.clear_pending_delete(user_id)

                if isinstance(delete_records, list) and len(delete_records) > 1:
                    self._state_manager.set_last_result(user_id, delete_records, query)

                if result.success:
                    deleted_record_id = str(data.get("record_id") or "").strip()
                    active = self._state_manager.get_active_record(user_id)
                    if deleted_record_id and active and active.record_id == deleted_record_id:
                        self._state_manager.clear_active_record(user_id)

            if skill_name == "UpdateSkill":
                update_records = data.get("records") if isinstance(data, dict) else None
                if isinstance(update_records, list) and len(update_records) > 1:
                    self._state_manager.set_last_result(user_id, update_records, query)

            if skill_name == "QuerySkill" and result.success:
                records = data.get("records") if isinstance(data, dict) else None
                if isinstance(records, list):
                    self._state_manager.set_last_result(user_id, records, query)

                    if records:
                        table_id, table_name = self._resolve_table_context_from_result(data, records)
                        if table_id or table_name:
                            self._state_manager.set_active_table(user_id, table_id, table_name)
                        if len(records) == 1 and isinstance(records[0], dict):
                            self._state_manager.set_active_record(
                                user_id,
                                records[0],
                                table_id=table_id,
                                table_name=table_name,
                                source="query_single",
                            )
                    else:
                        self._state_manager.clear_active_record(user_id)

                pagination = data.get("pagination") if isinstance(data, dict) else None
                query_meta = data.get("query_meta") if isinstance(data, dict) else None
                if isinstance(pagination, dict) and isinstance(query_meta, dict):
                    has_more = bool(pagination.get("has_more", False))
                    if has_more:
                        tool = str(query_meta.get("tool") or "")
                        params_raw = query_meta.get("params")
                        params: dict[str, Any] = params_raw if isinstance(params_raw, dict) else {}
                        page_token = pagination.get("page_token")
                        current_page = int(pagination.get("current_page") or 1)
                        total = pagination.get("total")
                        total_num = int(total) if isinstance(total, int) else None
                        self._state_manager.set_pagination(
                            user_id=user_id,
                            tool=tool,
                            params=params,
                            page_token=str(page_token) if page_token else None,
                            current_page=current_page,
                            total=total_num,
                        )
                    else:
                        self._state_manager.clear_pagination(user_id)
                else:
                    # 查询结果未携带分页信息时，清空旧分页状态
                    self._state_manager.clear_pagination(user_id)
            elif skill_name == "CreateSkill" and result.success:
                if "pending_action" not in data:
                    self._state_manager.clear_pending_action(user_id)
                table_id = str(data.get("table_id") or "").strip() or None
                table_name = str(data.get("table_name") or "").strip() or None
                if table_id or table_name:
                    self._state_manager.set_active_table(user_id, table_id, table_name)
                record_id = str(data.get("record_id") or "").strip()
                if record_id:
                    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
                    record = {
                        "record_id": record_id,
                        "record_url": data.get("record_url", ""),
                        "fields": fields,
                        "fields_text": fields,
                        "table_id": table_id,
                        "table_name": table_name,
                    }
                    self._state_manager.set_active_record(
                        user_id,
                        record,
                        table_id=table_id,
                        table_name=table_name,
                        source="create",
                    )
            elif skill_name == "UpdateSkill" and result.success:
                if "pending_action" not in data:
                    self._state_manager.clear_pending_action(user_id)
                table_id = str(data.get("table_id") or "").strip() or None
                table_name = str(data.get("table_name") or "").strip() or None
                if table_id or table_name:
                    self._state_manager.set_active_table(user_id, table_id, table_name)
                record_id = str(data.get("record_id") or "").strip()
                if record_id:
                    updated_fields = data.get("updated_fields") if isinstance(data.get("updated_fields"), dict) else {}
                    record = {
                        "record_id": record_id,
                        "record_url": data.get("record_url", ""),
                        "fields": updated_fields,
                        "fields_text": updated_fields,
                        "table_id": table_id,
                        "table_name": table_name,
                    }
                    self._state_manager.set_active_record(
                        user_id,
                        record,
                        table_id=table_id,
                        table_name=table_name,
                        source="update",
                    )

            elif skill_name != "QuerySkill":
                # 非查询请求会使分页上下文失效
                self._state_manager.clear_pagination(user_id)
        except Exception as exc:
            logger.warning(
                "同步会话状态失败: %s",
                exc,
                extra={"event_code": "orchestrator.state.sync_failed"},
            )

    def _resolve_table_context_from_result(
        self,
        result_data: dict[str, Any],
        records: list[dict[str, Any]],
    ) -> tuple[str | None, str | None]:
        table_id = str(result_data.get("table_id") or "").strip() or None
        table_name = str(result_data.get("table_name") or "").strip() or None

        query_meta = result_data.get("query_meta")
        if isinstance(query_meta, dict):
            params = query_meta.get("params")
            if isinstance(params, dict):
                if not table_id:
                    table_id = str(params.get("table_id") or "").strip() or None
                if not table_name:
                    table_name = str(params.get("table_name") or "").strip() or None

        if not table_id and records:
            table_id = self._extract_table_id_from_record(records[0])
        return table_id, table_name

    def _extract_table_id_from_record(self, record: dict[str, Any]) -> str | None:
        url = str(record.get("record_url") or "").strip()
        if not url:
            return None
        match = re.search(r"[?&]table=([^&#]+)", url)
        if match:
            return match.group(1)
        return None

    async def _build_llm_context(
        self,
        user_id: str,
        query: str | None = None,
        file_markdown: str = "",
        file_provider: str = "none",
    ) -> dict[str, str]:
        context: dict[str, str] = {
            "soul_prompt": "",
            "shared_memory": "",
            "user_memory": "",
            "recent_logs": "",
            "midterm_memory": "",
            "file_context": "",
        }
        try:
            context["soul_prompt"] = self._soul_manager.build_system_prompt()
        except Exception:
            context["soul_prompt"] = ""

        try:
            snapshot = self._memory_manager.snapshot(user_id)
            context["shared_memory"] = snapshot.shared_memory
            context["user_memory"] = snapshot.user_memory
            context["recent_logs"] = snapshot.recent_logs
        except Exception:
            context["shared_memory"] = ""
            context["user_memory"] = ""
            context["recent_logs"] = ""

        if query and query.strip():
            try:
                vector_hits = await self._memory_manager.search_memory(
                    user_id,
                    query,
                    top_k=self._vector_top_k,
                )
            except Exception:
                vector_hits = ""

            if vector_hits:
                if context["user_memory"]:
                    context["user_memory"] = "\n".join([vector_hits, context["user_memory"]]).strip()
                elif context["recent_logs"]:
                    context["recent_logs"] = "\n".join([vector_hits, context["recent_logs"]]).strip()
                else:
                    context["user_memory"] = vector_hits

        context["midterm_memory"] = self._build_midterm_memory_context(user_id)
        context["file_context"] = self._build_file_context(
            user_id=user_id,
            file_markdown=file_markdown,
            provider=file_provider,
        )

        return context

    def _build_file_context(self, user_id: str, file_markdown: str, provider: str = "none") -> str:
        del user_id
        provider_name = str(provider or "none")
        if not bool(getattr(self, "_file_context_enabled", False)):
            record_file_pipeline("context", "skipped", provider_name)
            return ""

        text = str(file_markdown or "").strip()
        if not text:
            record_file_pipeline("context", "skipped", provider_name)
            return ""

        char_budget = max(20, int(getattr(self, "_file_context_max_chars", 2000)))
        token_budget = max(1, int(getattr(self, "_file_context_max_tokens", 500)))
        token_char_budget = token_budget * 4
        hard_limit = max(8, min(char_budget, token_char_budget))
        if len(text) <= hard_limit:
            record_file_pipeline("context", "applied", provider_name)
            return text

        clipped = text[: max(1, hard_limit - 3)].rstrip()
        record_file_pipeline("context", "truncated", provider_name)
        return f"{clipped}..."

    def _build_midterm_memory_context(self, user_id: str) -> str:
        if not getattr(self, "_midterm_memory_inject_to_llm", False):
            return ""

        store = getattr(self, "_midterm_memory_store", None)
        if store is None:
            return ""

        recent_limit = max(1, int(getattr(self, "_midterm_memory_llm_recent_limit", 6)))
        max_chars = max(80, int(getattr(self, "_midterm_memory_llm_max_chars", 240)))
        try:
            rows = store.list_recent(user_id=user_id, limit=recent_limit)
        except Exception as exc:
            logger.warning(
                "读取中期记忆失败: %s",
                exc,
                extra={"event_code": "orchestrator.midterm_memory.read_failed"},
            )
            return ""

        if not rows:
            return ""

        lines: list[str] = []
        seen: set[str] = set()
        for row in reversed(rows):
            kind = str(row.get("kind") or "").strip()
            value = str(row.get("value") or "").strip()
            if not value:
                continue
            if kind == "event":
                skill_name = row.get("metadata", {}).get("skill_name") if isinstance(row.get("metadata"), dict) else None
                if skill_name:
                    rendered = f"event:{skill_name}"
                else:
                    rendered = f"event:{value}"
            else:
                rendered = f"{kind}:{value}" if kind else value
            if rendered in seen:
                continue
            seen.add(rendered)
            lines.append(rendered)

        if not lines:
            return ""

        text = "\n".join(lines)
        if len(text) <= max_chars:
            return text

        clipped = text[: max_chars - 1].rstrip()
        if not clipped:
            return ""
        return clipped + "..."

    def _format_llm_context(self, llm_context: dict[str, str] | None) -> str:
        if not llm_context:
            return ""

        parts = []
        soul_prompt = llm_context.get("soul_prompt", "").strip()
        if soul_prompt:
            parts.append(soul_prompt)

        memory_parts = []
        user_memory = llm_context.get("user_memory", "").strip()
        shared_memory = llm_context.get("shared_memory", "").strip()
        recent_logs = llm_context.get("recent_logs", "").strip()
        midterm_memory = llm_context.get("midterm_memory", "").strip()
        file_context = llm_context.get("file_context", "").strip()
        if user_memory:
            memory_parts.append(f"用户记忆：\n{user_memory}")
        if shared_memory:
            memory_parts.append(f"团队共享记忆：\n{shared_memory}")
        if recent_logs:
            memory_parts.append(f"最近日志：\n{recent_logs}")
        if midterm_memory:
            memory_parts.append(f"中期记忆（摘要）：\n{midterm_memory}")
        if file_context:
            memory_parts.append(f"文件上下文（节选）：\n{file_context}")

        if memory_parts:
            parts.append("参考记忆：\n" + "\n\n".join(memory_parts))

        return "\n\n".join(parts)

    async def _resolve_time_range(
        self,
        text: str,
        llm_context: dict[str, str] | None = None,
    ) -> dict[str, str] | None:
        """解析时间范围"""
        # 优先使用规则解析
        parsed = parse_time_range(text)
        if parsed:
            result = {"date_from": parsed.date_from, "date_to": parsed.date_to}
            if parsed.time_from:
                result["time_from"] = parsed.time_from
            if parsed.time_to:
                result["time_to"] = parsed.time_to
            return result
        
        # 检查是否有时间相关词
        if not self._has_time_hint(text):
            return None
        
        # 尝试 LLM 解析
        try:
            system_context = self._format_llm_context(llm_context)
            content = await self._llm.parse_time_range(
                text,
                system_context=system_context,
                timeout=self._llm_timeout,
            )
            if "date_from" in content and "date_to" in content:
                return {"date_from": content["date_from"], "date_to": content["date_to"]}
        except Exception:
            return None
        
        return None

    def _has_time_hint(self, text: str) -> bool:
        """检查是否包含时间相关词"""
        keywords = [
            "今天", "明天", "后天", "本周", "这周", "下周", "本月", "这个月",
            "上午", "下午", "中午", "晚上", "今早", "明早", "今晚", "明晚", "凌晨", "傍晚",
        ]
        if any(keyword in text for keyword in keywords):
            return True
        return bool(
            re.search(
                r"\d{1,2}月\d{1,2}[日号]?|\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}|\d{1,2}[-/\.]\d{1,2}|\d{1,2}[:：]\d{1,2}|\d{1,2}点(?:\d{1,2}分?|半)?",
                text,
            )
        )

    def _pick_casual_response(self) -> str:
        if self._casual_responses:
            return random.choice(self._casual_responses)
        return "我先聚焦案件相关事项，您可以直接告诉我需要查询什么。"

    def reload_config(self, config_path: str = "config/skills.yaml") -> None:
        """
        热更新配置

        参数:
            config_path: 配置文件路径
        """
        logger.info(
            "开始热重载技能配置: %s",
            config_path,
            extra={"event_code": "orchestrator.config.reload_start"},
        )
        self._skills_config = self._load_skills_config(config_path)
        self._table_identity_fields = self._load_table_identity_fields(self._skills_config)
        self._assistant_name = _resolve_assistant_name(self._skills_config)
        self._chitchat_allow_llm = _resolve_chitchat_allow_llm(self._skills_config)
        self._casual_responses = _load_casual_responses()
        self._response_renderer = ResponseRenderer(
            assistant_name=self._assistant_name,
            query_card_v2_enabled=bool(getattr(self._settings.reply, "query_card_v2_enabled", False)),
        )
        
        # 重新初始化解析器和路由器
        self._intent_parser = IntentParser(
            skills_config=self._skills_config,
            llm_client=self._llm,
        )
        
        max_hops = self._skills_config.get("chain", {}).get(
            "max_hops",
            self._skills_config.get("routing", {}).get("max_hops", 2),
        )
        self._router = SkillRouter(
            skills_config=self._skills_config,
            max_hops=max_hops,
            llm_client=getattr(self, "_task_llm", self._llm),
        )

        self._llm_timeout = float(self._skills_config.get("intent", {}).get("llm_timeout", 10))

        # 重新加载 L0 规则
        l0_rules = self._load_l0_rules(config_path)
        self._l0_engine = L0RuleEngine(
            state_manager=self._state_manager,
            l0_rules=l0_rules,
            skills_config=self._skills_config,
        )
        
        # 重新注册技能
        self._register_skills()
        logger.info(
            "技能配置热重载完成",
            extra={"event_code": "orchestrator.config.reload_success"},
        )

    def reload_skill_metadata(self) -> dict[str, Any]:
        """手动重载 SKILL.md 元数据缓存。"""
        report = self._router.reload_skill_metadata()
        failed_items = [
            {
                "skill_name": item.skill_name,
                "file_path": item.file_path,
                "error": item.error,
            }
            for item in report.failed
        ]
        result = {
            "loaded": list(report.loaded),
            "failed": failed_items,
            "loaded_count": len(report.loaded),
            "failed_count": len(report.failed),
        }
        logger.info(
            "技能元数据重载完成",
            extra={
                "event_code": "orchestrator.skill_metadata.reload",
                "loaded_count": result["loaded_count"],
                "failed_count": result["failed_count"],
            },
        )
        return result
# endregion


# region 向后兼容：AgentCore 别名
# 保持向后兼容，允许现有代码继续使用 AgentCore
AgentCore = AgentOrchestrator
# endregion
