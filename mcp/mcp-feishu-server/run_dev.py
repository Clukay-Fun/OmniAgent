"""
描述: MCP 统一开发入口代理脚本。
主要功能:
    - 复用 Agent 侧统一开发入口能力
    - 在 MCP 目录下提供一致的 up/down/logs/ps 体验
    - 将参数透传到 agent/feishu-agent/run_dev.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    """向上查找仓库根目录。"""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "agent" / "feishu-agent" / "run_dev.py").exists():
            return candidate
    raise RuntimeError("未找到仓库根目录（缺少 agent/feishu-agent/run_dev.py）")


def main() -> int:
    """执行代理入口。"""
    repo_root = _find_repo_root(Path(__file__).resolve().parent)
    target = repo_root / "agent" / "feishu-agent" / "run_dev.py"
    command = [sys.executable, str(target), *sys.argv[1:]]
    result = subprocess.run(command, cwd=str(repo_root), check=False)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
