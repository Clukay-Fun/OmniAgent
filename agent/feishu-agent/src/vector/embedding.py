"""
Embedding client for vector memory.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class EmbeddingClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self._provider = config.get("provider", "")
        self._api_base = config.get("api_base", "")
        self._api_key = config.get("api_key", "")
        self._model = config.get("model", "")
        self._timeout = float(config.get("timeout", 10))
        self._batch_size = max(int(config.get("batch_size", 32)), 1)

    @property
    def batch_size(self) -> int:
        return self._batch_size

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
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
