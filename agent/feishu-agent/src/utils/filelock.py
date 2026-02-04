"""
描述: 跨平台文件锁工具
主要功能:
    - 提供文件级互斥锁
    - 兼容 Windows (msvcrt) 和 Linux (fcntl)
    - 支持超时重试机制
"""

from __future__ import annotations

import os
import time
from pathlib import Path


# region 文件锁实现
class FileLock:
    """
    跨平台文件锁类
    
    功能:
        - 独占式锁定文件
        - 自动重试与超时控制
        - 上下文管理器支持 (with FileLock...)
    """
    
    def __init__(self, lock_path: str | Path, timeout: float = 5.0, poll_interval: float = 0.05) -> None:
        """
        初始化文件锁
        
        参数:
            lock_path: 锁文件路径
            timeout: 获取锁超时时间 (秒)
            poll_interval: 重试间隔 (秒)
        """
        self._path = Path(lock_path)
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._handle = None

    def acquire(self) -> None:
        """
        尝试获取锁
        
        异常:
            TimeoutError: 获取锁超时
        """
        start = time.time()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self._path, "a+")

        while True:
            try:
                self._lock_file()
                return
            except OSError:
                if time.time() - start >= self._timeout:
                    raise TimeoutError(f"Timeout acquiring lock: {self._path}")
                time.sleep(self._poll_interval)

    def release(self) -> None:
        """释放锁"""
        if not self._handle:
            return
        try:
            self._unlock_file()
        finally:
            try:
                self._handle.close()
            finally:
                self._handle = None

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def _lock_file(self) -> None:
        """平台相关的文件锁定实现"""
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(self) -> None:
        """平台相关的文件解锁实现"""
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
# endregion
