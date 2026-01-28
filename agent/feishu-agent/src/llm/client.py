"""
LLM client wrapper.
"""

from __future__ import annotations

import json
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from src.config import LLMSettings


class LLMClient:
    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.api_base or None,
        )

    async def chat(self, messages: list[dict[str, str]]) -> str:
        response = await self._client.chat.completions.create(
            model=self._settings.model,
            messages=cast(list[ChatCompletionMessageParam], messages),
            temperature=self._settings.temperature,
            max_tokens=self._settings.max_tokens,
        )
        return response.choices[0].message.content or ""

    async def parse_time_range(self, text: str) -> dict[str, Any]:
        if not self._settings.api_key:
            return {}
        prompt = (
            "将用户的时间表达解析为 JSON: {\"date_from\": \"YYYY-MM-DD\", "
            "\"date_to\": \"YYYY-MM-DD\"}. 只返回 JSON。\n\n"
            f"用户问题: {text}"
        )
        content = await self.chat([
            {"role": "system", "content": "你是时间解析助手。"},
            {"role": "user", "content": prompt},
        ])
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}
