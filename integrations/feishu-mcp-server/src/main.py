"""MCP service entrypoint (role-based app)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from src.server.app_factory import create_app


BASE_DIR = Path(__file__).resolve().parents[1]
os.environ.setdefault("CONFIG_PATH", str(BASE_DIR / "config.yaml"))
load_dotenv(BASE_DIR / ".env")
app = create_app()
