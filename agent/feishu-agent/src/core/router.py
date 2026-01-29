"""
Skill router with chain execution support.
Routes intent to skills and manages execution context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from src.core.intent import IntentResult, SkillMatch

logger = logging.getLogger(__name__)


# ============================================
# region 执行上下文
# ============================================
@dataclass
class SkillContext:
    """
    技能执行上下文，用于链式调用间传递数据
    
    Attributes:
        query: 原始用户输入
        last_result: 上一个技能的执行结果
        last_skill: 上一个执行的技能名称
        hop_count: 当前链式跳数
        user_id: 用户 ID
        extra: 额外数据（如时间范围、过滤条件等）
    """
    query: str
    user_id: str = ""
    last_result: dict[str, Any] | None = None
    last_skill: str | None = None
    hop_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def with_result(self, skill_name: str, result: dict[str, Any]) -> "SkillContext":
        """创建新上下文，携带上一个技能的结果"""
        return SkillContext(
            query=self.query,
            user_id=self.user_id,
            last_result=result,
            last_skill=skill_name,
            hop_count=self.hop_count + 1,
            extra=self.extra.copy(),
        )
# endregion
# ============================================


# ============================================
# region 技能执行结果
# ============================================
@dataclass
class SkillResult:
    """技能执行结果"""
    success: bool
    skill_name: str
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    reply_type: str = "text"  # text / card
    reply_text: str = ""
    reply_card: dict[str, Any] | None = None

    def to_reply(self) -> dict[str, Any]:
        """转换为回复格式"""
        result = {
            "type": self.reply_type,
            "text": self.reply_text or self.message,
        }
        if self.reply_card:
            result["card"] = self.reply_card
        return result
# endregion
# ============================================


# ============================================
# region 技能基类
# ============================================
class BaseSkill:
    """
    技能基类，所有技能需继承此类
    
    子类需实现：
    - name: 技能名称
    - execute(): 执行逻辑
    """
    name: str = "BaseSkill"
    description: str = ""

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        执行技能
        
        Args:
            context: 执行上下文
            
        Returns:
            SkillResult: 执行结果
        """
        raise NotImplementedError("Subclass must implement execute()")

    async def can_handle(self, context: SkillContext) -> bool:
        """
        检查是否能处理当前上下文（可选重写）
        
        Returns:
            True 表示可以处理
        """
        return True
# endregion
# ============================================


# ============================================
# region SkillRouter 核心类
# ============================================
class SkillRouter:
    """
    技能路由器
    
    职责：
    1. 注册技能
    2. 根据 IntentResult 选择技能
    3. 执行技能（支持链式）
    4. 管理执行上下文
    """

    def __init__(
        self,
        skills_config: dict[str, Any],
        max_hops: int = 2,
    ) -> None:
        """
        Args:
            skills_config: skills.yaml 配置
            max_hops: 链式调用最大跳数
        """
        self._config = skills_config
        self._max_hops = max_hops
        self._skills: dict[str, BaseSkill] = {}
        self._chains = skills_config.get("chains", {})

    def register(self, skill: BaseSkill) -> None:
        """注册技能"""
        self._skills[skill.name] = skill
        logger.debug(f"Registered skill: {skill.name}")

    def register_all(self, skills: list[BaseSkill]) -> None:
        """批量注册技能"""
        for skill in skills:
            self.register(skill)

    def get_skill(self, name: str) -> BaseSkill | None:
        """获取技能实例"""
        return self._skills.get(name)

    def list_skills(self) -> list[str]:
        """列出所有已注册技能"""
        return list(self._skills.keys())

    async def route(
        self,
        intent: IntentResult,
        context: SkillContext,
    ) -> SkillResult:
        """
        根据意图路由并执行技能
        
        Args:
            intent: 意图识别结果
            context: 执行上下文
            
        Returns:
            SkillResult: 最终执行结果
        """
        top_skill = intent.top_skill()
        if not top_skill:
            return self._fallback_result("无法识别意图")

        # 检查是否需要链式执行
        if intent.is_chain:
            chain_sequence = self._resolve_chain(context.query)
            if chain_sequence:
                return await self._execute_chain(chain_sequence, context)

        # 单技能执行
        return await self._execute_skill(top_skill.name, context)

    async def _execute_skill(
        self,
        skill_name: str,
        context: SkillContext,
    ) -> SkillResult:
        """执行单个技能"""
        skill = self._skills.get(skill_name)
        if not skill:
            logger.warning(f"Skill not found: {skill_name}")
            return self._fallback_result(f"技能 {skill_name} 未注册")

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
            logger.info(
                "Skill executed",
                extra={
                    "skill": skill_name,
                    "success": result.success,
                },
            )
            return result
        except Exception as e:
            logger.error(f"Skill execution error: {skill_name} - {e}")
            return SkillResult(
                success=False,
                skill_name=skill_name,
                message=f"技能执行出错：{str(e)}",
                reply_text="抱歉，处理请求时遇到问题，请稍后重试。",
            )

    async def _execute_chain(
        self,
        sequence: list[str],
        context: SkillContext,
    ) -> SkillResult:
        """
        执行链式技能
        
        Args:
            sequence: 技能执行序列，如 ["query", "summary"]
            context: 初始上下文
            
        Returns:
            SkillResult: 链式执行的最终结果
        """
        current_context = context
        last_result: SkillResult | None = None

        for i, skill_key in enumerate(sequence):
            if current_context.hop_count >= self._max_hops:
                logger.warning(f"Chain execution hit max_hops: {self._max_hops}")
                break

            # 获取技能名称（从配置）
            skill_cfg = self._config.get("skills", {}).get(skill_key, {})
            skill_name = skill_cfg.get("name", skill_key)

            # 执行技能
            result = await self._execute_skill(skill_name, current_context)
            last_result = result

            if not result.success:
                logger.warning(f"Chain broken at {skill_name}: {result.message}")
                break

            # 更新上下文，传递结果给下一个技能
            current_context = current_context.with_result(skill_name, result.data)

        return last_result or self._fallback_result("链式执行失败")

    def _resolve_chain(self, query: str) -> list[str] | None:
        """
        解析链式执行序列
        
        Args:
            query: 用户输入
            
        Returns:
            技能 key 列表，如 ["query", "summary"]
        """
        for chain_key, chain_cfg in self._chains.items():
            triggers = chain_cfg.get("trigger_keywords", [])
            for trigger in triggers:
                if trigger in query:
                    return chain_cfg.get("sequence", [])
        return None

    def _fallback_result(self, message: str) -> SkillResult:
        """生成兜底结果"""
        return SkillResult(
            success=False,
            skill_name="fallback",
            message=message,
            reply_text="抱歉，我暂时无法处理您的请求。试试问我"本周有什么庭"吧！",
        )
# endregion
# ============================================


# ============================================
# region ContextManager（全局上下文管理）
# ============================================
class ContextManager:
    """
    全局上下文管理器
    
    维护每个用户的最近会话上下文，供链式调用使用
    """

    def __init__(self, ttl_minutes: int = 30) -> None:
        self._contexts: dict[str, SkillContext] = {}
        self._ttl_minutes = ttl_minutes

    def get(self, user_id: str) -> SkillContext | None:
        """获取用户上下文"""
        return self._contexts.get(user_id)

    def set(self, user_id: str, context: SkillContext) -> None:
        """设置用户上下文"""
        self._contexts[user_id] = context

    def update_result(
        self,
        user_id: str,
        skill_name: str,
        result: dict[str, Any],
    ) -> None:
        """更新用户最近执行结果"""
        ctx = self._contexts.get(user_id)
        if ctx:
            ctx.last_skill = skill_name
            ctx.last_result = result

    def clear(self, user_id: str) -> None:
        """清除用户上下文"""
        self._contexts.pop(user_id, None)

    def cleanup_expired(self) -> None:
        """清理过期上下文（暂未实现时间检查，留作扩展）"""
        pass
# endregion
# ============================================
