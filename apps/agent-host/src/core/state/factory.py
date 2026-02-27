"""
描述: StateStore 选择工厂。
主要功能:
    - 根据配置创建状态存储实例
    - 支持 memory 和 redis 两种后端
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.state.memory_store import MemoryStateStore
from src.core.state.redis_store import RedisStateStore
from src.core.state.store import StateStore
from src.utils.metrics import record_state_store_backend

logger = logging.getLogger(__name__)

# region 工厂函数
def create_state_store(settings: Any) -> StateStore:
    """
    根据配置创建状态存储，默认使用 memory 后端。

    功能:
        - 从配置中获取状态存储后端类型
        - 根据后端类型创建相应的 StateStore 实例
        - 如果配置的后端不可用，则回退到 memory 后端
    """
    configured_backend = str(getattr(getattr(settings, "state_store", None), "backend", "memory") or "memory")
    backend = configured_backend.strip().lower() or "memory"
    if backend == "memory":
        record_state_store_backend("memory", "selected")
        logger.info(
            "状态存储后端: memory",
            extra={"event_code": "state_store.factory.selected", "backend": "memory"},
        )
        return MemoryStateStore()

    if backend == "redis":
        redis_settings = getattr(getattr(settings, "state_store", None), "redis", None)
        try:
            store = RedisStateStore.from_settings(redis_settings)
            record_state_store_backend("redis", "selected")
            logger.info(
                "状态存储后端: redis",
                extra={"event_code": "state_store.factory.selected", "backend": "redis"},
            )
            return store
        except Exception as exc:
            record_state_store_backend("redis", "fallback_memory")
            logger.warning(
                "Redis 状态存储初始化失败，回退 memory: %s",
                exc,
                extra={
                    "event_code": "state_store.factory.fallback_memory",
                    "backend": "redis",
                },
            )
            return MemoryStateStore()

    record_state_store_backend(backend, "fallback_memory")
    logger.warning(
        "未知状态存储后端，回退 memory: %s",
        backend,
        extra={"event_code": "state_store.factory.unknown_backend", "backend": backend},
    )
    return MemoryStateStore()
# endregion
