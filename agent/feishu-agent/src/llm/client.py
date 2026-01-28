"""
LLM client wrapper.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from openai import AsyncOpenAI, BadRequestError
import httpx
from openai.types.chat import ChatCompletionMessageParam

from src.config import LLMSettings


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

    async def chat(self, messages: list[dict[str, str]]) -> str:
        logger = self._logger
        try:
            response = await self._client.chat.completions.create(
                model=self._settings.model,
                messages=cast(list[ChatCompletionMessageParam], messages),
                temperature=self._settings.temperature,
                max_tokens=self._settings.max_tokens,
            )
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

    async def parse_time_range(self, text: str) -> dict[str, Any]:
        if not self._settings.api_key:
            return {}
        prompt = (
            "将用户的时间表达解析为 JSON: {\"date_from\": \"YYYY-MM-DD\", "
            "\"date_to\": \"YYYY-MM-DD\"}. 只返回 JSON。\n\n"
            f"用户问题: {text}"
        )
        try:
            content = await self.chat([
                {"role": "system", "content": "你是时间解析助手。"},
                {"role": "user", "content": prompt},
            ])
            return json.loads(content)
        except Exception:
            return {}
