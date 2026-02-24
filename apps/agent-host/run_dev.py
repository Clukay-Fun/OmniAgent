"""
描述: Agent 子目录开发入口代理。
主要功能:
    - 在 apps/agent-host 目录下提供一致的命令体验
    - 参数透传到仓库根 run_dev.py（唯一权威实现）
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    """向上查找仓库根目录。"""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "run_dev.py").exists() and (candidate / "deploy" / "docker" / "compose.yml").exists():
            return candidate
    raise RuntimeError("未找到仓库根目录（缺少 run_dev.py）")


def main() -> int:
    """执行代理入口。"""
    repo_root = _find_repo_root(Path(__file__).resolve().parent)
    target = repo_root / "run_dev.py"
    command = [sys.executable, str(target), *sys.argv[1:]]
    result = subprocess.run(command, cwd=str(repo_root), check=False)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
