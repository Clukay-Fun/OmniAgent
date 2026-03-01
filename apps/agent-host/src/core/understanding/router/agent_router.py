"""
描述: Agent 模式路由器 — 基于原生 Tool Calling 的技能路由
主要功能:
    - 将已注册技能转换为 OpenAI Tool Schema
    - 通过 LLM Tool Calling 实现零关键词意图识别
    - 替代传统的关键词权重匹配路由
设计参考:
    - OpenClaw 的 Native Tool Calling Agent Loop
    - Nanobot 的 provider.chat(messages, tools) 范式
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from src.core.understanding.intent import IntentResult, SkillMatch

if TYPE_CHECKING:
    from src.infra.llm.client import LLMClient
    from src.core.capabilities.skills.base.metadata import SkillMetadataLoader

logger = logging.getLogger(__name__)


# region Tool Schema 定义

# 每个技能对应一个 OpenAI Function Calling 工具定义
# name: 技能标识（供 LLM 选择）
# description: 让 LLM 理解何时应该调用该技能
SKILL_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_records",
            "description": (
                "查询多维表格中的记录。"
                "当用户想要搜索、查找、列出、统计案件或其他表格数据时调用。"
                "适用于：查案件、查合同、按条件筛选、按时间查询等所有读操作。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_summary": {
                        "type": "string",
                        "description": "用一句话概括用户的查询意图（如：查询委托人为XX的案件）",
                    },
                },
                "required": ["intent_summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_record",
            "description": (
                "创建新记录。"
                "当用户想要新增、创建、录入、登记一条新的案件、项目或其他记录时调用。"
                "用户通常会提供当事人、案由、案件类型等信息。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_summary": {
                        "type": "string",
                        "description": "用一句话概括要创建的内容",
                    },
                },
                "required": ["intent_summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_record",
            "description": (
                "更新已有记录的字段值。"
                "当用户想要修改、更新、变更现有记录的某些信息时调用。"
                "例如：改案件状态、更新联系方式、修改备注等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_summary": {
                        "type": "string",
                        "description": "用一句话概括要更新的内容",
                    },
                },
                "required": ["intent_summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_record",
            "description": (
                "删除记录。"
                "当用户明确要求删除、移除已有记录时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_summary": {
                        "type": "string",
                        "description": "用一句话概括要删除的内容",
                    },
                },
                "required": ["intent_summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": (
                "创建定时提醒。"
                "当用户想要设置提醒、闹钟或在将来某个时间点被通知时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_summary": {
                        "type": "string",
                        "description": "用一句话概括提醒内容和时间",
                    },
                },
                "required": ["intent_summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize",
            "description": (
                "生成摘要或总结。"
                "当用户要求总结、汇总、整理、归纳内容时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_summary": {
                        "type": "string",
                        "description": "用一句话概括用户想要总结的范围",
                    },
                },
                "required": ["intent_summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chitchat",
            "description": (
                "自由闲聊和帮助引导。"
                "当用户进行问候、感谢、闲聊或不属于上述任何业务意图时调用。"
                "也包括：你好、你是谁、你能做什么、帮助等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent_summary": {
                        "type": "string",
                        "description": "用一句话概括用户的对话意图",
                    },
                },
                "required": ["intent_summary"],
            },
        },
    },
]

# Tool name -> Skill name 映射
_TOOL_TO_SKILL: dict[str, str] = {
    "query_records": "QuerySkill",
    "create_record": "CreateSkill",
    "update_record": "UpdateSkill",
    "delete_record": "DeleteSkill",
    "create_reminder": "ReminderSkill",
    "summarize": "SummarySkill",
    "chitchat": "ChitchatSkill",
}

# endregion


# region AgentRouter

class AgentRouter:
    """
    Agent 模式路由器：通过 LLM 原生 Tool Calling 进行意图识别。

    核心思想（对标 Nanobot / OpenClaw）：
    - 不做任何关键词匹配
    - 将所有技能描述为标准化 Tool Schema
    - 让 LLM 通过 function calling 自主决定调用哪个技能
    - 如果 LLM 不调用任何工具，视为自由闲聊
    """

    def __init__(
        self,
        llm_client: LLMClient,
        timeout_seconds: float = 8.0,
        metadata_loader: SkillMetadataLoader | None = None,
    ) -> None:
        self._llm = llm_client
        self._timeout = timeout_seconds
        self._metadata_loader = metadata_loader
        self._tools = list(SKILL_TOOL_SCHEMAS)

    async def resolve_intent(
        self,
        query: str,
        conversation_history: list[dict[str, str]] | None = None,
        soul_prompt: str | None = None,
    ) -> IntentResult:
        """
        通过 Tool Calling 解析用户意图。

        参数:
            query: 用户输入
            conversation_history: 对话历史（可选）
            soul_prompt: 人设提示词（可选）

        返回:
            IntentResult，与现有路由层兼容
        """
        system_content = self._build_system_prompt(soul_prompt)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content},
        ]
        if conversation_history:
            messages.extend(conversation_history[-10:])
        messages.append({"role": "user", "content": query})

        try:
            result = await self._llm.chat_with_tools(
                messages=messages,
                tools=self._tools,
                timeout=self._timeout,
                tool_choice="required",
            )
        except Exception as exc:
            logger.warning(
                "Agent 路由 Tool Calling 失败，回退到闲聊: %s",
                exc,
                extra={"event_code": "agent_router.tool_calling.failed"},
            )
            return self._fallback_chitchat("tool_calling_error")

        tool_calls = result.get("tool_calls")
        if not tool_calls:
            content = result.get("content") or ""
            logger.info(
                "Agent 路由：LLM 未调用工具，视为闲聊",
                extra={
                    "event_code": "agent_router.no_tool_call",
                    "content_preview": content[:80],
                },
            )
            return self._fallback_chitchat("no_tool_call")

        # 取第一个 tool call 作为意图
        first_call = tool_calls[0]
        tool_name = first_call.get("name", "")
        tool_args = first_call.get("arguments", {})
        intent_summary = tool_args.get("intent_summary", "")

        skill_name = _TOOL_TO_SKILL.get(tool_name, "ChitchatSkill")

        logger.info(
            "Agent 路由完成",
            extra={
                "event_code": "agent_router.resolved",
                "tool_name": tool_name,
                "skill_name": skill_name,
                "intent_summary": intent_summary[:100],
                "query_preview": query[:80],
            },
        )

        return IntentResult(
            skills=[
                SkillMatch(
                    name=skill_name,
                    score=0.95,
                    reason=f"agent:{tool_name}",
                )
            ],
            is_chain=False,
            requires_llm_confirm=False,
            method="agent",
        )

    def _build_system_prompt(self, soul_prompt: str | None = None) -> str:
        parts = [
            "你是一个智能律师助理。根据用户的消息，判断用户想要执行什么操作，然后调用对应的工具。",
            "",
            "规则：",
            "1. 仔细理解用户的真实意图，不要被个别词语误导",
            "2. 如果用户想查信息，调用 query_records",
            "3. 如果用户想新建记录，调用 create_record",
            "4. 如果用户想修改记录，调用 update_record",
            "5. 如果用户想删除记录，调用 delete_record",
            "6. 如果用户想设置提醒，调用 create_reminder",
            "7. 如果用户想要总结内容，调用 summarize",
            "8. 其他情况（问候、闲聊、帮助等），调用 chitchat",
        ]
        if soul_prompt:
            parts.extend(["", soul_prompt])
        return "\n".join(parts)

    @staticmethod
    def _fallback_chitchat(reason: str) -> IntentResult:
        return IntentResult(
            skills=[
                SkillMatch(
                    name="ChitchatSkill",
                    score=0.5,
                    reason=f"agent_fallback:{reason}",
                )
            ],
            is_chain=False,
            requires_llm_confirm=False,
            method="agent",
        )


# endregion
