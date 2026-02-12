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

# Windows 兼容性：在任何 asyncio 操作前设置策略
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

import uvicorn

if __name__ == "__main__":
    print("Starting MCP Feishu Server on http://0.0.0.0:8081")
    print("Press Ctrl+C to stop")
    uvicorn.run("src.main:app", host="0.0.0.0", port=8081, log_level="info")
