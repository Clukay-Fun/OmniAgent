"""Application factory for role-based MCP runtime."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI

from src.config import Settings, check_tool_config_consistency, get_settings
from src.utils.logger import setup_logging


ROLE_MCP_SERVER = "mcp_server"
ROLE_AUTOMATION_WORKER = "automation_worker"
SUPPORTED_ROLES = {ROLE_MCP_SERVER, ROLE_AUTOMATION_WORKER}


def resolve_runtime_role() -> str:
    """Resolve runtime role from environment."""
    role = str(os.getenv("ROLE", ROLE_MCP_SERVER)).strip().lower()
    if role not in SUPPORTED_ROLES:
        supported = ", ".join(sorted(SUPPORTED_ROLES))
        raise RuntimeError(f"Unsupported ROLE='{role}', expected one of: {supported}")
    return role


def _log_tool_consistency(settings: Settings, logger: logging.Logger) -> None:
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


def _build_mcp_server_app(settings: Settings, logger: logging.Logger) -> FastAPI:
    # Import tool registry only for mcp_server role.
    import src.tools  # noqa: F401
    from src.server.mcp import router as mcp_router

    _log_tool_consistency(settings, logger)

    logger.info(
        "MCP server config loaded",
        extra={
            "role": ROLE_MCP_SERVER,
            "tools_enabled_count": len(settings.tools.enabled),
        },
    )

    app = FastAPI(title="MCP Feishu Server", version="0.2.0")
    app.include_router(mcp_router)
    return app


def _build_automation_worker_app(settings: Settings, logger: logging.Logger) -> FastAPI:
    # Import automation stack only for automation_worker role.
    from src.server.automation import (
        router as automation_router,
        start_automation_poller,
        stop_automation_poller,
    )

    logger.info(
        "Automation worker config loaded",
        extra={
            "role": ROLE_AUTOMATION_WORKER,
            "automation_enabled": bool(settings.automation.enabled),
            "schema_sync_enabled": bool(settings.automation.schema_sync_enabled),
        },
    )

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        await start_automation_poller()
        try:
            yield
        finally:
            await stop_automation_poller()

    app = FastAPI(title="MCP Feishu Automation Worker", version="0.2.0", lifespan=_lifespan)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"status": "ok", "service": "automation-worker"}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": ROLE_AUTOMATION_WORKER}

    app.include_router(automation_router)
    return app


def create_app() -> FastAPI:
    """Create FastAPI application by runtime role."""
    settings = get_settings()
    setup_logging(settings.logging)
    logger = logging.getLogger(__name__)

    role = resolve_runtime_role()
    if role == ROLE_MCP_SERVER:
        return _build_mcp_server_app(settings, logger)
    return _build_automation_worker_app(settings, logger)
