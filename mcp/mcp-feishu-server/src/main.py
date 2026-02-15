"""
描述: MCP Server 主入口
主要功能:
    - FastAPI 应用初始化
    - 路由注册 (MCP Protocol & HTTP)
    - 日志与配置加载
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from dotenv import load_dotenv

from src.config import check_tool_config_consistency, get_settings
from src.server.automation import (
    router as automation_router,
    start_automation_poller,
    stop_automation_poller,
)
from src.server.http import router as http_router
import src.tools  # noqa: F401
from src.utils.logger import setup_logging


# region 初始化
load_dotenv()
settings = get_settings()
setup_logging(settings.logging)
logger = logging.getLogger(__name__)

tool_consistency = check_tool_config_consistency(settings)
if tool_consistency["runtime_missing"]:
    missing = ", ".join(tool_consistency["runtime_missing"])
    raise RuntimeError(
        f"MCP runtime config missing required tools: {missing} "
        f"(config: {tool_consistency['runtime_config_path']})"
    )
if tool_consistency["example_exists"] and tool_consistency["example_missing"]:
    logger.warning(
        "MCP example config missing required tools: %s (config: %s)",
        ", ".join(tool_consistency["example_missing"]),
        tool_consistency["example_config_path"],
    )

logger.info(
    "MCP server config loaded",
    extra={
        "automation_enabled": bool(settings.automation.enabled),
        "schema_sync_enabled": bool(settings.automation.schema_sync_enabled),
        "tools_enabled_count": len(settings.tools.enabled),
    },
)
# endregion

# region FastAPI 应用
@asynccontextmanager
async def _lifespan(_: FastAPI):
    await start_automation_poller()
    try:
        yield
    finally:
        await stop_automation_poller()


app = FastAPI(title="MCP Feishu Server", version="0.1.0", lifespan=_lifespan)
app.include_router(http_router)
app.include_router(automation_router)
# endregion
