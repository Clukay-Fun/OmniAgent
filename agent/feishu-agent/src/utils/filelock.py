"""Simple cross-platform file lock."""

from __future__ import annotations

import os
import time
from pathlib import Path


class FileLock:
    def __init__(self, lock_path: str | Path, timeout: float = 5.0, poll_interval: float = 0.05) -> None:
        self._path = Path(lock_path)
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._handle = None

    def acquire(self) -> None:
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
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(self) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
