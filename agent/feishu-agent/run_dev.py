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


LEGACY_CONTAINER_NAMES = [
    "omniagent-mcp-feishu",
    "omniagent-feishu-agent",
    "omniagent-postgres",
    "omniagent-prometheus",
    "omniagent-grafana",
]


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


def _profile_flags(args: argparse.Namespace, include_all: bool = False) -> list[str]:
    """根据参数生成 compose profile 参数。"""
    enable_monitoring = bool(include_all or args.all or args.with_monitoring)
    enable_db = bool(include_all or args.all or args.with_db)

    profiles: list[str] = []
    if enable_monitoring:
        profiles.extend(["--profile", "monitoring"])
    if enable_db:
        profiles.extend(["--profile", "db"])
    return profiles


def _run_command(args: list[str], repo_root: Path) -> int:
    """执行命令并返回退出码。"""
    print("$", " ".join(args))
    result = subprocess.run(args, cwd=str(repo_root), check=False)
    return int(result.returncode)


def _cleanup_legacy_containers(repo_root: Path) -> int:
    """清理旧版固定容器名，避免历史冲突。"""
    cmd = ["docker", "rm", "-f", *LEGACY_CONTAINER_NAMES]
    print("$", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(repo_root), check=False)
    if result.returncode != 0:
        print("[warn] 清理旧容器时有非致命错误，可忽略（通常是容器不存在）")
    return 0


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="OmniAgent 开发栈统一入口")
    parser.add_argument(
        "action",
        nargs="?",
        default="up",
        choices=["up", "down", "restart", "logs", "ps", "clean"],
        help="执行动作，默认 up",
    )
    parser.add_argument("--build", action="store_true", help="up/restart 时强制重建镜像")
    parser.add_argument("--no-daemon", action="store_true", help="up 时前台运行")
    parser.add_argument("--follow", action="store_true", help="logs 时持续追踪")
    parser.add_argument("--service", default="", help="logs/ps 时指定服务名")
    parser.add_argument("--with-monitoring", action="store_true", help="启用 monitoring profile")
    parser.add_argument("--with-db", action="store_true", help="启用 db profile")
    parser.add_argument("--all", action="store_true", help="等价于 --with-monitoring --with-db")
    return parser.parse_args()


def main() -> int:
    """统一开发入口主函数。"""
    script_path = Path(__file__).resolve()
    repo_root = _find_repo_root(script_path.parent)
    base = _compose_base_args(repo_root)

    args = _parse_args()
    action = str(args.action)
    profile_flags = _profile_flags(args)

    if action == "up":
        cmd = [*base, *profile_flags, "up"]
        if not bool(args.no_daemon):
            cmd.append("-d")
        if bool(args.build):
            cmd.append("--build")
        return _run_command(cmd, repo_root)

    if action == "down":
        down_profile_flags = _profile_flags(args, include_all=True)
        return _run_command([*base, *down_profile_flags, "down", "--remove-orphans"], repo_root)

    if action == "restart":
        down_profile_flags = _profile_flags(args, include_all=True)
        down_code = _run_command([*base, *down_profile_flags, "down", "--remove-orphans"], repo_root)
        if down_code != 0:
            return down_code
        up_cmd = [*base, *profile_flags, "up", "-d"]
        if bool(args.build):
            up_cmd.append("--build")
        return _run_command(up_cmd, repo_root)

    if action == "clean":
        _cleanup_legacy_containers(repo_root)
        down_profile_flags = _profile_flags(args, include_all=True)
        return _run_command([*base, *down_profile_flags, "down", "--remove-orphans"], repo_root)

    if action == "logs":
        cmd = [*base, *profile_flags, "logs"]
        if bool(args.follow):
            cmd.append("-f")
        service = str(args.service).strip()
        if service:
            cmd.append(service)
        return _run_command(cmd, repo_root)

    if action == "ps":
        cmd = [*base, *profile_flags, "ps"]
        service = str(args.service).strip()
        if service:
            cmd.append(service)
        return _run_command(cmd, repo_root)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
