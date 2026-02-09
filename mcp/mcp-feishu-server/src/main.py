"""
描述: MCP Server 主入口
主要功能:
    - FastAPI 应用初始化
    - 路由注册 (MCP Protocol & HTTP)
    - 日志与配置加载
"""

from __future__ import annotations

from fastapi import FastAPI
from dotenv import load_dotenv

from src.config import get_settings
from src.server.automation import router as automation_router
from src.server.http import router as http_router
import src.tools  # noqa: F401
from src.utils.logger import setup_logging


# region 初始化
load_dotenv()
settings = get_settings()
setup_logging(settings.logging)
print(f"=== MCP Server Config ===")
print(f"App ID: {settings.feishu.app_id}")
print(f"Bitable App Token: {settings.bitable.default_app_token}")
print(f"Bitable Table ID: {settings.bitable.default_table_id}")
print(f"Bitable View ID: {settings.bitable.default_view_id}")
print(f"Calendar ID: {settings.calendar.default_calendar_id}")
print(f"=========================")
# endregion

# region FastAPI 应用
app = FastAPI(title="MCP Feishu Server", version="0.1.0")
app.include_router(http_router)
app.include_router(automation_router)
# endregion
