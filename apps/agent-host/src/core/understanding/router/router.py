"""
描述: 技能路由与链式执行管理器
主要功能:
    - 意图(Intent) -> 技能(Skill) 路由分发
    - 支持单一技能执行与链式(Chain)执行
    - 全局上下文生命周期管理 (ContextManager)
    - 运行时异常处理与降级策略
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Any

from src.core.foundation.common.errors import get_user_message_by_code
from src.core.understanding.intent import IntentResult, SkillMatch
from src.core.foundation.common.types import SkillContext, SkillExecutionStatus, SkillResult

if TYPE_CHECKING:
    from src.core.understanding.router.llm_selector import LLMSelectionResult, LLMSkillSelector
    from src.core.capabilities.skills.base.base import BaseSkill
    from src.core.capabilities.skills.base.metadata import ReloadReport, SkillMetadata, SkillMetadataLoader

logger = logging.getLogger(__name__)


# 统一的技能名映射表（支持别名 -> 标准名 双向映射）
_SKILL_NAME_MAP: dict[str, str] = {
    # 小写别名 -> 标准名
    "query": "QuerySkill",
    "create": "CreateSkill",
    "update": "UpdateSkill",
    "delete": "DeleteSkill",
    "summary": "SummarySkill",
    "reminder": "ReminderSkill",
    "chitchat": "ChitchatSkill",
    # 标准名 -> 标准名（方便统一查找）
    "QuerySkill": "QuerySkill",
    "CreateSkill": "CreateSkill",
    "UpdateSkill": "UpdateSkill",
    "DeleteSkill": "DeleteSkill",
    "SummarySkill": "SummarySkill",
    "ReminderSkill": "ReminderSkill",
    "ChitchatSkill": "ChitchatSkill",
}


# ============================================
# region SkillRouter 核心类
# ============================================
class SkillRouter:
    """
    技能路由器核心类
    
    功能:
        - 维护已注册技能表
        - 解析意图并调度技能执行
        - 处理技能链 (Chain) 逻辑
        - 统一异常捕获与指标记录
    """

    # 使用模块级统一映射表
    SKILL_NAME_MAP = _SKILL_NAME_MAP

    def __init__(
        self,
        skills_config: dict[str, Any],
        max_hops: int = 2,
        skills_metadata_dir: str | Path | None = None,
        llm_client: Any | None = None,
    ) -> None:
        self._config = skills_config
        self._max_hops = max_hops
        self._skills: dict[str, BaseSkill] = {}
        self._chains = skills_config.get("chains", {})
        self._chain_config = skills_config.get("chain", {})
        resolved_metadata_dir = (
            Path(skills_metadata_dir)
            if skills_metadata_dir is not None
            else self._resolve_skills_metadata_dir()
        )
        from src.core.capabilities.skills.base.metadata import SkillMetadataLoader

        self._skill_metadata_loader = SkillMetadataLoader(skills_dir=resolved_metadata_dir)
        self._skill_metadata_report = self._skill_metadata_loader.load_all()
        logger.info(
            "技能元数据已加载",
            extra={
                "event_code": "router.skill_metadata.loaded",
                "skills_dir": str(resolved_metadata_dir),
                "loaded": len(self._skill_metadata_report.loaded),
                "failed": len(self._skill_metadata_report.failed),
            },
        )

        routing_cfg = self._config.get("routing", {})
        if not isinstance(routing_cfg, dict):
            routing_cfg = {}
        self._routing_mode = str(routing_cfg.get("mode", "rule") or "rule").strip().lower()
        self._llm_selection_timeout = float(routing_cfg.get("llm_selection_timeout", 5.0))
        self._llm_confidence_threshold = float(routing_cfg.get("llm_confidence_threshold", 0.6))
        self._shadow_max_pending = max(1, int(routing_cfg.get("shadow_max_pending", 10)))
        self._shadow_tasks: set[asyncio.Task[Any]] = set()
        self._llm_selector: LLMSkillSelector | None = None
        if llm_client is not None:
            from src.core.understanding.router.llm_selector import LLMSkillSelector

            self._llm_selector = LLMSkillSelector(
                llm_client=llm_client,
                metadata_loader=self._skill_metadata_loader,
                timeout_seconds=self._llm_selection_timeout,
                confidence_threshold=self._llm_confidence_threshold,
            )

        # Agent 模式路由器（基于 Tool Calling）
        self._agent_router: AgentRouter | None = None
        if self._routing_mode == "agent" and llm_client is not None:
            from src.core.understanding.router.agent_router import AgentRouter

            agent_timeout = float(routing_cfg.get("agent_timeout", 8.0))
            self._agent_router = AgentRouter(
                llm_client=llm_client,
                timeout_seconds=agent_timeout,
                metadata_loader=self._skill_metadata_loader,
            )

        logger.info(
            "技能路由模式已初始化",
            extra={
                "event_code": "router.mode.initialized",
                "mode": self._routing_mode,
                "llm_selector_enabled": bool(self._llm_selector),
                "agent_router_enabled": bool(self._agent_router),
                "shadow_max_pending": self._shadow_max_pending,
            },
        )

    @staticmethod
    def _resolve_skills_metadata_dir() -> Path:
        current_candidate = Path("config/skills")
        if current_candidate.exists():
            return current_candidate

        app_root = Path(__file__).resolve().parents[4]
        app_candidate = app_root / "config" / "skills"
        if app_candidate.exists():
            return app_candidate
        return current_candidate

    def _metadata_name_candidates(self, skill_name: str) -> list[str]:
        raw_name = str(skill_name or "").strip()
        if not raw_name:
            return []

        candidates: list[str] = [raw_name]
        normalized_name = self._normalize_skill_name(raw_name)
        if normalized_name not in candidates:
            candidates.append(normalized_name)

        for name in list(candidates):
            if name.endswith("Skill") and len(name) > len("Skill"):
                short_name = name[: -len("Skill")].lower()
                if short_name not in candidates:
                    candidates.append(short_name)
            lower_name = name.lower()
            if lower_name not in candidates:
                candidates.append(lower_name)

        return candidates

    def get_skill_metadata(self, skill_name: str) -> SkillMetadata | None:
        for candidate in self._metadata_name_candidates(skill_name):
            metadata = self._skill_metadata_loader.get_metadata(candidate)
            if metadata:
                return metadata
        return None

    def get_all_skill_metadata_for_routing(self) -> list[dict[str, str]]:
        return self._skill_metadata_loader.get_all_for_routing()

    def reload_skill_metadata(self) -> ReloadReport:
        self._skill_metadata_report = self._skill_metadata_loader.reload()
        logger.info(
            "技能元数据重载完成",
            extra={
                "event_code": "router.skill_metadata.reloaded",
                "loaded": len(self._skill_metadata_report.loaded),
                "failed": len(self._skill_metadata_report.failed),
            },
        )
        return self._skill_metadata_report

    @property
    def last_skill_metadata_report(self) -> ReloadReport:
        from src.core.capabilities.skills.base.metadata import ReloadReport

        return ReloadReport(
            loaded=list(self._skill_metadata_report.loaded),
            failed=list(self._skill_metadata_report.failed),
        )

    def register(self, skill: BaseSkill) -> None:
        self._skills[skill.name] = skill
        logger.debug(
            "已注册技能: %s",
            skill.name,
            extra={"event_code": "router.skill.registered"},
        )

    def register_all(self, skills: list[BaseSkill]) -> None:
        for skill in skills:
            self.register(skill)

    def get_skill(self, name: str) -> BaseSkill | None:
        return self._skills.get(name)

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())

    async def route(
        self,
        intent: IntentResult,
        context: SkillContext,
    ) -> SkillResult:
        if self._routing_mode == "agent":
            return await self._agent_route(context)
        if self._routing_mode == "rule":
            return await self._rule_based_route(intent, context)
        if self._routing_mode == "shadow":
            return await self._shadow_route(intent, context)
        if self._routing_mode == "llm":
            return await self._llm_route_with_fallback(intent, context)

        logger.warning(
            "未知路由模式，回退到规则模式: %s",
            self._routing_mode,
            extra={"event_code": "router.mode.unknown_fallback_rule"},
        )
        return await self._rule_based_route(intent, context)

    async def _agent_route(self, context: SkillContext) -> SkillResult:
        """
        Agent 模式路由：通过 LLM Tool Calling 直接解析意图并执行技能。
        完全绕过关键词匹配，让 LLM 自主决定调用哪个技能。
        """
        if self._agent_router is None:
            logger.warning(
                "Agent 路由器未初始化，回退到 Chitchat",
                extra={"event_code": "router.agent.not_initialized"},
            )
            return await self._execute_skill("ChitchatSkill", context)

        try:
            agent_intent = await self._agent_router.resolve_intent(
                query=context.query,
            )
            top = agent_intent.top_skill()
            skill_name = top.name if top else "ChitchatSkill"
            return await self._execute_skill(skill_name, context)
        except Exception as exc:
            logger.error(
                "Agent 路由异常，回退到 Chitchat: %s",
                exc,
                extra={"event_code": "router.agent.error"},
            )
            return await self._execute_skill("ChitchatSkill", context)

    async def _rule_based_route(self, intent: IntentResult, context: SkillContext) -> SkillResult:
        top_skill = intent.top_skill()
        if not top_skill:
            return self._fallback_result("无法识别意图")

        if intent.is_chain:
            chain_sequence = self._resolve_chain(context.query)
            if chain_sequence:
                return await self._execute_chain(chain_sequence, context)

        return await self._execute_skill(top_skill.name, context)

    async def _llm_route_with_fallback(self, intent: IntentResult, context: SkillContext) -> SkillResult:
        if self._llm_selector is not None:
            selected = await self._llm_selector.select(context.query, context)
            if selected is not None:
                normalized_skill_name = self._normalize_skill_name(selected.skill_name)
                if normalized_skill_name in self._skills:
                    logger.info(
                        "LLM选路命中: %s",
                        normalized_skill_name,
                        extra={
                            "event_code": "router.llm.selected",
                            "skill": normalized_skill_name,
                            "confidence": selected.confidence,
                            "latency_ms": round(selected.latency_ms, 2),
                        },
                    )
                    return await self._execute_skill(normalized_skill_name, context)

                logger.warning(
                    "LLM选路结果未注册，回退规则: %s",
                    normalized_skill_name,
                    extra={"event_code": "router.llm.selected_unregistered"},
                )

        logger.info(
            "LLM选路失败，回退规则匹配",
            extra={"event_code": "router.llm.fallback_rule"},
        )
        return await self._rule_based_route(intent, context)

    async def _shadow_route(self, intent: IntentResult, context: SkillContext) -> SkillResult:
        rule_result = await self._rule_based_route(intent, context)
        if self._llm_selector is None:
            return rule_result

        if len(self._shadow_tasks) >= self._shadow_max_pending:
            logger.warning(
                "Shadow任务队列已满 (%s/%s)，跳过本次LLM对比",
                len(self._shadow_tasks),
                self._shadow_max_pending,
                extra={"event_code": "router.shadow.queue_full"},
            )
            return rule_result

        rule_skill_name = str(rule_result.skill_name or "")
        task = asyncio.create_task(
            self._shadow_llm_compare(
                user_message=context.query,
                context=context,
                rule_skill_name=rule_skill_name,
            )
        )
        self._shadow_tasks.add(task)
        task.add_done_callback(self._shadow_tasks.discard)
        return rule_result

    async def _shadow_llm_compare(
        self,
        user_message: str,
        context: SkillContext,
        rule_skill_name: str,
    ) -> None:
        if self._llm_selector is None:
            return

        try:
            llm_result = await self._llm_selector.select(user_message, context)
            self._log_shadow_comparison(user_message, rule_skill_name, llm_result)
        except Exception as exc:
            logger.warning(
                "Shadow LLM 对比异常: %s",
                exc,
                extra={"event_code": "router.shadow.error"},
            )

    def _log_shadow_comparison(
        self,
        user_message: str,
        rule_skill_name: str,
        llm_result: LLMSelectionResult | None,
    ) -> None:
        normalized_rule = self._normalize_skill_name(rule_skill_name)
        llm_skill = "NONE"
        llm_confidence = 0.0
        llm_latency_ms = 0.0

        if llm_result is not None:
            llm_skill = self._normalize_skill_name(llm_result.skill_name)
            llm_confidence = float(llm_result.confidence)
            llm_latency_ms = float(llm_result.latency_ms)

        is_match = normalized_rule == llm_skill
        logger.info(
            "Shadow 对比: rule=%s llm=%s match=%s",
            normalized_rule,
            llm_skill,
            is_match,
            extra={
                "event_code": "router.shadow.comparison",
                "rule_skill": normalized_rule,
                "llm_skill": llm_skill,
                "match": is_match,
                "confidence": llm_confidence,
                "latency_ms": round(llm_latency_ms, 2),
                "query_preview": user_message[:80],
            },
        )

    async def _execute_skill(
        self,
        skill_name: str,
        context: SkillContext,
    ) -> SkillResult:
        """
        执行单个技能 (带监控与错误处理)
        
        流程:
            1. 规范化技能名称
            2. 查找技能实例 (未找到则降级到 Chitchat)
            3. 执行并记录耗时/结果
            4. 处理超时与异常
        """
        from src.utils.observability.metrics import record_skill_execution
        from src.utils.errors.exceptions import (
            LLMTimeoutError,
            MCPTimeoutError,
            SkillTimeoutError,
            get_user_message,
        )

        normalized_name = self._normalize_skill_name(skill_name)
        skill = self._skills.get(normalized_name)
        if not skill and normalized_name != "ChitchatSkill":
            fallback_skill = self._skills.get("ChitchatSkill")
            if fallback_skill:
                logger.warning(
                    "技能不存在，降级到闲聊技能: %s",
                    normalized_name,
                    extra={"event_code": "router.skill.fallback_to_chitchat"},
                )
                normalized_name = "ChitchatSkill"
                skill = fallback_skill

        if not skill:
            logger.warning(
                "技能未注册: %s",
                normalized_name,
                extra={"event_code": "router.skill.not_found"},
            )
            record_skill_execution(normalized_name, "not_found", 0)
            return self._fallback_result(f"技能 {normalized_name} 未注册")

        global_default = float(self._config.get("defaults", {}).get("timeout_seconds", 10.0))
        skills_cfg = self._config.get("skills")
        skill_cfg: dict[str, Any] = {}
        if isinstance(skills_cfg, dict):
            resolved_cfg = skills_cfg.get(normalized_name)
            if isinstance(resolved_cfg, dict):
                skill_cfg = resolved_cfg
            else:
                alias_key = normalized_name.replace("Skill", "").lower()
                alias_cfg = skills_cfg.get(alias_key)
                if isinstance(alias_cfg, dict):
                    skill_cfg = alias_cfg
        timeout = float(skill_cfg.get("timeout_seconds", global_default))

        start_time = time.perf_counter()
        status = SkillExecutionStatus.SUCCESS

        try:
            logger.info(
                "开始执行技能",
                extra={
                    "event_code": "router.skill.start",
                    "skill": normalized_name,
                    "query": context.query,
                    "hop": context.hop_count,
                    "timeout_seconds": timeout,
                },
            )
            result = await asyncio.wait_for(skill.execute(context), timeout=timeout)

            if not result.success:
                status = SkillExecutionStatus.FAILED

            logger.info(
                "技能执行完成",
                extra={
                    "event_code": "router.skill.completed",
                    "skill": normalized_name,
                    "success": result.success,
                    "duration_ms": round((time.perf_counter() - start_time) * 1000, 2),
                    "close_semantic": str((result.data or {}).get("close_semantic") or ""),
                    "close_profile": str((result.data or {}).get("close_profile") or ""),
                },
            )
            return result
        except asyncio.TimeoutError:
            status = SkillExecutionStatus.TIMEOUT
            logger.warning(
                "技能执行超时: %s (>%ss)",
                normalized_name,
                timeout,
                extra={"event_code": "router.skill.timeout", "skill": normalized_name},
            )
            error = SkillTimeoutError(skill_name=normalized_name, timeout_seconds=timeout)
            reply_text = get_user_message(error) or "抱歉，操作响应超时，请稍后重试。"
            return SkillResult(
                success=False,
                skill_name=normalized_name,
                message=str(error),
                reply_text=reply_text,
            )
        except (LLMTimeoutError, MCPTimeoutError) as e:
            status = SkillExecutionStatus.TIMEOUT
            logger.warning(
                "技能执行超时: %s - %s",
                normalized_name,
                e,
                extra={"event_code": "router.skill.timeout", "skill": normalized_name},
            )
            return SkillResult(
                success=False,
                skill_name=normalized_name,
                message=str(e),
                reply_text=get_user_message(e),
            )
        except Exception as e:
            status = SkillExecutionStatus.ERROR
            logger.error(
                "技能执行异常: %s - %s",
                normalized_name,
                e,
                extra={"event_code": "router.skill.error", "skill": normalized_name},
                exc_info=True,
            )
            return SkillResult(
                success=False,
                skill_name=normalized_name,
                data={"error_code": "router_processing_failed"},
                message=f"技能执行出错：{str(e)}",
                reply_text=get_user_message_by_code("router_processing_failed"),
            )
        finally:
            duration = time.perf_counter() - start_time
            record_skill_execution(normalized_name, status.value, duration)

    async def _execute_chain(
        self,
        sequence: list[str],
        context: SkillContext,
    ) -> SkillResult:
        current_context = context
        last_result: SkillResult | None = None

        for skill_key in sequence:
            if current_context.hop_count >= self._max_hops:
                logger.warning(
                    "链式执行达到最大跳数: %s",
                    self._max_hops,
                    extra={"event_code": "router.chain.max_hops"},
                )
                break

            skill_name = self._resolve_skill_name(skill_key)
            result = await self._execute_skill(skill_name, current_context)
            last_result = result

            if not result.success:
                logger.warning(
                    "链式执行中断: %s - %s",
                    skill_name,
                    result.message,
                    extra={"event_code": "router.chain.broken", "skill": skill_name},
                )
                break

            current_context = current_context.with_result(skill_name, result.data)

        return last_result or self._fallback_result("链式执行失败")

    def _resolve_chain(self, query: str) -> list[str] | None:
        triggers = self._chain_config.get("triggers", [])
        for trigger in triggers:
            pattern = trigger.get("pattern")
            if pattern and re.search(pattern, query):
                skills = trigger.get("skills", [])
                return [self._resolve_skill_name(name) for name in skills]

        for chain_key, chain_cfg in self._chains.items():
            trigger_keywords = chain_cfg.get("trigger_keywords", [])
            for trigger in trigger_keywords:
                if trigger in query:
                    return chain_cfg.get("sequence", [])
        return None

    def _resolve_skill_name(self, skill_key: str) -> str:
        skills_cfg = self._config.get("skills", {})
        if skill_key in skills_cfg:
            skill_cfg = skills_cfg.get(skill_key, {})
            return self._normalize_skill_name(skill_cfg.get("name", skill_key))
        return self._normalize_skill_name(skill_key)

    def _normalize_skill_name(self, name: str) -> str:
        if name in self.SKILL_NAME_MAP:
            return self.SKILL_NAME_MAP[name]
        lower_name = name.lower()
        for key, value in self.SKILL_NAME_MAP.items():
            if key.lower() == lower_name:
                return value
        return _SKILL_NAME_MAP.get(name, name)

    def _fallback_result(self, message: str) -> SkillResult:
        return SkillResult(
            success=False,
            skill_name="fallback",
            data={"error_code": "router_fallback_unavailable"},
            message=message,
            reply_text=get_user_message_by_code("router_fallback_unavailable"),
        )


# endregion
# ============================================


# ============================================
# region ContextManager（全局上下文管理）
# ============================================
class ContextManager:
    """
    全局上下文管理器 (Memory Store)
    
    功能:
        - 存储用户会话上下文 (SkillContext)
        - 管理上下文生命周期 (TTL 过期清理)
        - 记录最后一次技能执行结果 (用于多轮对话)
    """

    def __init__(self, ttl_minutes: int = 30) -> None:
        self._contexts: dict[str, SkillContext] = {}
        self._timestamps: dict[str, float] = {}
        self._ttl_seconds = ttl_minutes * 60

    def get(self, user_id: str) -> SkillContext | None:
        ctx = self._contexts.get(user_id)
        if ctx:

            self._timestamps[user_id] = time.time()
        return ctx

    def set(self, user_id: str, context: SkillContext) -> None:
        self._contexts[user_id] = context
        self._timestamps[user_id] = time.time()

    def update_result(
        self,
        user_id: str,
        skill_name: str,
        result: dict[str, Any],
    ) -> None:

        ctx = self._contexts.get(user_id)
        if ctx:
            ctx.last_skill = skill_name
            ctx.last_result = result
            self._timestamps[user_id] = time.time()

    def clear(self, user_id: str) -> None:
        self._contexts.pop(user_id, None)
        self._timestamps.pop(user_id, None)

    def cleanup_expired(self) -> None:
        now = time.time()
        expired_users = [
            user_id
            for user_id, ts in self._timestamps.items()
            if now - ts > self._ttl_seconds
        ]
        for user_id in expired_users:
            self._contexts.pop(user_id, None)
            self._timestamps.pop(user_id, None)

        if expired_users:
            logger.debug(
                "已清理过期上下文数量: %s",
                len(expired_users),
                extra={"event_code": "router.context.cleanup"},
            )

    def active_count(self) -> int:
        return len(self._contexts)


# endregion
# ============================================
