"""
Feishu Agent entrypoint.
"""

from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI

from src.api.health import router as health_router
from src.api.webhook import router as webhook_router
from src.config import get_settings
from src.utils.logger import setup_logging


load_dotenv()
settings = get_settings()
setup_logging(settings.logging)

app = FastAPI(title="Feishu Agent", version="0.1.0")
app.include_router(health_router)
app.include_router(webhook_router)
