"""
描述: 轻量级配置加载器
主要功能:
    - 自动定期重载配置文件 (Auto-Reload)
    - 线程安全的配置访问 (Thread-Safe)
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import yaml


# region 配置管理器
class ConfigManager:
    """
    配置管理器
    
    属性:
        config_path: 配置文件路径
        reload_interval: 重载检查间隔 (秒)
    """
    def __init__(self, config_path: str | Path, reload_interval: int = 60) -> None:
        self._path = Path(config_path)
        self._reload_interval = reload_interval
        self._lock = threading.Lock()
        self._last_load = 0.0
        self._data: dict[str, Any] = {}
        self.reload()

    def get(self) -> dict[str, Any]:
        """获取最新配置 (如果过期则自动重载)"""
        self._reload_if_needed()
        with self._lock:
            return dict(self._data)

    def reload(self) -> None:
        """强制重载配置"""
        with self._lock:
            if not self._path.exists():
                self._data = {}
                self._last_load = time.time()
                return
            self._data = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
            self._last_load = time.time()

    def _reload_if_needed(self) -> None:
        """检查是否需要重载"""
        if time.time() - self._last_load < self._reload_interval:
            return
        self.reload()
# endregion
