from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.automation import AutomationPoller, AutomationService, AutomationValidationError
from src.config import Settings, get_settings
from src.feishu.client import FeishuAPIError, FeishuClient


router = APIRouter()

_feishu_client: FeishuClient | None = None
_automation_service: AutomationService | None = None
_automation_poller: AutomationPoller | None = None


def get_feishu_client(settings: Settings) -> FeishuClient:
    global _feishu_client
    if _feishu_client is None:
        _feishu_client = FeishuClient(settings)
    return _feishu_client


def get_automation_service(settings: Settings) -> AutomationService:
    global _automation_service
    if _automation_service is None:
        _automation_service = AutomationService(settings, get_feishu_client(settings))
    return _automation_service


def get_automation_poller(settings: Settings) -> AutomationPoller:
    global _automation_poller
    if _automation_poller is None:
        _automation_poller = AutomationPoller(
            service=get_automation_service(settings),
            enabled=bool(settings.automation.poller_enabled),
            interval_seconds=float(settings.automation.poller_interval_seconds),
        )
    return _automation_poller


async def start_automation_poller() -> None:
    settings = get_settings()
    if not settings.automation.enabled:
        return
    poller = get_automation_poller(settings)
    await poller.start()


async def stop_automation_poller() -> None:
    global _automation_poller
    if _automation_poller is None:
        return
    await _automation_poller.stop()


@router.post("/feishu/events")
async def feishu_events(payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    service = get_automation_service(settings)

    try:
        result = await service.handle_event(payload)
    except AutomationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FeishuAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if result.get("kind") == "challenge":
        return {"challenge": result.get("challenge")}
    return {
        "status": "ok",
        "result": result,
    }


@router.post("/automation/init")
async def automation_init(
    table_id: str | None = Query(default=None),
    app_token: str | None = Query(default=None),
) -> dict[str, Any]:
    settings = get_settings()
    service = get_automation_service(settings)
    try:
        return await service.init_snapshot(table_id=table_id, app_token=app_token)
    except AutomationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FeishuAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/automation/scan")
async def automation_scan(
    table_id: str | None = Query(default=None),
    app_token: str | None = Query(default=None),
) -> dict[str, Any]:
    settings = get_settings()
    service = get_automation_service(settings)
    try:
        return await service.scan_table(table_id=table_id, app_token=app_token)
    except AutomationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FeishuAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
