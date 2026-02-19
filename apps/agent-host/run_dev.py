"""
描述: agent-host 开发入口兼容 shim。
主要功能:
    - 统一新入口路径为 apps/agent-host
    - 参数透传到 agent/feishu-agent/run_dev.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    """执行 agent-host 开发入口兼容代理。"""
    repo_root = Path(__file__).resolve().parents[2]
    target = repo_root / "agent" / "feishu-agent" / "run_dev.py"
    command = [sys.executable, str(target), *sys.argv[1:]]
    result = subprocess.run(command, cwd=str(repo_root), check=False)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
