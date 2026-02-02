from datetime import datetime

from src.core.memory import MemoryManager


def test_memory_manager_snapshot_and_cleanup(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    manager = MemoryManager(workspace_root=workspace, retention_days=1, max_context_tokens=50)

    manager.remember_user("u1", "喜欢简洁回复")
    manager.append_daily_log("u1", "用户: 你好")
    manager.append_daily_log("u1", "助手: 你好")

    snapshot = manager.snapshot("u1", days=2)
    assert "喜欢简洁回复" in snapshot.user_memory
    assert "用户:" in snapshot.recent_logs

    old_dir = workspace / "users" / "u1" / "daily"
    old_dir.mkdir(parents=True, exist_ok=True)
    old_file = old_dir / "2020-01-01.md"
    old_file.write_text("- old log", encoding="utf-8")

    removed = manager.cleanup_logs()
    assert removed >= 1


def test_memory_manager_truncate(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    manager = MemoryManager(workspace_root=workspace, max_context_tokens=10)
    manager.append_daily_log("u2", "a b c d e f g h i j k l")

    recent = manager.load_recent_logs("u2", days=1)
    assert len(recent) > 0
