"""
使用 waitress 启动服务（Windows 兼容方案）

用法：python run_server.py
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

from a2wsgi import ASGIMiddleware
from waitress import serve
from src.main import app

# 将 ASGI 应用转换为 WSGI
wsgi_app = ASGIMiddleware(app)

if __name__ == "__main__":
    print("Starting Feishu Agent on http://0.0.0.0:8088")
    print("Press Ctrl+C to stop")
    serve(wsgi_app, host="0.0.0.0", port=8088, threads=4)
