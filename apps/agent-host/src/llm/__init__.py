"""Backward-compatible exports for src.llm."""

from __future__ import annotations

import warnings

from src.infra.llm import LLMClient, create_llm_client

warnings.warn(
    "src.llm is deprecated; use src.infra.llm instead. "
    "This shim will be removed in a future iteration.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["LLMClient", "create_llm_client"]
