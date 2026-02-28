"""
描述: 提供向量存储和管理的核心功能
主要功能:
    - 加载向量配置
    - 提供嵌入客户端
    - 实现Chroma存储
    - 管理向量内存
"""

from __future__ import annotations

from src.vector.config import load_vector_config
from src.vector.embedding import EmbeddingClient
from src.vector.chroma_store import ChromaStore
from src.vector.memory import VectorMemoryManager

__all__ = [
    "load_vector_config",
    "EmbeddingClient",
    "ChromaStore",
    "VectorMemoryManager",
]
