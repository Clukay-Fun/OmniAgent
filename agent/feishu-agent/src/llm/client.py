"""
LLM client wrapper.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, cast

from openai import AsyncOpenAI, BadRequestError
import httpx
from openai.types.chat import ChatCompletionMessageParam

from src.config import LLMSettings
from src.utils.exceptions import LLMTimeoutError


class LLMClient:
    def __init__(self, settings: LLMSettings) -> None:
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
        logger = self._logger
        timeout_seconds = timeout if timeout is not None else self._settings.timeout
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
            logger.warning("LLM request timeout after %ss", timeout_seconds)
            raise LLMTimeoutError(timeout_seconds) from exc
        except BadRequestError as exc:
            if exc.response is not None:
                logger.error("LLM 400 response: %s", exc.response.text)
            logger.error("LLM request failed: %s", exc)
            raise
        except Exception as exc:
            response = getattr(exc, "response", None)
            if response is not None:
                logger.error("LLM error response: %s", response.text)
            logger.error("LLM request failed: %s", exc)
            raise
        return response.choices[0].message.content or ""

    async def chat_json(
        self,
        prompt: str,
        system: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        content = await self.chat(messages, timeout=timeout)
        try:
            return json.loads(content)
        except Exception:
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(content[start : end + 1])
                except Exception:
                    return {}
        return {}

    async def parse_time_range(
        self,
        text: str,
        system_context: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
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
