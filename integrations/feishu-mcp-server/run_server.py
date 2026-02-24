"""
描述: MCP Server 启动脚本
主要功能:
    - 配置 asyncio 策略 (Windows)
    - 使用 uvicorn 启动 ASGI 服务
    - 监听 8081 端口（支持 FastAPI lifespan）
"""
import asyncio
import os
import sys
from pathlib import Path

# Windows 兼容性：在任何 asyncio 操作前设置策略
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 添加项目路径
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# 默认使用 MCP 目录下配置，避免在仓库根目录启动时误读根 config.yaml
os.environ.setdefault("CONFIG_PATH", str(BASE_DIR / "config.yaml"))

from dotenv import load_dotenv

load_dotenv(BASE_DIR / ".env")

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("MCP_PORT", "8081"))
    print(f"Starting MCP Feishu Server on http://0.0.0.0:{port}")
    print("Press Ctrl+C to stop")
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, log_level="info")
