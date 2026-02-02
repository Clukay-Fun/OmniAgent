"""Memory manager for shared and user memories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from src.utils.filelock import FileLock
from src.utils.workspace import ensure_workspace, get_workspace_root


@dataclass
class MemorySnapshot:
    shared_memory: str
    user_memory: str
    recent_logs: str


class MemoryManager:
    def __init__(
        self,
        workspace_root: Path | None = None,
        retention_days: int = 30,
        lock_timeout: float = 5.0,
        max_context_tokens: int = 2000,
    ) -> None:
        self._workspace_root = Path(workspace_root) if workspace_root else get_workspace_root()
        ensure_workspace(self._workspace_root)
        self._retention_days = retention_days
        self._lock_timeout = lock_timeout
        self._max_context_tokens = max_context_tokens

    def load_shared_memory(self) -> str:
        path = self._workspace_root / "MEMORY.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def load_user_memory(self, user_id: str) -> str:
        path = self._user_dir(user_id) / "memory.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def load_recent_logs(self, user_id: str, days: int = 2) -> str:
        daily_dir = self._daily_dir(user_id)
        if not daily_dir.exists():
            return ""

        today = datetime.now().date()
        parts: list[str] = []
        for offset in range(days):
            date_str = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
            path = daily_dir / f"{date_str}.md"
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
        merged = "\n".join(reversed(parts)).strip()
        return self._truncate_text(merged, self._max_context_tokens)

    def append_daily_log(self, user_id: str, content: str) -> None:
        daily_dir = self._daily_dir(user_id)
        daily_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d")
        path = daily_dir / f"{date_str}.md"
        line = f"- {datetime.now().isoformat()} {content}\n"

        lock = FileLock(str(path) + ".lock", timeout=self._lock_timeout)
        with lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)

    def remember_user(self, user_id: str, content: str) -> None:
        user_dir = self._user_dir(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        path = user_dir / "memory.md"
        line = f"- {content}\n"

        lock = FileLock(str(path) + ".lock", timeout=self._lock_timeout)
        with lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)

    def snapshot(self, user_id: str, days: int = 2) -> MemorySnapshot:
        return MemorySnapshot(
            shared_memory=self.load_shared_memory(),
            user_memory=self.load_user_memory(user_id),
            recent_logs=self.load_recent_logs(user_id, days=days),
        )

    def cleanup_logs(self) -> int:
        cutoff = datetime.now().date() - timedelta(days=self._retention_days)
        removed = 0
        users_root = self._workspace_root / "users"
        if not users_root.exists():
            return 0

        for daily_dir in users_root.glob("*/daily"):
            for path in daily_dir.glob("*.md"):
                try:
                    date_str = path.stem
                    log_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if log_date < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed

    def _user_dir(self, user_id: str) -> Path:
        return self._workspace_root / "users" / user_id

    def _daily_dir(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "daily"

    @staticmethod
    def _truncate_text(text: str, max_tokens: int) -> str:
        if not text:
            return ""

        words = text.split()
        if len(words) >= max_tokens:
            return " ".join(words[-max_tokens:])

        if len(text) <= max_tokens:
            return text
        return text[-max_tokens:]
