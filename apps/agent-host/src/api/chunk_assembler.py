"""
描述: 入站文本分片聚合器
主要功能:
    - 按会话作用域在短窗口内聚合多条消息
    - 提供快速直通 (fast-path) 与保险丝上限 (fuse)
    - 以同步可测试方式实现，便于后续替换为异步定时器
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class ChunkDecision:
    """分片聚合决策结果。"""

    should_process: bool
    text: str = ""
    reason: str = ""


@dataclass
class _ChunkBuffer:
    segments: list[str]
    started_at: float
    last_at: float


class ChunkAssembler:
    """3 秒窗口分片聚合器。"""

    _FAST_PATH_SUFFIXES = ("。", "！", "？", ".", "!", "?", "；", ";")

    def __init__(
        self,
        enabled: bool,
        window_seconds: float = 3.0,
        max_segments: int = 5,
        max_chars: int = 500,
    ) -> None:
        self._enabled = bool(enabled)
        self._window_seconds = max(float(window_seconds), 0.1)
        self._max_segments = max(int(max_segments), 1)
        self._max_chars = max(int(max_chars), 1)
        self._buffers: dict[str, _ChunkBuffer] = {}
        self._lock = threading.Lock()

    def ingest(self, scope_key: str, text: str, now: float | None = None) -> ChunkDecision:
        """写入单条文本并返回是否应进入处理链路。"""
        content = str(text or "").strip()
        if not content:
            return ChunkDecision(should_process=False, reason="empty")
        if not self._enabled:
            return ChunkDecision(should_process=True, text=content, reason="disabled")

        ts = float(now) if now is not None else time.time()
        key = str(scope_key or "").strip() or "default"
        with self._lock:
            existing = self._buffers.get(key)
            if existing is None:
                if self._is_fast_path(content):
                    return ChunkDecision(should_process=True, text=content, reason="fast_path")
                self._buffers[key] = _ChunkBuffer(segments=[content], started_at=ts, last_at=ts)
                limited = self._apply_fuse(self._buffers[key].segments)
                if limited[1]:
                    self._buffers.pop(key, None)
                    return ChunkDecision(should_process=True, text=limited[0], reason="fuse_limit")
                return ChunkDecision(should_process=False, reason="buffering")

            if ts - existing.started_at > self._window_seconds:
                flushed, _ = self._apply_fuse(existing.segments)
                self._buffers[key] = _ChunkBuffer(segments=[content], started_at=ts, last_at=ts)
                return ChunkDecision(should_process=True, text=flushed, reason="window_elapsed")

            existing.segments.append(content)
            existing.last_at = ts
            merged, hit_fuse = self._apply_fuse(existing.segments)
            existing.segments = self._split_back(merged)

            if hit_fuse:
                self._buffers.pop(key, None)
                return ChunkDecision(should_process=True, text=merged, reason="fuse_limit")
            if self._is_fast_path(content):
                self._buffers.pop(key, None)
                return ChunkDecision(should_process=True, text=merged, reason="fast_path")
            return ChunkDecision(should_process=False, reason="buffering")

    def _is_fast_path(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        return stripped.endswith(self._FAST_PATH_SUFFIXES)

    def _apply_fuse(self, segments: list[str]) -> tuple[str, bool]:
        limited_segments = list(segments[: self._max_segments])
        merged = "\n".join(seg for seg in limited_segments if seg)
        fuse_hit = len(segments) >= self._max_segments
        if len(merged) > self._max_chars:
            merged = merged[: self._max_chars].rstrip()
            fuse_hit = True
        return merged, fuse_hit

    def _split_back(self, merged: str) -> list[str]:
        if not merged:
            return []
        return [part for part in merged.split("\n") if part]
