"""
Feishu Agent entrypoint.

功能：
- FastAPI 应用初始化
- 配置热更新启动
- 优雅关闭处理
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI

from src.api.health import router as health_router
from src.api.metrics import router as metrics_router
from src.api.webhook import router as webhook_router, agent_core
from src.config import get_settings
from src.utils.logger import setup_logging
from src.utils.workspace import ensure_workspace
from src.utils.hot_reload import HotReloadManager

logger = logging.getLogger(__name__)


# ============================================
# region 初始化
# ============================================
load_dotenv()
settings = get_settings()
setup_logging(settings.logging)
ensure_workspace()

# 热更新管理器
hot_reload_manager = HotReloadManager()
# endregion
# ============================================


# ============================================
# region 生命周期管理
# ============================================
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    应用生命周期管理
    
    - 启动时：初始化热更新监控
    - 关闭时：优雅停止
    """
    # 启动配置热更新
    hot_reload_manager.add_watcher(
        config_path="config/skills.yaml",
        reload_callback=lambda cfg: agent_core.reload_config("config/skills.yaml"),
        interval_seconds=60,
    )
    hot_reload_manager.add_watcher(
        config_path="config/prompts.yaml",
        reload_callback=lambda cfg: logger.info("Prompts config reloaded"),
        interval_seconds=60,
    )
    hot_reload_manager.start_all()
    logger.info("Feishu Agent started with hot-reload enabled")
    
    yield
    
    # 关闭
    hot_reload_manager.stop_all()
    logger.info("Feishu Agent shutdown complete")
# endregion
# ============================================


# ============================================
# region FastAPI 应用
# ============================================
app = FastAPI(
    title="Feishu Agent",
    version="0.2.0",
    description="飞书私聊机器人 - 基于 Skill System 架构",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(metrics_router)
app.include_router(webhook_router)
# endregion
# ============================================
