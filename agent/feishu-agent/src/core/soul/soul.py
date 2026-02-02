"""Soul prompt loader with lightweight hot-reload."""

from __future__ import annotations

import time
from pathlib import Path

from src.utils.workspace import ensure_workspace, get_workspace_root


class SoulManager:
    def __init__(self, workspace_root: Path | None = None, reload_interval: int = 60) -> None:
        self._workspace_root = Path(workspace_root) if workspace_root else get_workspace_root()
        ensure_workspace(self._workspace_root)

        self._soul_path = self._workspace_root / "SOUL.md"
        self._identity_path = self._workspace_root / "IDENTITY.md"
        self._reload_interval = reload_interval
        self._last_load = 0.0
        self._soul_text = ""
        self._identity_text = ""
        self._load(force=True)

    def get_soul(self) -> str:
        self._load()
        return self._soul_text

    def get_identity(self) -> str:
        self._load()
        return self._identity_text

    def build_system_prompt(self) -> str:
        self._load()
        parts = [self._identity_text.strip(), self._soul_text.strip()]
        return "\n\n".join([p for p in parts if p])

    def _load(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_load < self._reload_interval:
            return

        self._soul_text = self._read_file(self._soul_path)
        self._identity_text = self._read_file(self._identity_path)
        self._last_load = now

    @staticmethod
    def _read_file(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")
