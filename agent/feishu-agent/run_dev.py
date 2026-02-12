"""
描述: OmniAgent 统一开发入口脚本。
主要功能:
    - 统一启动 MCP + Feishu Agent 的开发态容器
    - 统一查看日志、停止、重启开发栈
    - 统一使用 deploy/docker/compose.yml 与 compose.dev.yml
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    """向上查找仓库根目录。"""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "deploy" / "docker" / "compose.yml").exists():
            return candidate
    raise RuntimeError("未找到仓库根目录（缺少 deploy/docker/compose.yml）")


def _compose_base_args(repo_root: Path) -> list[str]:
    """构建统一 compose 基础参数。"""
    return [
        "docker",
        "compose",
        "-p",
        "omniagent-dev",
        "-f",
        str(repo_root / "deploy" / "docker" / "compose.yml"),
        "-f",
        str(repo_root / "deploy" / "docker" / "compose.dev.yml"),
    ]


def _run_command(args: list[str], repo_root: Path) -> int:
    """执行命令并返回退出码。"""
    print("$", " ".join(args))
    result = subprocess.run(args, cwd=str(repo_root), check=False)
    return int(result.returncode)


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="OmniAgent 开发栈统一入口")
    parser.add_argument(
        "action",
        nargs="?",
        default="up",
        choices=["up", "down", "restart", "logs", "ps"],
        help="执行动作，默认 up",
    )
    parser.add_argument("--build", action="store_true", help="up/restart 时强制重建镜像")
    parser.add_argument("--no-daemon", action="store_true", help="up 时前台运行")
    parser.add_argument("--follow", action="store_true", help="logs 时持续追踪")
    parser.add_argument("--service", default="", help="logs/ps 时指定服务名")
    return parser.parse_args()


def main() -> int:
    """统一开发入口主函数。"""
    script_path = Path(__file__).resolve()
    repo_root = _find_repo_root(script_path.parent)
    base = _compose_base_args(repo_root)

    args = _parse_args()
    action = str(args.action)

    if action == "up":
        cmd = [*base, "up"]
        if not bool(args.no_daemon):
            cmd.append("-d")
        if bool(args.build):
            cmd.append("--build")
        return _run_command(cmd, repo_root)

    if action == "down":
        return _run_command([*base, "down"], repo_root)

    if action == "restart":
        down_code = _run_command([*base, "down"], repo_root)
        if down_code != 0:
            return down_code
        up_cmd = [*base, "up", "-d"]
        if bool(args.build):
            up_cmd.append("--build")
        return _run_command(up_cmd, repo_root)

    if action == "logs":
        cmd = [*base, "logs"]
        if bool(args.follow):
            cmd.append("-f")
        service = str(args.service).strip()
        if service:
            cmd.append(service)
        return _run_command(cmd, repo_root)

    if action == "ps":
        cmd = [*base, "ps"]
        service = str(args.service).strip()
        if service:
            cmd.append(service)
        return _run_command(cmd, repo_root)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
