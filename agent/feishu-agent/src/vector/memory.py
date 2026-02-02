"""
Vector memory manager.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from src.vector.chroma_store import ChromaStore
from src.vector.embedding import EmbeddingClient

logger = logging.getLogger(__name__)


class VectorMemoryManager:
    def __init__(
        self,
        store: ChromaStore,
        embedder: EmbeddingClient,
        top_k: int = 5,
        fallback: str = "keyword",
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._top_k = top_k
        self._fallback = fallback

    async def add_memory(self, user_id: str, content: str, metadata: dict[str, Any]) -> None:
        if not content.strip():
            return
        if not self._store.is_available():
            return
        try:
            embedding = await self._embedder.embed_texts([content])
        except Exception as exc:
            logger.warning("Vector embed failed: %s", exc)
            return
        if not embedding:
            return

        try:
            self._store.add_documents(
                user_id=user_id,
                documents=[content],
                embeddings=embedding,
                metadatas=[metadata],
                ids=[str(uuid.uuid4())],
            )
        except Exception as exc:
            logger.warning("Vector store add failed: %s", exc)
            return

    async def search(self, user_id: str, query: str, top_k: int | None = None) -> list[str]:
        if not query.strip():
            return []
        if not self._store.is_available():
            return []

        k = top_k if top_k is not None else self._top_k
        try:
            embedding = await self._embedder.embed_texts([query])
        except Exception as exc:
            logger.warning("Vector embed failed: %s", exc)
            return []
        if not embedding:
            return []

        try:
            return self._store.query(user_id=user_id, embedding=embedding[0], top_k=k)
        except Exception as exc:
            logger.warning("Vector store query failed: %s", exc)
            return []
