"""
描述: ChromaDB 向量存储封装
主要功能:
    - 封装 ChromaDB 客户端
    - 管理 Collection 隔离 (按 User ID)
    - 提供文档增删改查接口
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


# region ChromaDB 适配器
class ChromaStore:
    """
    ChromaDB 存储适配器 (User 粒度隔离)
    
    功能:
        - 自动管理 PersistentClient
        - 懒加载机制 (Lazy Loading)
        - 屏蔽依赖缺失异常
    """

    def __init__(self, persist_path: str, collection_prefix: str = "memory_vectors_") -> None:
        """
        初始化适配器

        参数:
            persist_path: 数据持久化路径
            collection_prefix: Collection 命名前缀
        """
        self._persist_path = persist_path
        self._collection_prefix = collection_prefix
        self._client = None

    @property
    def is_available(self) -> bool:
        """检查 ChromaDB 是否可用"""
        return _CHROMA_AVAILABLE

    def _get_client(self):
        """获取或创建 ChromaDB 客户端 (懒加载)"""
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
        """
        批量添加文档

        参数:
            user_id: 归属用户 ID (决定 Collection)
            documents: 原始文本文档
            embeddings: 对应的向量列表
            metadatas: 元数据列表
            ids: 文档唯一 ID
        """
        collection = self._get_collection(user_id)
        collection.add(
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
            ids=ids,
        )

    def query(self, user_id: str, embedding: list[float], top_k: int) -> list[str]:
        """
        向量检索

        参数:
            user_id: 归属用户 ID
            embedding: 查询向量
            top_k: 返回结果数量

        返回:
            匹配的文本文档列表
        """
        collection = self._get_collection(user_id)
        result = collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
        )
        docs = result.get("documents") or [[]]
        return docs[0]
# endregion
