"""
描述: Soul & Identity 管理器
主要功能:
    - 加载 SOUL.md 和 IDENTITY.md 配置
    - 构建 LLM System Prompt
    - 支持配置文件的热重载
"""

from __future__ import annotations

import time
from pathlib import Path

from src.utils.runtime.workspace import ensure_workspace, get_workspace_root


# region Soul 管理器
class SoulManager:
    """
    Soul 管理器

    功能:
        - 统一管理 Agent 的人设 (Identity) 和灵魂 (Soul)
        - 定期自动重载配置，支持热更新
    """
    def __init__(self, workspace_root: Path | None = None, reload_interval: int = 60) -> None:
        """
        初始化管理器

        参数:
            workspace_root: 工作区路径
            reload_interval: 重载检查间隔 (秒)
        """
        self._workspace_root = Path(workspace_root) if workspace_root else get_workspace_root()
        ensure_workspace(self._workspace_root)

        app_root = Path(__file__).resolve().parents[4]
        config_identity_root = app_root / "config" / "identity"
        self._config_soul_path = config_identity_root / "SOUL.md"
        self._config_identity_path = config_identity_root / "IDENTITY.md"
        self._reload_interval = reload_interval
        self._last_load = 0.0
        self._soul_text = ""
        self._identity_text = ""
        self._load(force=True)

    def get_soul(self) -> str:
        """获取 SOUL 内容"""
        self._load()
        return self._soul_text

    def get_identity(self) -> str:
        """获取 IDENTITY 内容"""
        self._load()
        return self._identity_text

    def build_system_prompt(self) -> str:
        """构建完整的系统提示词 (Identity + Soul)"""
        self._load()
        parts = [self._identity_text.strip(), self._soul_text.strip()]
        return "\n\n".join([p for p in parts if p])

    def _load(self, force: bool = False) -> None:
        """加载配置文件 (带缓存检查)"""
        now = time.time()
        if not force and now - self._last_load < self._reload_interval:
            return

        self._soul_text = self._read_file(self._config_soul_path)
        self._identity_text = self._read_file(self._config_identity_path)
        self._last_load = now

    @staticmethod
    def _read_file(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")
# endregion
