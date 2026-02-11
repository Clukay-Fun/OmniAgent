from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.automation import (
    AutomationPoller,
    AutomationService,
    AutomationValidationError,
    SchemaPoller,
)
from src.config import Settings, get_settings
from src.feishu.client import FeishuAPIError, FeishuClient


router = APIRouter()

_feishu_client: FeishuClient | None = None
_automation_service: AutomationService | None = None
_automation_poller: AutomationPoller | None = None
_schema_poller: SchemaPoller | None = None


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


def get_schema_poller(settings: Settings) -> SchemaPoller:
    global _schema_poller
    if _schema_poller is None:
        _schema_poller = SchemaPoller(
            service=get_automation_service(settings),
            enabled=bool(settings.automation.schema_sync_enabled),
            interval_seconds=float(settings.automation.schema_sync_interval_seconds),
        )
    return _schema_poller


async def start_automation_poller() -> None:
    settings = get_settings()
    if not settings.automation.enabled:
        return
    poller = get_automation_poller(settings)
    await poller.start()
    schema_poller = get_schema_poller(settings)
    await schema_poller.start()


async def stop_automation_poller() -> None:
    global _automation_poller
    global _schema_poller
    if _automation_poller is None:
        pass
    else:
        await _automation_poller.stop()
        _automation_poller = None

    if _schema_poller is None:
        return
    await _schema_poller.stop()
    _schema_poller = None


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


@router.post("/automation/schema/refresh")
async def automation_schema_refresh(
    table_id: str | None = Query(default=None),
    app_token: str | None = Query(default=None),
    drill: bool = Query(default=False),
) -> dict[str, Any]:
    settings = get_settings()
    service = get_automation_service(settings)
    try:
        if drill and not bool(settings.automation.schema_webhook_drill_enabled):
            raise AutomationValidationError(
                "schema webhook drill is disabled, set AUTOMATION_SCHEMA_WEBHOOK_DRILL_ENABLED=true"
            )

        if table_id:
            resolved_table_id = str(table_id).strip()
            resolved_app_token = str(app_token or settings.bitable.default_app_token or "").strip()
            if not resolved_app_token:
                raise AutomationValidationError("app_token required when default app_token is empty")
            refresh_result = await service.refresh_schema_table(
                table_id=resolved_table_id,
                app_token=resolved_app_token,
                triggered_by="manual_api",
            )

            if not drill:
                return refresh_result

            drill_result = await service.trigger_schema_webhook_drill(
                table_id=resolved_table_id,
                app_token=resolved_app_token,
                triggered_by="manual_api",
            )
            return {
                "refresh": refresh_result,
                "drill": drill_result,
            }

        if drill:
            raise AutomationValidationError("drill requires table_id to avoid bulk webhook push")

        return await service.refresh_schema_once_all_tables(triggered_by="manual_api")
    except AutomationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FeishuAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
