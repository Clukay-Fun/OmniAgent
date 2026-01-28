"""
MCP Feishu Server entrypoint.
"""

from __future__ import annotations

from fastapi import FastAPI
from dotenv import load_dotenv

from src.config import get_settings
from src.server.http import router as http_router
import src.tools  # noqa: F401
from src.utils.logger import setup_logging


load_dotenv()
settings = get_settings()
setup_logging(settings.logging)

app = FastAPI(title="MCP Feishu Server", version="0.1.0")
app.include_router(http_router)
