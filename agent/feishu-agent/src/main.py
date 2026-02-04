"""
描述: Feishu Agent 主入口
主要功能:
    - FastAPI 应用初始化
    - 配置热更新 (Hot Reload) 启动
    - 数据库与定时任务调度
    - 优雅停止 (Graceful Shutdown)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI

from src.api.health import router as health_router
from src.api.metrics import router as metrics_router
from src.api.webhook import router as webhook_router, agent_core
from src.config import get_settings
from src.core.intent import load_skills_config
from src.utils.logger import setup_logging
from src.utils.workspace import ensure_workspace
from src.utils.hot_reload import HotReloadManager
from src.db.postgres import PostgresClient
from src.jobs.reminder_scheduler import ReminderScheduler

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


# region 生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    应用生命周期回调

    功能:
        - Startup: 启动热更新、连接数据库、初始化调度器
        - Shutdown: 停止任务、关闭连接
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

    skills_config = load_skills_config("config/skills.yaml")
    reminder_cfg = skills_config.get("reminder", {})
    if settings.postgres.dsn:
        db = PostgresClient(settings.postgres)
        interval_seconds = int(reminder_cfg.get("scan_interval_seconds", 60))
        lock_timeout_seconds = int(reminder_cfg.get("lock_timeout_seconds", 300))
        instance_id = os.getenv("OMNI_INSTANCE_ID", os.getenv("HOSTNAME", "instance"))
        app.state.reminder_scheduler = ReminderScheduler(
            settings=settings,
            db=db,
            interval_seconds=interval_seconds,
            instance_id=instance_id,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        app.state.reminder_scheduler.start()
    logger.info("Feishu Agent started with hot-reload enabled")
    
    yield
    
    # 关闭
    hot_reload_manager.stop_all()
    scheduler: ReminderScheduler | None = getattr(app.state, "reminder_scheduler", None)
    if scheduler is not None:
        await scheduler.stop()
    logger.info("Feishu Agent shutdown complete")
# endregion


# region FastAPI 应用实例
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
