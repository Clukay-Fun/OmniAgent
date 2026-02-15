"""
描述: 仓库根目录统一开发入口代理。
主要功能:
    - 从根目录一键启动/停止 OmniAgent 开发栈
    - 参数透传到 agent/feishu-agent/run_dev.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    """执行根目录代理入口。"""
    repo_root = Path(__file__).resolve().parent
    target = repo_root / "agent" / "feishu-agent" / "run_dev.py"
    command = [sys.executable, str(target), *sys.argv[1:]]
    result = subprocess.run(command, cwd=str(repo_root), check=False)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
