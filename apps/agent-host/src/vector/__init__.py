"""Backward-compatible exports for src.vector."""

from __future__ import annotations

import warnings

from src.infra.vector import ChromaStore, EmbeddingClient, VectorMemoryManager, load_vector_config

warnings.warn(
    "src.vector is deprecated; use src.infra.vector instead. "
    "This shim will be removed in a future iteration.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "load_vector_config",
    "EmbeddingClient",
    "ChromaStore",
    "VectorMemoryManager",
]
