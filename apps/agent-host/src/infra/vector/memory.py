"""
描述: 向量记忆管理器
主要功能:
    - 统一封装向量存储 (Chroma) 与 Embedding 客户端
    - 管理记忆写入与检索流程
    - 异常处理与降级
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from src.infra.vector.chroma_store import ChromaStore
from src.infra.vector.embedding import EmbeddingClient

logger = logging.getLogger(__name__)


# region 向量记忆管理器
class VectorMemoryManager:
    """
    向量记忆管理器

    功能:
        - 协调 Embedding 生成与向量存储
        - 管理用户语义记忆检索
    """

    def __init__(
        self,
        store: ChromaStore,
        embedder: EmbeddingClient,
        top_k: int = 5,
        fallback: str = "keyword",
    ) -> None:
        """
        初始化管理器

        参数:
            store: 向量数据库实例
            embedder: Embedding 客户端
            top_k: 默认检索条数
            fallback: 检索失败时的降级策略 (保留字段)
        """
        self._store = store
        self._embedder = embedder
        self._top_k = top_k
        self._fallback = fallback

    async def add_memory(self, user_id: str, content: str, metadata: dict[str, Any]) -> None:
        """
        添加语义记忆

        参数:
            user_id: 用户 ID
            content: 记忆文本内容
            metadata: 关联元数据
        """
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
        """
        语义检索

        参数:
            user_id: 用户 ID
            query: 查询文本
            top_k: 自定义返回条数 (覆盖默认值)

        返回:
            匹配的记忆文本列表
        """
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
# endregion
