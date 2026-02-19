"""
描述: Feishu Agent 启动脚本 (兼容性模式)
主要功能:
    - 配置 asyncio 策略 (Windows)
    - 使用 waitress 启动 WSGI 服务
    - 监听 8088 端口
"""
import asyncio
import os
import subprocess
import sys

# Windows 兼容性：在任何 asyncio 操作前设置策略
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from a2wsgi import ASGIMiddleware
from waitress import serve
from src.main import app


def run_scenario_precheck() -> bool:
    """可选场景预检查（生产/兼容启动模式）。"""
    enabled = os.getenv("AGENT_SCENARIO_CHECK", "0").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return True

    print("🧪 运行场景预检查: tests/scenarios/runner.py")
    extra_args = os.getenv("AGENT_SCENARIO_CHECK_ARGS", "").strip()
    command = [sys.executable, "tests/scenarios/runner.py"]
    if extra_args:
        command.extend(extra_args.split())

    result = subprocess.run(
        command,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        check=False,
    )
    if result.returncode != 0:
        print("❌ 场景预检查失败，已停止启动。")
        return False
    print("✅ 场景预检查通过。")
    return True

# 将 ASGI 应用转换为 WSGI
wsgi_app = ASGIMiddleware(app)

if __name__ == "__main__":
    if not run_scenario_precheck():
        raise SystemExit(1)
    print("Starting Feishu Agent on http://0.0.0.0:8088")
    print("Press Ctrl+C to stop")
    serve(wsgi_app, host="0.0.0.0", port=8088, threads=4)
