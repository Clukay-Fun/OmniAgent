"""
描述: OmniAgent 统一开发入口脚本。
主要功能:
    - 统一启动 MCP + Feishu Agent 的开发态容器
    - 统一查看日志、停止、重启开发栈
    - 统一使用 deploy/docker/compose.yml 与 compose.dev.yml
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
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
    try:
        result = subprocess.run(args, cwd=str(repo_root), check=False)
        return int(result.returncode)
    except KeyboardInterrupt:
        return 130  # 标准 Ctrl+C 退出码


def _run_agent_ws(repo_root: Path) -> int:
    """启动一次飞书长连接客户端。"""
    agent_root = repo_root / "apps" / "agent-host"
    ws_cmd = [sys.executable, "-m", "src.api.ws_client"]
    return _run_command(ws_cmd, agent_root)


def _run_agent_ws_watch(repo_root: Path) -> int:
    """监听代码变更并自动重启长连接客户端。"""
    try:
        from watchfiles import watch
    except Exception:
        print("[error] 未安装 watchfiles，无法使用 agent-ws-watch")
        print("请先执行: pip install watchfiles")
        return 1

    agent_root = repo_root / "apps" / "agent-host"
    watch_targets = [
        agent_root / "src",
        agent_root / "config",
        agent_root / "workspace",
    ]

    for target in watch_targets:
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)

    def _spawn() -> subprocess.Popen[str]:
        command = [sys.executable, "-m", "src.api.ws_client"]
        print("$", " ".join(command))
        return subprocess.Popen(command, cwd=str(agent_root), text=True)

    def _stop(proc: subprocess.Popen[str]) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)

    process = _spawn()
    print("[info] agent-ws-watch 已启动，监听 src/config/workspace 变更")
    print("[info] 按 Ctrl+C 停止")
    try:
        for changes in watch(*[str(p) for p in watch_targets], debounce=500):
            if not changes:
                continue
            print(f"[info] 检测到 {len(changes)} 处变更，重启长连接客户端")
            _stop(process)
            process = _spawn()
    except KeyboardInterrupt:
        print("[info] 已停止 agent-ws-watch")
    finally:
        _stop(process)

    return 0


def _cleanup_legacy_containers(repo_root: Path) -> int:
    """清理旧版固定容器名，避免历史冲突。"""
    cmd = ["docker", "rm", "-f", *LEGACY_CONTAINER_NAMES]
    print("$", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(repo_root), check=False)
    if result.returncode != 0:
        print("[warn] 清理旧容器时有非致命错误，可忽略（通常是容器不存在）")
    return 0


def _refresh_schema(
    endpoint: str,
    table_id: str,
    app_token: str,
    drill: bool,
) -> int:
    """调用 MCP 手动 schema 刷新接口。"""
    base = endpoint.rstrip("/")
    query: dict[str, str] = {}
    if table_id:
        query["table_id"] = table_id
    if app_token:
        query["app_token"] = app_token
    if drill:
        query["drill"] = "true"

    url = f"{base}/automation/schema/refresh"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"

    request = urllib.request.Request(url=url, method="POST", data=b"")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8")
            try:
                parsed = json.loads(body)
                print(json.dumps(parsed, ensure_ascii=False, indent=2))
            except json.JSONDecodeError:
                print(body)
        return 0
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="ignore")
        print(f"[error] refresh schema failed: HTTP {exc.code} {payload}")
        return 1
    except Exception as exc:
        print(f"[error] refresh schema failed: {exc}")
        return 1


def _scan_automation(endpoint: str, table_id: str, app_token: str, route: str = "/automation/scan") -> int:
    """调用 MCP 手动补偿扫描接口。"""
    base = endpoint.rstrip("/")
    query: dict[str, str] = {}
    if table_id:
        query["table_id"] = table_id
    if app_token:
        query["app_token"] = app_token

    route_path = route if route.startswith("/") else f"/{route}"
    url = f"{base}{route_path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"

    request = urllib.request.Request(url=url, method="POST", data=b"")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8")
            try:
                parsed = json.loads(body)
                print(json.dumps(parsed, ensure_ascii=False, indent=2))
            except json.JSONDecodeError:
                print(body)
        return 0
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="ignore")
        print(f"[error] automation scan failed: HTTP {exc.code} {payload}")
        return 1
    except Exception as exc:
        print(f"[error] automation scan failed: {exc}")
        return 1


def _auth_health(endpoint: str) -> int:
    """调用 MCP 鉴权健康检查接口。"""
    base = endpoint.rstrip("/")
    url = f"{base}/automation/auth/health"

    request = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
            parsed = json.loads(body)
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
            if str(parsed.get("status") or "") != "ok":
                return 1
            result = parsed.get("result")
            if isinstance(result, dict) and str(result.get("status") or "") != "ok":
                return 1
        return 0
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="ignore")
        print(f"[error] auth health failed: HTTP {exc.code} {payload}")
        return 1
    except Exception as exc:
        print(f"[error] auth health failed: {exc}")
        return 1


def _run_card_preview(repo_root: Path, args: argparse.Namespace) -> int:
    """运行卡片 payload 预览工具。"""
    script_path = repo_root / "tools" / "dev" / "preview_card_payload.py"
    cmd = [sys.executable, str(script_path)]

    default_preset = "t2"
    preset = str(getattr(args, "preset", "") or "").strip()
    template_id = str(getattr(args, "template_id", "") or "").strip()
    template_version = str(getattr(args, "template_version", "") or "").strip()
    domain = str(getattr(args, "domain", "") or "").strip()
    style = str(getattr(args, "style", "") or "").strip()
    total = int(getattr(args, "total", 1) or 1)
    mock_file = str(getattr(args, "mock_file", "") or "").strip()
    send_chat_id = str(getattr(args, "send_chat_id", "") or "").strip()
    send_open_id = str(getattr(args, "send_open_id", "") or "").strip()
    send_user_id = str(getattr(args, "send_user_id", "") or "").strip()
    send_to_me = bool(getattr(args, "send_to_me", False))
    send_last_user = bool(getattr(args, "send_last_user", False))

    if (
        not preset
        and not template_id
        and not template_version
        and not mock_file
        and not domain
        and not style
        and int(total) == 1
    ):
        preset = default_preset

    if preset:
        cmd.extend(["--preset", preset])
    if template_id:
        cmd.extend(["--template-id", template_id])
    if template_version:
        cmd.extend(["--template-version", template_version])
    if domain:
        cmd.extend(["--domain", domain])
    if style:
        cmd.extend(["--style", style])
    cmd.extend(["--total", str(total)])
    if mock_file:
        cmd.extend(["--mock-file", mock_file])
    if send_chat_id:
        cmd.extend(["--send-chat-id", send_chat_id])
    if send_open_id:
        cmd.extend(["--send-open-id", send_open_id])
    if send_user_id:
        cmd.extend(["--send-user-id", send_user_id])
    if send_to_me:
        cmd.append("--send-to-me")
    if send_last_user:
        cmd.append("--send-last-user")

    return _run_command(cmd, repo_root)


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="OmniAgent 开发栈统一入口")
    parser.add_argument(
        "action",
        nargs="?",
        default="up",
        choices=[
            "up",
            "down",
            "restart",
            "logs",
            "ps",
            "clean",
            "agent-ws",
            "agent-ws-watch",
            "card-preview",
            "refresh-schema",
            "scan",
            "sync",
            "auth-health",
        ],
        help="执行动作，默认 up",
    )
    parser.add_argument("--build", action="store_true", help="up/restart 时强制重建镜像")
    parser.add_argument("--no-daemon", action="store_true", help="up 时前台运行")
    parser.add_argument("--follow", action="store_true", help="logs 时持续追踪")
    parser.add_argument("--service", default="", help="logs/ps 时指定服务名")
    parser.add_argument("--with-monitoring", action="store_true", help="启用 monitoring profile")
    parser.add_argument("--with-db", action="store_true", help="启用 db profile")
    parser.add_argument("--all", action="store_true", help="等价于 --with-monitoring --with-db")
    parser.add_argument("--endpoint", default="http://localhost:8081", help="MCP 地址（refresh-schema/scan/sync 使用）")
    parser.add_argument("--table-id", default="", help="指定目标表 ID（refresh-schema/scan 使用）")
    parser.add_argument("--app-token", default="", help="指定 app_token（refresh-schema/scan 使用）")
    parser.add_argument("--drill", action="store_true", help="refresh-schema 时触发风险演练")
    parser.add_argument("--preset", default="", help="card-preview 预设（如 t1/t2/c1/c2/c3/all）")
    parser.add_argument("--template-id", default="", help="card-preview 模板 ID")
    parser.add_argument("--template-version", default="", help="card-preview 模板版本")
    parser.add_argument("--domain", default="", help="card-preview 查询域")
    parser.add_argument("--style", default="", help="card-preview 样式")
    parser.add_argument("--total", type=int, default=1, help="card-preview 模拟记录数")
    parser.add_argument("--mock-file", default="", help="card-preview mock JSON 文件")
    parser.add_argument("--send-chat-id", default="", help="card-preview 发送目标 chat_id")
    parser.add_argument("--send-open-id", default="", help="card-preview 发送目标 open_id")
    parser.add_argument("--send-user-id", default="", help="card-preview 发送目标 user_id")
    parser.add_argument("--send-to-me", action="store_true", help="card-preview 发送到 FEISHU_PREVIEW_OPEN_ID")
    parser.add_argument("--send-last-user", action="store_true", help="card-preview 自动发送给最近对话用户")
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

    if action == "agent-ws":
        return _run_agent_ws(repo_root)

    if action == "agent-ws-watch":
        return _run_agent_ws_watch(repo_root)

    if action == "card-preview":
        return _run_card_preview(repo_root, args)

    if action == "refresh-schema":
        table_id = str(args.table_id or "").strip()
        app_token = str(args.app_token or "").strip()
        return _refresh_schema(
            endpoint=str(args.endpoint),
            table_id=table_id,
            app_token=app_token,
            drill=bool(args.drill),
        )

    if action in {"scan", "sync"}:
        table_id = str(args.table_id or "").strip()
        app_token = str(args.app_token or "").strip()
        route = "/automation/scan"
        if action == "sync":
            table_id = ""
            app_token = ""
            route = "/automation/sync"
        return _scan_automation(
            endpoint=str(args.endpoint),
            table_id=table_id,
            app_token=app_token,
            route=route,
        )

    if action == "auth-health":
        return _auth_health(endpoint=str(args.endpoint))

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
