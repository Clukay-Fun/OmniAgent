"""
描述: 文本 Embedding 客户端
主要功能:
    - 文本向量化 (Text Embedding)
    - 支持 SiliconFlow API 调用
    - 自动批处理 (Batch Processing)
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# region Embedding 客户端
class EmbeddingClient:
    """
    Embedding 客户端

    功能:
        - 封装第三方 Embedding API
        - 提供文本到向量的转换能力
    """
    def __init__(self, config: dict[str, Any]) -> None:
        """
        初始化客户端

        参数:
            config: 配置字典 (包含 provider, api_key, model 等)
        """
        self._provider = config.get("provider", "")
        self._api_base = config.get("api_base", "")
        self._api_key = config.get("api_key", "")
        self._model = config.get("model", "")
        self._timeout = float(config.get("timeout", 10))
        self._batch_size = max(int(config.get("batch_size", 32)), 1)

    @property
    def batch_size(self) -> int:
        """获取批处理大小"""
        return self._batch_size

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        批量生成文本向量

        参数:
            texts: 文本列表

        返回:
            向量列表 (与输入文本一一对应)
        """
        if not texts:
            return []
        if self._provider != "siliconflow":
            raise ValueError("Unsupported embedding provider")
        if not self._api_key or not self._api_base:
            raise ValueError("Embedding API config missing")

        url = f"{self._api_base.rstrip('/')}/embeddings"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        embeddings: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for start in range(0, len(texts), self._batch_size):
                batch = texts[start:start + self._batch_size]
                payload = {"model": self._model, "input": batch}
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()

                items = data.get("data") or []
                if items and isinstance(items[0], dict) and "index" in items[0]:
                    items = sorted(items, key=lambda item: item.get("index", 0))
                batch_embeddings = [item.get("embedding") for item in items]
                if len(batch_embeddings) != len(batch) or any(e is None for e in batch_embeddings):
                    raise ValueError("Invalid embedding response")
                embeddings.extend(batch_embeddings)

        return embeddings
# endregion
