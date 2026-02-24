"""
Redis 会话状态存储占位实现。

说明：
- 当前实现以最小可用为目标，优先保证失败可降级。
- 不提供分布式锁、pipeline 或高级优化。
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
    """Redis-backed 状态存储（轻量占位）。"""

    def __init__(self, client: Any, key_prefix: str = "omniagent:state:") -> None:
        self._client = client
        self._key_prefix = str(key_prefix or "omniagent:state:")

    @classmethod
    def from_settings(cls, redis_settings: Any) -> RedisStateStore:
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
        key = str(session_key or user_id or "").strip()
        if not key or state is None:
            return

        payload = json.dumps(asdict(state))
        ttl_seconds = max(int(state.expires_at - time.time()), 1)
        self._client.set(self._redis_key(key), payload, ex=ttl_seconds)

    def delete(self, session_key: str | None = None, *, user_id: str | None = None) -> None:
        key = str(session_key or user_id or "").strip()
        if not key:
            return
        self._client.delete(self._redis_key(key))

    def list_session_keys(self) -> list[str]:
        keys: list[str] = []
        for redis_key in self._iter_keys():
            as_text = str(redis_key)
            if as_text.startswith(self._key_prefix):
                keys.append(as_text[len(self._key_prefix) :])
        return keys

    def cleanup_expired(self) -> None:
        now = time.time()
        for session_key in self.list_session_keys():
            state = self.get(session_key)
            if state is None or state.is_expired(now):
                self.delete(session_key)

    def active_count(self) -> int:
        return len(self.list_session_keys())

    def _iter_keys(self) -> list[str]:
        pattern = f"{self._key_prefix}*"
        if hasattr(self._client, "scan_iter"):
            return [str(item) for item in self._client.scan_iter(match=pattern)]
        if hasattr(self._client, "keys"):
            return [str(item) for item in self._client.keys(pattern)]
        return []

    def _redis_key(self, session_key: str) -> str:
        return f"{self._key_prefix}{session_key}"

    def _deserialize_state(self, raw: str) -> ConversationState | None:
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
