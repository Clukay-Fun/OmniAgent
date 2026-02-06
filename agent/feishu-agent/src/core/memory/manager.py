"""
描述: 记忆管理器 (Memory Manager)
主要功能:
    - 管理用户长期记忆 (User Memory) 和共享记忆 (Shared Memory)
    - 维护每日对话日志 (Daily Logs)
    - 提供记忆快照和向量检索支持
"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import typing
from datetime import datetime, timedelta
from pathlib import Path

from src.utils.filelock import FileLock
from src.utils.workspace import ensure_workspace, get_workspace_root


@dataclass
class MemorySnapshot:
    """记忆快照数据结构"""
    shared_memory: str
    user_memory: str
    recent_logs: str


# region 核心记忆管理器
class MemoryManager:
    """
    记忆管理器

    功能:
        - 统一管理基于文件系统的记忆存储
        - 集成向量检索 (Vector Memory)
        - 处理记忆的写入、读取和清理
    """
    def __init__(
        self,
        workspace_root: Path | None = None,
        retention_days: int = 30,
        lock_timeout: float = 5.0,
        max_context_tokens: int = 2000,
        vector_memory: typing.Any | None = None,
    ) -> None:
        """
        初始化记忆管理器

        参数:
            workspace_root: 工作区根路径
            retention_days: 日志保留天数
            lock_timeout: 文件锁超时时间
            max_context_tokens: 最大上下文 Token 限制
            vector_memory: 向量记忆实例
        """
        self._workspace_root = Path(workspace_root) if workspace_root else get_workspace_root()
        ensure_workspace(self._workspace_root)
        self._retention_days = retention_days
        self._lock_timeout = lock_timeout
        self._max_context_tokens = max_context_tokens
        self._vector_memory = vector_memory

    def load_shared_memory(self) -> str:
        """读取共享记忆"""
        path = self._workspace_root / "MEMORY.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def load_user_memory(self, user_id: str) -> str:
        """读取用户专属记忆"""
        path = self._user_dir(user_id) / "memory.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def load_recent_logs(self, user_id: str, days: int = 2) -> str:
        """
        读取最近 N 天的对话日志

        参数:
            user_id: 用户 ID
            days: 回溯天数

        返回:
            合并并截断后的日志文本
        """
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

    def append_daily_log(
        self,
        user_id: str,
        content: str,
        vectorize: bool = False,
        metadata: dict[str, typing.Any] | None = None,
    ) -> None:
        """
        追加每日日志

        参数:
            user_id: 用户 ID
            content: 日志内容
            vectorize: 是否同时写入向量库
            metadata: 向量元数据 (可选)
        """
        daily_dir = self._daily_dir(user_id)
        daily_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d")
        path = daily_dir / f"{date_str}.md"
        line = f"- {datetime.now().isoformat()} {content}\n"

        lock = FileLock(str(path) + ".lock", timeout=self._lock_timeout)
        with lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)

        if self._vector_memory and vectorize:
            vector_metadata = metadata or self._build_metadata(
                memory_type="auto",
                source="daily_log",
                tags=["daily_log"],
            )
            self._run_vector_task(
                self._vector_memory.add_memory(user_id, content, vector_metadata)
            )

    def remember_user(self, user_id: str, content: str) -> None:
        """
        写入用户长期记忆

        参数:
            user_id: 用户 ID
            content: 记忆内容
        """
        user_dir = self._user_dir(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        path = user_dir / "memory.md"
        line = f"- {content}\n"

        lock = FileLock(str(path) + ".lock", timeout=self._lock_timeout)
        with lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)

        if self._vector_memory:
            metadata = self._build_metadata(
                memory_type="user",
                source="user_memory",
                tags=["explicit"],
            )
            self._run_vector_task(self._vector_memory.add_memory(user_id, content, metadata))

    def snapshot(self, user_id: str, days: int = 2) -> MemorySnapshot:
        """获取当前上下文快照 (共享 + 用户 + 最近日志)"""
        return MemorySnapshot(
            shared_memory=self.load_shared_memory(),
            user_memory=self.load_user_memory(user_id),
            recent_logs=self.load_recent_logs(user_id, days=days),
        )

    async def search_memory(self, user_id: str, query: str, top_k: int = 5) -> str:
        """
        搜索相关记忆

        优先使用向量检索，失败则退化为关键词匹配
        """
        if not query:
            return ""

        if self._vector_memory:
            try:
                results = await self._vector_memory.search(user_id, query, top_k=top_k)
                if results:
                    return "\n".join(results)
            except Exception:
                pass

        combined = "\n".join([
            self.load_user_memory(user_id),
            self.load_recent_logs(user_id, days=2),
        ])
        return self._keyword_fallback(combined, query, top_k)

    @staticmethod
    def _run_vector_task(task: typing.Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            loop.create_task(task)
        else:
            asyncio.run(task)

    @staticmethod
    def _build_metadata(
        memory_type: str,
        source: str,
        tags: list[str] | None = None,
    ) -> dict[str, typing.Any]:
        tag_value = ",".join(tags) if tags else ""
        return {
            "type": memory_type,
            "created_at": datetime.now().isoformat(),
            "source": source,
            "tags": tag_value,
        }

    def cleanup_logs(self) -> int:
        """清理过期日志"""
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
    def _keyword_fallback(text: str, query: str, top_k: int) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        hits = [line for line in lines if query in line]
        if not hits:
            tokens = [t for t in query.split() if t]
            for token in tokens:
                hits.extend([line for line in lines if token in line])
        return "\n".join(hits[:top_k])

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
# endregion
