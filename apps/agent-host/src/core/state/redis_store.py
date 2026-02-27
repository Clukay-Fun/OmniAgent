"""
描述: Redis 会话状态存储占位实现。
主要功能:
    - 提供基于 Redis 的会话状态存储。
    - 支持会话状态的获取、设置和删除。
    - 支持过期会话的清理。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from typing import Any

from src.core.state.models import (
    ActiveRecordState,
    ConversationState,
    LastResultState,
    PaginationState,
    PendingActionState,
    PendingDeleteState,
)

logger = logging.getLogger(__name__)


class RedisStateStore:
    """Redis-backed 状态存储（轻量占位）。

    功能:
        - 初始化 Redis 客户端。
        - 从配置中创建 RedisStateStore 实例。
        - 获取、设置和删除会话状态。
        - 清理过期的会话状态。
        - 统计活跃会话数量。
    """

    def __init__(self, client: Any, key_prefix: str = "omniagent:state:") -> None:
        """初始化 RedisStateStore 实例。

        参数:
            client (Any): Redis 客户端实例。
            key_prefix (str): 会话状态存储的键前缀。
        """
        self._client = client
        self._key_prefix = str(key_prefix or "omniagent:state:")

    @classmethod
    def from_settings(cls, redis_settings: Any) -> RedisStateStore:
        """从配置中创建 RedisStateStore 实例。

        参数:
            redis_settings (Any): 包含 Redis 配置的设置对象。

        返回:
            RedisStateStore: 初始化后的 RedisStateStore 实例。

        异常:
            RuntimeError: 如果 Redis 依赖不可用或连接失败。
        """
        try:
            import redis
        except Exception as exc:  # pragma: no cover - 依赖是否安装由环境决定
            raise RuntimeError("redis dependency unavailable") from exc

        dsn = str(getattr(redis_settings, "dsn", "") or "").strip()
        socket_timeout = float(getattr(redis_settings, "socket_timeout_seconds", 1.0) or 1.0)
        if dsn:
            client = redis.Redis.from_url(
                dsn,
                decode_responses=True,
                socket_timeout=socket_timeout,
            )
        else:
            client = redis.Redis(
                host=str(getattr(redis_settings, "host", "localhost") or "localhost"),
                port=int(getattr(redis_settings, "port", 6379) or 6379),
                db=int(getattr(redis_settings, "db", 0) or 0),
                password=getattr(redis_settings, "password", None),
                decode_responses=True,
                socket_timeout=socket_timeout,
            )

        try:
            client.ping()
        except Exception as exc:
            raise RuntimeError("redis ping failed") from exc

        return cls(client=client, key_prefix=str(getattr(redis_settings, "key_prefix", "omniagent:state:")))

    def get(self, session_key: str | None = None, *, user_id: str | None = None) -> ConversationState | None:
        """获取会话状态。

        参数:
            session_key (str | None): 会话键。
            user_id (str | None): 用户 ID。

        返回:
            ConversationState | None: 会话状态对象或 None。
        """
        key = str(session_key or user_id or "").strip()
        if not key:
            return None
        raw = self._client.get(self._redis_key(key))
        if not raw:
            return None
        state = self._deserialize_state(raw)
        if state is None:
            self.delete(key)
            return None
        state.session_key = key
        return state

    def set(
        self,
        session_key: str | None = None,
        state: ConversationState | None = None,
        *,
        user_id: str | None = None,
    ) -> None:
        """设置会话状态。

        参数:
            session_key (str | None): 会话键。
            state (ConversationState | None): 会话状态对象。
            user_id (str | None): 用户 ID。
        """
        key = str(session_key or user_id or "").strip()
        if not key or state is None:
            return

        payload = json.dumps(asdict(state))
        ttl_seconds = max(int(state.expires_at - time.time()), 1)
        self._client.set(self._redis_key(key), payload, ex=ttl_seconds)

    def delete(self, session_key: str | None = None, *, user_id: str | None = None) -> None:
        """删除会话状态。

        参数:
            session_key (str | None): 会话键。
            user_id (str | None): 用户 ID。
        """
        key = str(session_key or user_id or "").strip()
        if not key:
            return
        self._client.delete(self._redis_key(key))

    def list_session_keys(self) -> list[str]:
        """列出所有会话键。

        返回:
            list[str]: 会话键列表。
        """
        keys: list[str] = []
        for redis_key in self._iter_keys():
            as_text = str(redis_key)
            if as_text.startswith(self._key_prefix):
                keys.append(as_text[len(self._key_prefix) :])
        return keys

    def cleanup_expired(self) -> None:
        """清理过期的会话状态。"""
        now = time.time()
        for session_key in self.list_session_keys():
            state = self.get(session_key)
            if state is None or state.is_expired(now):
                self.delete(session_key)

    def active_count(self) -> int:
        """统计活跃会话数量。

        返回:
            int: 活跃会话数量。
        """
        return len(self.list_session_keys())

    def _iter_keys(self) -> list[str]:
        """迭代所有匹配的 Redis 键。

        返回:
            list[str]: 匹配的键列表。
        """
        pattern = f"{self._key_prefix}*"
        if hasattr(self._client, "scan_iter"):
            return [str(item) for item in self._client.scan_iter(match=pattern)]
        if hasattr(self._client, "keys"):
            return [str(item) for item in self._client.keys(pattern)]
        return []

    def _redis_key(self, session_key: str) -> str:
        """生成 Redis 键。

        参数:
            session_key (str): 会话键。

        返回:
            str: 完整的 Redis 键。
        """
        return f"{self._key_prefix}{session_key}"

    def _deserialize_state(self, raw: str) -> ConversationState | None:
        """反序列化会话状态。

        参数:
            raw (str): 序列化后的会话状态字符串。

        返回:
            ConversationState | None: 反序列化后的会话状态对象或 None。
        """
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                return None

            if isinstance(data.get("pending_delete"), dict):
                data["pending_delete"] = PendingDeleteState(**data["pending_delete"])
            if isinstance(data.get("pagination"), dict):
                data["pagination"] = PaginationState(**data["pagination"])
            if isinstance(data.get("last_result"), dict):
                data["last_result"] = LastResultState(**data["last_result"])
            if isinstance(data.get("active_record"), dict):
                data["active_record"] = ActiveRecordState(**data["active_record"])
            if isinstance(data.get("pending_action"), dict):
                data["pending_action"] = PendingActionState(**data["pending_action"])
            return ConversationState(**data)
        except Exception:
            logger.warning(
                "反序列化会话状态失败，已丢弃无效值",
                extra={"event_code": "state_store.redis.deserialize_failed"},
            )
            return None
