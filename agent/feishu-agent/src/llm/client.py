"""
描述: LLM 客户端 wrapper
主要功能:
    - 统一封装 OpenAI 兼容接口
    - 处理请求日志记录与异常重试
    - 提供 JSON 解析和辅助工具方法
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, cast

from openai import AsyncOpenAI, BadRequestError
import httpx
from openai.types.chat import ChatCompletionMessageParam

from src.config import LLMSettings
from src.utils.exceptions import LLMTimeoutError
from src.utils.metrics import record_llm_call


# region LLM 客户端
class LLMClient:
    """
    LLM 客户端封装

    功能:
        - 异步调用 OpenAI 兼容模型
        - 自动处理超时与错误映射
        - 记录性能与状态指标
    """
    def __init__(self, settings: LLMSettings) -> None:
        """
        初始化客户端

        参数:
            settings: LLM 配置对象
        """
        self._settings = settings
        self._logger = logging.getLogger(__name__)
        self._http_client = httpx.AsyncClient(event_hooks={"response": [self._log_response]})
        self._client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.api_base or None,
            http_client=self._http_client,
        )

    async def _log_response(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            self._logger.error("LLM http %s response: %s", response.status_code, response.text)

    async def chat(self, messages: list[dict[str, str]], timeout: float | None = None) -> str:
        """
        执行对话请求

        参数:
            messages: 消息列表
            timeout: 超时时间 (秒)

        返回:
            模型回复文本
        """
        logger = self._logger
        timeout_seconds = timeout if timeout is not None else self._settings.timeout
        start = time.perf_counter()
        status = "success"
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._settings.model,
                    messages=cast(list[ChatCompletionMessageParam], messages),
                    temperature=self._settings.temperature,
                    max_tokens=self._settings.max_tokens,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            status = "timeout"
            logger.warning("LLM request timeout after %ss", timeout_seconds)
            raise LLMTimeoutError(timeout_seconds) from exc
        except BadRequestError as exc:
            status = "error"
            if exc.response is not None:
                logger.error("LLM 400 response: %s", exc.response.text)
            logger.error("LLM request failed: %s", exc)
            raise
        except Exception as exc:
            status = "error"
            response = getattr(exc, "response", None)
            if response is not None:
                logger.error("LLM error response: %s", response.text)
            logger.error("LLM request failed: %s", exc)
            raise
        finally:
            duration = time.perf_counter() - start
            record_llm_call("chat", status, duration)
        return response.choices[0].message.content or ""

    async def chat_json(
        self,
        prompt_or_messages: Any,
        system: str | None = None,
        timeout: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        执行对话并解析 JSON 结果

        参数:
            prompt_or_messages: 提示词或消息列表
            system: 系统提示词 (可选)
            timeout: 超时时间 (可选)
            context: 上下文 (已弃用)

        返回:
            解析后的字典对象
        """
        del context
        if isinstance(prompt_or_messages, list):
            messages = prompt_or_messages
        else:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": str(prompt_or_messages)})

        content = await self.chat(messages, timeout=timeout)
        return self._safe_json_loads(content)

    def _safe_json_loads(self, content: str) -> dict[str, Any]:
        """解析 JSON 内容 (自动处理 Markdown 代码块)"""
        if not content:
            return {}

        text = content.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    return {}
        except Exception as exc:
            self._logger.error("chat_json error: %s", exc)
        return {}

    async def parse_time_range(
        self,
        text: str,
        system_context: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        解析时间范围 (辅助工具)

        参数:
            text: 自然语言时间描述
            system_context: 系统上下文补充

        返回:
            包含 date_from / date_to 的字典
        """
        if not self._settings.api_key:
            return {}
        system_prompt = "你是时间解析助手。"
        if system_context:
            system_prompt = f"{system_context.strip()}\n\n{system_prompt}"
        prompt = (
            "将用户的时间表达解析为 JSON: {\"date_from\": \"YYYY-MM-DD\", "
            "\"date_to\": \"YYYY-MM-DD\"}. 只返回 JSON。\n\n"
            f"用户问题: {text}"
        )
        try:
            content = await self.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ], timeout=timeout)
            return json.loads(content)
        except Exception:
            return {}
