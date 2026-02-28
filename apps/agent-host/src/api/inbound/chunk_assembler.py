"""
描述: 入站文本分片聚合器
主要功能:
    - 按会话作用域在短窗口内聚合多条消息
    - 提供快速直通 (fast-path) 与保险丝上限 (fuse)
    - 基于会话状态管理器持久化分片缓冲
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from src.core.state.manager import ConversationStateManager
from src.core.state.models import MessageChunkState


@dataclass
class ChunkDecision:
    """分片聚合决策结果。"""

    should_process: bool
    text: str = ""
    reason: str = ""


class ChunkAssembler:
    """3 秒窗口分片聚合器。"""

    _FAST_PATH_SUFFIXES = ("。", "！", "？", ".", "!", "?", "；", ";")

    def __init__(
        self,
        enabled: bool,
        state_manager: ConversationStateManager,
        window_seconds: float = 3.0,
        stale_window_seconds: float = 10.0,
        max_segments: int = 5,
        max_chars: int = 500,
    ) -> None:
        """
        初始化分片聚合器。

        功能:
            - 设置聚合器的启用状态
            - 初始化会话状态管理器
            - 设置窗口时间和过期时间
            - 设置最大分段数和最大字符数
        """
        self._enabled = bool(enabled)
        self._state_manager = state_manager
        self._window_seconds = max(float(window_seconds), 0.1)
        self._stale_window_seconds = max(float(stale_window_seconds), self._window_seconds)
        self._max_segments = max(int(max_segments), 1)
        self._max_chars = max(int(max_chars), 1)
        self._locks: dict[str, asyncio.Lock] = {}

    async def ingest(self, scope_key: str, text: str, now: float | None = None) -> ChunkDecision:
        """
        写入单条文本并返回是否应进入处理链路。

        功能:
            - 检查文本是否为空
            - 检查聚合器是否启用
            - 获取当前时间戳
            - 获取或创建会话锁
            - 获取现有消息分片状态
            - 处理快速直通情况
            - 创建新的消息分片状态
            - 应用保险丝限制
            - 更新消息分片状态
            - 返回聚合决策结果
        """
        content = str(text or "").strip()
        if not content:
            return ChunkDecision(should_process=False, reason="empty")
        if not self._enabled:
            return ChunkDecision(should_process=True, text=content, reason="disabled")

        ts = float(now) if now is not None else time.time()
        key = str(scope_key or "").strip() or "default"
        lock = self._get_lock(key)
        async with lock:
            existing = self._state_manager.get_message_chunk(key, now=ts, enforce_stale=False)
            if existing is None:
                if self._is_fast_path(content):
                    self._cleanup_lock_if_idle(key, now=ts)
                    return ChunkDecision(should_process=True, text=content, reason="fast_path")
                chunk = MessageChunkState(segments=[content], started_at=ts, last_at=ts)
                merged, hit_fuse = self._apply_fuse(chunk.segments)
                if hit_fuse:
                    self._state_manager.set_message_chunk(key, None)
                    self._cleanup_lock_if_idle(key, now=ts)
                    return ChunkDecision(should_process=True, text=merged, reason="fuse_limit")
                chunk.segments = self._split_back(merged)
                chunk.last_at = ts
                self._state_manager.set_message_chunk(key, chunk)
                return ChunkDecision(should_process=False, reason="buffering")

            age = ts - existing.started_at
            if age > self._stale_window_seconds:
                flushed, _ = self._apply_fuse(existing.segments)
                self._state_manager.set_message_chunk(
                    key,
                    MessageChunkState(segments=[content], started_at=ts, last_at=ts),
                )
                return ChunkDecision(should_process=True, text=flushed, reason="stale_window_elapsed")

            if age > self._window_seconds:
                flushed, _ = self._apply_fuse(existing.segments)
                self._state_manager.set_message_chunk(
                    key,
                    MessageChunkState(segments=[content], started_at=ts, last_at=ts),
                )
                return ChunkDecision(should_process=True, text=flushed, reason="window_elapsed")

            existing.segments.append(content)
            existing.last_at = ts
            merged, hit_fuse = self._apply_fuse(existing.segments)
            if hit_fuse:
                self._state_manager.set_message_chunk(key, None)
                self._cleanup_lock_if_idle(key, now=ts)
                return ChunkDecision(should_process=True, text=merged, reason="fuse_limit")

            existing.segments = self._split_back(merged)
            self._state_manager.set_message_chunk(key, existing)
            if self._is_fast_path(content):
                self._state_manager.set_message_chunk(key, None)
                self._cleanup_lock_if_idle(key, now=ts)
                return ChunkDecision(should_process=True, text=merged, reason="fast_path")
            return ChunkDecision(should_process=False, reason="buffering")

    async def drain(self, scope_key: str) -> ChunkDecision:
        """
        主动冲刷指定作用域残留分片。

        功能:
            - 获取或创建会话锁
            - 获取现有消息分片状态
            - 清除消息分片状态
            - 应用保险丝限制
            - 返回聚合决策结果
        """
        key = str(scope_key or "").strip() or "default"
        lock = self._get_lock(key)
        async with lock:
            existing = self._state_manager.get_message_chunk(key, enforce_stale=False)
            if existing is None:
                self._cleanup_lock_if_idle(key)
                return ChunkDecision(should_process=False, reason="empty")
            self._state_manager.set_message_chunk(key, None)
            merged, _ = self._apply_fuse(existing.segments)
            if not merged.strip():
                self._cleanup_lock_if_idle(key)
                return ChunkDecision(should_process=False, reason="empty")
            self._cleanup_lock_if_idle(key)
            return ChunkDecision(should_process=True, text=merged, reason="manual_drain")

    def _get_lock(self, key: str) -> asyncio.Lock:
        """
        获取或创建指定键的锁。

        功能:
            - 检查锁是否存在
            - 创建并存储新锁
            - 返回锁
        """
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _cleanup_lock_if_idle(self, key: str, now: float | None = None) -> None:
        """
        如果会话为空，则清理指定键的锁。

        功能:
            - 检查会话状态
            - 清理锁
        """
        if self._state_manager.get_message_chunk(key, now=now, enforce_stale=False) is None:
            self._locks.pop(key, None)

    def _is_fast_path(self, text: str) -> bool:
        """
        检查文本是否符合快速直通条件。

        功能:
            - 去除文本首尾空白
            - 检查文本是否以快速直通后缀结尾
        """
        stripped = text.strip()
        if not stripped:
            return False
        return stripped.endswith(self._FAST_PATH_SUFFIXES)

    def _apply_fuse(self, segments: list[str]) -> tuple[str, bool]:
        """
        应用保险丝限制。

        功能:
            - 限制分段数量
            - 合并分段
            - 检查合并后的文本长度是否超过限制
            - 返回合并后的文本和是否触发保险丝
        """
        limited_segments = list(segments[: self._max_segments])
        merged = "\n".join(seg for seg in limited_segments if seg)
        fuse_hit = len(segments) >= self._max_segments
        if len(merged) > self._max_chars:
            merged = merged[: self._max_chars].rstrip()
            fuse_hit = True
        return merged, fuse_hit

    def _split_back(self, merged: str) -> list[str]:
        """
        将合并后的文本拆分为分段。

        功能:
            - 拆分文本
            - 过滤空分段
            - 返回分段列表
        """
        if not merged:
            return []
        return [part for part in merged.split("\n") if part]
