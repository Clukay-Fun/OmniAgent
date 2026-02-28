"""
描述: 业务问答/知识库检索技能 (Knowledge QA Skill)
主要功能:
    - 拦截所有关于业务知识、怎么做、是什么等带有咨询性质的查询
    - 作为闲聊与核心业务操作之间的重度兜底层
    - 预留 MCP 知识库或外挂搜索的调用接口，默认通过内置 LLM 进行业务规范答疑
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.capabilities.skills.base.base import BaseSkill
from src.core.foundation.common.types import SkillContext, SkillResult
from src.infra.mcp.client import MCPClient
from src.infra.llm.client import LLMClient

logger = logging.getLogger(__name__)


class KnowledgeQASkill(BaseSkill):
    """
    业务知识问答技能

    策略:
        1. 接收用户的业务知识询问 (非操作指令)
        2. 如果配置了知识库或 Web 搜索 MCP 工具，则调用后总结
        3. 如果未配置外部强化工具，则直接调用大模型，并在 prompt 中注入当前环境信息
    """

    name: str = "KnowledgeQASkill"
    description: str = "处理业务领域相关的知识问答、规范咨询等。如果是和业务完全无关的话题请走闲聊。"

    def __init__(
        self,
        llm_client: LLMClient | Any | None = None,
        mcp_client: MCPClient | None = None,
        skills_config: dict[str, Any] | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._mcp_client = mcp_client
        self._config = skills_config or {}

    async def execute(self, context: SkillContext) -> SkillResult:
        query = context.query.strip()
        
        if not self._llm_client:
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"type": "no_llm"},
                message="无可用 LLM",
                reply_text="抱歉，目前缺乏解答复杂业务问题的大脑（未配置主要模型），请稍后再试。",
            )

        # 这里的实现逻辑是典型的“直接走 LLM 问答兜底”。
        # 如果将来想要扩展接入 RAG / 飞书知识库，则在这里先调用 self._mcp_client 查资料。
        try:
            soul_prompt = ""
            if isinstance(context.extra, dict):
                soul_prompt = context.extra.get("soul_prompt", "")

            system_prompt = (
                "你是一个专业的业务知识问答助手。\n"
                "用户正在进行一项业务相关的咨询（如流程、规定、特定指标含义等）。\n"
                "解答要点：\n"
                "1. 尽可能使用专业的术语，并保持态度客观严谨。\n"
                "2. 避免给出绝对性的或不负责任的承诺。\n"
                "3. 如果问题超出了通用常识，说明需要查阅最新文档，或根据你的内部知识给出保守的建议。\n"
            )

            if soul_prompt:
                system_prompt = f"{soul_prompt.strip()}\n\n{system_prompt}"

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ]

            response = await self._llm_client.chat(messages)
            reply_text = response if isinstance(response, str) else response.get("content", "")

            if not reply_text:
                reply_text = "这个问题有些难，我暂时无法给出明确的业务回答。"

            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"type": "business_qa"},
                message="业务知识问答完成",
                reply_text=reply_text,
            )

        except Exception as e:
            logger.error(f"Knowledge QA execution error: {e}", exc_info=True)
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"type": "error", "error": str(e)},
                message="执行查询时发生错误",
                reply_text="抱歉，解答此业务问题时遭遇了技术小故障，请稍后重试。",
            )
