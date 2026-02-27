"""
描述: 基于LLM的技能选择器模块。
主要功能:
    - 根据SKILL.md元数据构建受限路由提示，并请求LLM选择一个技能。
    - 设计上具有容错性：超时、解析错误、未知技能名称或低置信度输出均返回None。
"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import logging
import time
from typing import Any

from src.core.skills.metadata import SkillMetadataLoader
from src.utils.exceptions import LLMTimeoutError


logger = logging.getLogger(__name__)

# region 数据类定义
@dataclass
class LLMSelectionResult:
    """
    LLM选择结果的数据类。

    功能:
        - 存储选择的技能名称、置信度、推理理由和延迟时间。
    """
    skill_name: str
    confidence: float
    reasoning: str
    latency_ms: float
# endregion

# region 类定义
class LLMSkillSelector:
    """
    基于LLM的技能选择器。

    功能:
        - 根据技能元数据选择一个技能名称。
    """

    def __init__(
        self,
        llm_client: Any,
        metadata_loader: SkillMetadataLoader,
        timeout_seconds: float = 5.0,
        confidence_threshold: float = 0.6,
    ) -> None:
        """
        初始化LLMSkillSelector。

        功能:
            - 初始化LLM客户端、元数据加载器、超时时间和置信度阈值。
        """
        self._llm = llm_client
        self._metadata = metadata_loader
        self._timeout_seconds = max(0.1, float(timeout_seconds))
        self._confidence_threshold = max(0.0, min(float(confidence_threshold), 1.0))

    async def select(
        self,
        user_message: str,
        context: Any | None = None,
    ) -> LLMSelectionResult | None:
        """
        根据用户消息选择一个技能。

        功能:
            - 获取所有可用技能。
            - 构建选择提示。
            - 请求LLM并处理响应。
            - 解析响应并返回选择结果。
        """
        del context

        skills = self._metadata.get_all_for_routing()
        if not skills:
            return None

        prompt = self._build_selection_prompt(skills=skills, user_message=user_message)
        start_time = time.perf_counter()
        try:
            raw_response = await asyncio.wait_for(
                self._llm.chat_json(prompt, timeout=self._timeout_seconds),
                timeout=self._timeout_seconds,
            )
            response = self._normalize_response_payload(raw_response)
            if response is None:
                return None

            parsed = self._parse_response(response=response, available_skills=skills)
            if parsed is None:
                return None

            parsed.latency_ms = (time.perf_counter() - start_time) * 1000
            return parsed
        except (asyncio.TimeoutError, LLMTimeoutError):
            logger.warning(
                "LLM skill selection timed out",
                extra={"event_code": "router.llm_selection.timeout"},
            )
            return None

    @staticmethod
    def _normalize_response_payload(raw_response: Any) -> dict[str, Any] | None:
        """
        规范化LLM响应负载。

        功能:
            - 将响应转换为字典格式。
            - 处理JSON解析错误。
        """
        if isinstance(raw_response, dict):
            return raw_response

        if not isinstance(raw_response, str):
            return None

        text = raw_response.strip()
        if not text:
            return None

        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            return None
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, dict):
                    return parsed
                return None
            except json.JSONDecodeError:
                return None
        except Exception as exc:
            logger.warning(
                "LLM skill selection failed: %s",
                exc,
                extra={"event_code": "router.llm_selection.failed"},
            )
            return None

    @staticmethod
    def _build_selection_prompt(skills: list[dict[str, str]], user_message: str) -> str:
        """
        构建技能选择提示。

        功能:
            - 格式化技能描述。
            - 构建提示字符串。
        """
        skill_descriptions = "\n".join(
            f"- {item.get('name', '')}: {item.get('description', '')}（触发条件：{item.get('trigger_conditions', '')}）"
            for item in skills
        )
        return (
            "你是一个技能路由器。根据用户消息，从以下技能列表中选择最匹配的一个。\n\n"
            f"用户消息：{user_message}\n\n"
            f"可用技能：\n{skill_descriptions}\n\n"
            "请严格按以下 JSON 格式返回，不要包含任何其他内容：\n"
            '{"skill_name": "技能名称", "confidence": 0.0到1.0的置信度, "reasoning": "选择理由"}\n\n'
            "重要规则：\n"
            "- skill_name 必须是上面列表中的某一个，不能编造\n"
            "- 如果不确定，选择最接近的技能并给低置信度（如 0.3-0.5）\n"
            "- 完全无法匹配任何技能时，置信度设为 0.1"
        )

    def _parse_response(
        self,
        response: dict[str, Any],
        available_skills: list[dict[str, str]],
    ) -> LLMSelectionResult | None:
        """
        解析LLM响应。

        功能:
            - 提取技能名称、置信度和推理理由。
            - 验证技能名称的有效性。
            - 检查置信度是否达到阈值。
        """
        skill_name = str(response.get("skill_name") or "").strip()
        if not skill_name:
            return None

        confidence_raw = response.get("confidence", 0.0)
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            return None
        confidence = max(0.0, min(confidence, 1.0))

        valid_names = {
            str(item.get("name") or "").strip()
            for item in available_skills
            if str(item.get("name") or "").strip()
        }
        if skill_name not in valid_names:
            logger.warning(
                "LLM selected unknown skill: %s",
                skill_name,
                extra={"event_code": "router.llm_selection.unknown_skill"},
            )
            return None

        if confidence < self._confidence_threshold:
            logger.info(
                "LLM selection confidence too low: %s=%.2f",
                skill_name,
                confidence,
                extra={"event_code": "router.llm_selection.low_confidence"},
            )
            return None

        reasoning = str(response.get("reasoning") or "").strip()
        return LLMSelectionResult(
            skill_name=skill_name,
            confidence=confidence,
            reasoning=reasoning,
            latency_ms=0.0,
        )
# endregion
