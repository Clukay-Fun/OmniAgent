"""
配置热更新模块

功能：
- 监控 YAML 配置文件变更
- 60 秒周期自动 reload
- 线程安全
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yaml

logger = logging.getLogger(__name__)


# ============================================
# region ConfigWatcher
# ============================================
class ConfigWatcher:
    """
    配置文件监控器
    
    功能：
    - 定期检查配置文件修改时间
    - 文件变更时触发回调
    - 线程安全的热更新
    """
    
    def __init__(
        self,
        config_path: str,
        reload_callback: Callable[[dict[str, Any]], None],
        interval_seconds: int = 60,
    ) -> None:
        """
        Args:
            config_path: 配置文件路径
            reload_callback: 配置变更时的回调函数
            interval_seconds: 检查间隔（秒）
        """
        self._config_path = Path(config_path)
        self._callback = reload_callback
        self._interval = interval_seconds
        self._last_mtime: float = 0.0
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        
        # 初始加载
        self._load_config()

    def start(self) -> None:
        """启动监控线程"""
        if self._running:
            logger.warning("ConfigWatcher already running")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        logger.info(f"ConfigWatcher started: {self._config_path} (interval={self._interval}s)")

    def stop(self) -> None:
        """停止监控线程"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("ConfigWatcher stopped")

    def _watch_loop(self) -> None:
        """监控循环"""
        while self._running:
            time.sleep(self._interval)
            if not self._running:
                break
            
            try:
                self._check_and_reload()
            except Exception as e:
                logger.error(f"Config watch error: {e}")

    def _check_and_reload(self) -> None:
        """检查文件变更并重新加载"""
        if not self._config_path.exists():
            logger.warning(f"Config file not found: {self._config_path}")
            return
        
        current_mtime = os.path.getmtime(self._config_path)
        
        if current_mtime > self._last_mtime:
            logger.info(f"Config file changed, reloading: {self._config_path}")
            self._load_config()

    def _load_config(self) -> None:
        """加载配置文件"""
        with self._lock:
            if not self._config_path.exists():
                logger.warning(f"Config file not found: {self._config_path}")
                return
            
            try:
                self._last_mtime = os.path.getmtime(self._config_path)
                
                with open(self._config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                
                self._callback(config)
                logger.info(f"Config loaded: {self._config_path}")
                
            except Exception as e:
                logger.error(f"Failed to load config: {e}")

    def force_reload(self) -> None:
        """强制重新加载"""
        self._load_config()
# endregion
# ============================================


# ============================================
# region HotReloadManager
# ============================================
class HotReloadManager:
    """
    热更新管理器
    
    统一管理多个配置文件的热更新
    """
    
    def __init__(self) -> None:
        self._watchers: list[ConfigWatcher] = []

    def add_watcher(
        self,
        config_path: str,
        reload_callback: Callable[[dict[str, Any]], None],
        interval_seconds: int = 60,
    ) -> ConfigWatcher:
        """
        添加配置监控器
        
        Args:
            config_path: 配置文件路径
            reload_callback: 配置变更回调
            interval_seconds: 检查间隔
            
        Returns:
            ConfigWatcher 实例
        """
        watcher = ConfigWatcher(
            config_path=config_path,
            reload_callback=reload_callback,
            interval_seconds=interval_seconds,
        )
        self._watchers.append(watcher)
        return watcher

    def start_all(self) -> None:
        """启动所有监控器"""
        for watcher in self._watchers:
            watcher.start()
        logger.info(f"Started {len(self._watchers)} config watchers")

    def stop_all(self) -> None:
        """停止所有监控器"""
        for watcher in self._watchers:
            watcher.stop()
        logger.info("Stopped all config watchers")
# endregion
# ============================================
