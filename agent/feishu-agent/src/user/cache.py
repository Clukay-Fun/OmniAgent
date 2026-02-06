"""
描述: 用户身份缓存管理器
主要功能:
    - 内存缓存用户身份信息
    - TTL 过期管理
    - LRU 淘汰策略
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Dict, Optional

from src.user import UserProfile


# ============================================
# region 用户缓存管理器
# ============================================
class UserCache:
    """
    用户身份缓存
    
    功能:
        - 内存存储 UserProfile
        - TTL 自动过期
        - LRU 淘汰（容量限制）
    """
    
    def __init__(self, ttl_hours: int = 24, max_size: int = 1000):
        """
        初始化缓存
        
        参数:
            ttl_hours: 缓存有效期（小时）
            max_size: 最大缓存条目数
        """
        self._ttl_hours = ttl_hours
        self._max_size = max_size
        self._cache: Dict[str, UserProfile] = {}
        self._access_order: Dict[str, float] = {}  # open_id -> last_access_time
    
    def get(self, open_id: str) -> Optional[UserProfile]:
        """
        获取用户信息
        
        参数:
            open_id: 用户 Open ID
            
        返回:
            UserProfile 或 None（未找到或已过期）
        """
        profile = self._cache.get(open_id)
        if profile is None:
            return None
        
        # 检查是否过期
        if self._is_expired(profile):
            self._cache.pop(open_id, None)
            self._access_order.pop(open_id, None)
            return None
        
        # 更新访问时间
        self._access_order[open_id] = time.time()
        return profile
    
    def set(self, profile: UserProfile) -> None:
        """
        缓存用户信息
        
        参数:
            profile: 用户档案
        """
        # LRU 淘汰
        if len(self._cache) >= self._max_size:
            self._evict_lru()
        
        # 更新缓存时间
        profile.cached_at = datetime.now()
        self._cache[profile.open_id] = profile
        self._access_order[profile.open_id] = time.time()
    
    def remove(self, open_id: str) -> None:
        """删除缓存"""
        self._cache.pop(open_id, None)
        self._access_order.pop(open_id, None)
    
    def clear(self) -> None:
        """清空缓存"""
        self._cache.clear()
        self._access_order.clear()
    
    def _is_expired(self, profile: UserProfile) -> bool:
        """检查是否过期"""
        if profile.cached_at is None:
            return True
        expiry = profile.cached_at + timedelta(hours=self._ttl_hours)
        return datetime.now() > expiry
    
    def _evict_lru(self) -> None:
        """淘汰最久未访问的条目"""
        if not self._access_order:
            return
        
        # 找到最旧的访问记录
        lru_key = min(self._access_order, key=self._access_order.get)
        self._cache.pop(lru_key, None)
        self._access_order.pop(lru_key, None)
# endregion
