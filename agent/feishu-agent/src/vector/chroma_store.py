"""
Chroma vector store wrapper.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import chromadb
    _CHROMA_AVAILABLE = True
except Exception:
    _CHROMA_AVAILABLE = False


class ChromaStore:
    def __init__(self, persist_path: str, collection_prefix: str = "memory_vectors_") -> None:
        self._persist_path = persist_path
        self._collection_prefix = collection_prefix
        self._client = None

    def is_available(self) -> bool:
        return _CHROMA_AVAILABLE

    def _get_client(self):
        if not _CHROMA_AVAILABLE:
            raise RuntimeError("chromadb not installed")
        if self._client is None:
            self._client = chromadb.PersistentClient(path=self._persist_path)
        return self._client

    def _get_collection(self, user_id: str):
        client = self._get_client()
        name = f"{self._collection_prefix}{user_id}"
        return client.get_or_create_collection(name=name)

    def add_documents(
        self,
        user_id: str,
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
        ids: list[str],
    ) -> None:
        collection = self._get_collection(user_id)
        collection.add(
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
            ids=ids,
        )

    def query(self, user_id: str, embedding: list[float], top_k: int) -> list[str]:
        collection = self._get_collection(user_id)
        result = collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
        )
        docs = result.get("documents") or [[]]
        return docs[0]
