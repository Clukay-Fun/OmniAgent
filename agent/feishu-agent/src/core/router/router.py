"""
Skill router with chain execution support.
Routes intent to skills and manages execution context.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from src.core.intent import IntentResult, SkillMatch
from src.core.types import SkillContext, SkillResult

if TYPE_CHECKING:
    from src.core.skills.base import BaseSkill

logger = logging.getLogger(__name__)


_SKILL_NAME_MAP: dict[str, str] = {
    "query": "QuerySkill",
    "summary": "SummarySkill",
    "reminder": "ReminderSkill",
    "chitchat": "ChitchatSkill",
}


# ============================================
# region SkillRouter 核心类
# ============================================
class SkillRouter:
    """
    技能路由器
    """

    def __init__(
        self,
        skills_config: dict[str, Any],
        max_hops: int = 2,
    ) -> None:
        self._config = skills_config
        self._max_hops = max_hops
        self._skills: dict[str, BaseSkill] = {}
        self._chains = skills_config.get("chains", {})
        self._chain_config = skills_config.get("chain", {})

    def register(self, skill: BaseSkill) -> None:
        self._skills[skill.name] = skill
        logger.debug(f"Registered skill: {skill.name}")

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
        top_skill = intent.top_skill()
        if not top_skill:
            return self._fallback_result("无法识别意图")

        if intent.is_chain:
            chain_sequence = self._resolve_chain(context.query)
            if chain_sequence:
                return await self._execute_chain(chain_sequence, context)

        return await self._execute_skill(top_skill.name, context)

    async def _execute_skill(
        self,
        skill_name: str,
        context: SkillContext,
    ) -> SkillResult:
        import time
        from src.utils.metrics import record_skill_execution
        from src.utils.exceptions import (
            LLMTimeoutError,
            MCPTimeoutError,
            get_user_message,
        )

        skill = self._skills.get(skill_name)
        if not skill:
            logger.warning(f"Skill not found: {skill_name}")
            record_skill_execution(skill_name, "not_found", 0)
            return self._fallback_result(f"技能 {skill_name} 未注册")

        start_time = time.perf_counter()
        status = "success"

        try:
            logger.info(
                "Executing skill",
                extra={
                    "skill": skill_name,
                    "query": context.query,
                    "hop": context.hop_count,
                },
            )
            result = await skill.execute(context)

            if not result.success:
                status = "failure"

            logger.info(
                "Skill executed",
                extra={
                    "skill": skill_name,
                    "success": result.success,
                    "duration_ms": round((time.perf_counter() - start_time) * 1000, 2),
                },
            )
            return result
        except (LLMTimeoutError, MCPTimeoutError) as e:
            status = "timeout"
            logger.warning(f"Skill timeout: {skill_name} - {e}")
            return SkillResult(
                success=False,
                skill_name=skill_name,
                message=str(e),
                reply_text=get_user_message(e),
            )
        except Exception as e:
            status = "error"
            logger.error(f"Skill execution error: {skill_name} - {e}", exc_info=True)
            return SkillResult(
                success=False,
                skill_name=skill_name,
                message=f"技能执行出错：{str(e)}",
                reply_text="抱歉，处理请求时遇到问题，请稍后重试。",
            )
        finally:
            duration = time.perf_counter() - start_time
            record_skill_execution(skill_name, status, duration)

    async def _execute_chain(
        self,
        sequence: list[str],
        context: SkillContext,
    ) -> SkillResult:
        current_context = context
        last_result: SkillResult | None = None

        for skill_key in sequence:
            if current_context.hop_count >= self._max_hops:
                logger.warning(f"Chain execution hit max_hops: {self._max_hops}")
                break

            skill_name = self._resolve_skill_name(skill_key)
            result = await self._execute_skill(skill_name, current_context)
            last_result = result

            if not result.success:
                logger.warning(f"Chain broken at {skill_name}: {result.message}")
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
            return skill_cfg.get("name", skill_key)
        return _SKILL_NAME_MAP.get(skill_key, skill_key)

    def _fallback_result(self, message: str) -> SkillResult:
        return SkillResult(
            success=False,
            skill_name="fallback",
            message=message,
            reply_text='抱歉，我暂时无法处理您的请求。试试问我"本周有什么庭"吧！',
        )


# endregion
# ============================================


# ============================================
# region ContextManager（全局上下文管理）
# ============================================
class ContextManager:
    """
    全局上下文管理器
    """

    def __init__(self, ttl_minutes: int = 30) -> None:
        self._contexts: dict[str, SkillContext] = {}
        self._timestamps: dict[str, float] = {}
        self._ttl_seconds = ttl_minutes * 60

    def get(self, user_id: str) -> SkillContext | None:
        ctx = self._contexts.get(user_id)
        if ctx:
            import time

            self._timestamps[user_id] = time.time()
        return ctx

    def set(self, user_id: str, context: SkillContext) -> None:
        import time

        self._contexts[user_id] = context
        self._timestamps[user_id] = time.time()

    def update_result(
        self,
        user_id: str,
        skill_name: str,
        result: dict[str, Any],
    ) -> None:
        import time

        ctx = self._contexts.get(user_id)
        if ctx:
            ctx.last_skill = skill_name
            ctx.last_result = result
            self._timestamps[user_id] = time.time()

    def clear(self, user_id: str) -> None:
        self._contexts.pop(user_id, None)
        self._timestamps.pop(user_id, None)

    def cleanup_expired(self) -> None:
        import time

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
            logger.debug(f"Cleaned up {len(expired_users)} expired contexts")

    def active_count(self) -> int:
        return len(self._contexts)


# endregion
# ============================================
