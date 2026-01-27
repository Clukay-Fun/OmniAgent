"""
描述: 模型调用客户端
主要功能:
    - 通用聊天与嵌入接口
    - 重排序与结构化调用
依赖: 标准库
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelResponse:
    """
    模型响应
    """

    content: str | None
    raw: dict[str, Any]
    usage: dict[str, Any] | None


class ModelClient:
    """
    通用模型客户端
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 60) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    # ============================================
    # region _build_url
    # ============================================
    def _build_url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self._base_url}{path}"
    # endregion
    # ============================================

    # ============================================
    # region _post_json
    # ============================================
    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError("MODEL_API_KEY is required")

        url = self._build_url(path)
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                data = response.read().decode("utf-8")
            try:
                return json.loads(data) if data else {}
            except json.JSONDecodeError as exc:
                preview = data[:200]
                raise RuntimeError(f"Model response is not valid JSON: {preview}") from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8")
            raise RuntimeError(f"Model request failed: {exc.code} {detail}") from exc
        except TimeoutError as exc:
            raise RuntimeError("Model request timed out") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Model request failed: {exc.reason}") from exc
    # endregion
    # ============================================

    # ============================================
    # region chat_completion
    # ============================================
    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        raw = self._post_json("/chat/completions", payload)
        choices = raw.get("choices", [])
        content = None
        if choices:
            content = choices[0].get("message", {}).get("content")
        return ModelResponse(content=content, raw=raw, usage=raw.get("usage"))
    # endregion
    # ============================================

    # ============================================
    # region embeddings
    # ============================================
    def embeddings(self, model: str, inputs: list[str]) -> list[list[float]]:
        payload = {"model": model, "input": inputs}
        raw = self._post_json("/embeddings", payload)
        data = raw.get("data", [])
        return [item.get("embedding", []) for item in data]
    # endregion
    # ============================================

    # ============================================
    # region rerank
    # ============================================
    def rerank(
        self, model: str, query: str, documents: list[str], top_k: int = 5
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "query": query,
            "documents": documents,
            "top_k": top_k,
        }
        return self._post_json("/rerank", payload)
    # endregion
    # ============================================
