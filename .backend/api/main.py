"""
描述: FastAPI 应用入口
主要功能:
    - 初始化 FastAPI 实例
    - 注册 API 路由
依赖: fastapi
"""

from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI

from api.routes import router as api_router
from config.settings import get_settings

load_dotenv(override=True)

app = FastAPI(title="Universal RAG", version="0.1.0")
app.include_router(api_router)
app.state.settings = get_settings()
