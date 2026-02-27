"""
描述: 提供基于回调级别的去重功能，确保同一个回调在一个时间窗口内只执行一次。
主要功能:
    - 构建唯一的回调键
    - 检查并标记回调是否在时间窗口内重复
    - 清理过期的回调记录
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

# region 类定义
class CallbackDeduper:
    """内存中的LRU回调去重器。

    用法:
        deduper = CallbackDeduper(window_seconds=10)
        key = deduper.build_key(user_id="u1", action="create_record_confirm", payload={...})
        if not deduper.try_acquire(key):
            return  # 短路
        # 继续处理回调
    """

    def __init__(self, window_seconds: int = 10, max_size: int = 2048) -> None:
        """初始化回调去重器。

        功能:
            - 设置去重窗口时间
            - 设置缓存最大大小
            - 初始化有序字典缓存和线程锁
        """
        self._window = max(1, window_seconds)
        self._max_size = max(64, max_size)
        self._cache: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def build_key(self, *, user_id: str, action: str, payload: dict[str, Any] | None = None) -> str:
        """从user_id、action和确定性的payload哈希构建去重键。

        功能:
            - 将payload转换为JSON字符串
            - 计算payload的MD5哈希值
            - 返回格式化的去重键
        """
        raw = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False, default=str)
        payload_hash = hashlib.md5(raw.encode()).hexdigest()[:12]
        return f"cb:{user_id}:{action}:{payload_hash}"

    def is_duplicate(self, key: str) -> bool:
        """检查该键是否在去重窗口内已被处理。

        功能:
            - 获取锁
            - 清理过期的缓存项
            - 检查键是否在缓存中
        """
        with self._lock:
            self._cleanup_locked()
            return key in self._cache

    def try_acquire(self, key: str) -> bool:
        """原子性地检查并标记键。

        功能:
            - 获取当前时间
            - 获取锁
            - 清理过期的缓存项
            - 检查键是否在缓存中，如果在则返回False
            - 将键添加到缓存中并返回True
            - 如果缓存大小超过最大值，则移除最旧的项
        """
        now = time.time()
        with self._lock:
            self._cleanup_locked(now)
            if key in self._cache:
                return False
            self._cache[key] = now
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
            return True

    def mark(self, key: str) -> None:
        """将该键标记为已处理。

        功能:
            - 获取当前时间
            - 获取锁
            - 移除旧的键值对（如果存在）
            - 将键添加到缓存中
            - 如果缓存大小超过最大值，则移除最旧的项
        """
        now = time.time()
        with self._lock:
            self._cache.pop(key, None)
            self._cache[key] = now
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def _cleanup_locked(self, now: float | None = None) -> None:
        """清理过期的缓存项。

        功能:
            - 获取当前时间（如果未提供）
            - 计算过期时间
            - 移除所有过期的缓存项
        """
        if now is None:
            now = time.time()
        cutoff = now - self._window
        while self._cache:
            oldest_key = next(iter(self._cache))
            if self._cache[oldest_key] < cutoff:
                self._cache.pop(oldest_key)
            else:
                break
# endregion
