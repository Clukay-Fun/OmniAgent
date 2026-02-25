"""
描述: 自动化 HTTP 路由与生命周期管理。
主要功能:
    - 暴露事件接收、初始化扫描、schema 刷新接口
    - 管理自动化轮询器与 schema 轮询器启停
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from src.automation import (
    DelayScheduler,
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
_delay_scheduler: DelayScheduler | None = None


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
            enabled=bool(settings.automation.schema_sync_enabled and settings.automation.schema_poller_enabled),
            interval_seconds=float(settings.automation.schema_sync_interval_seconds),
        )
    return _schema_poller


def get_delay_scheduler(settings: Settings) -> DelayScheduler:
    global _delay_scheduler
    if _delay_scheduler is None:
        _delay_scheduler = DelayScheduler(
            service=get_automation_service(settings),
            enabled=bool(settings.automation.enabled and settings.automation.delay_scheduler_enabled),
            interval_seconds=float(settings.automation.delay_scheduler_interval_seconds),
            cleanup_retention_seconds=float(settings.automation.delay_task_retention_seconds),
        )
    return _delay_scheduler


async def start_automation_poller() -> None:
    settings = get_settings()
    if not settings.automation.enabled:
        return
    poller = get_automation_poller(settings)
    await poller.start()
    schema_poller = get_schema_poller(settings)
    await schema_poller.start()
    delay_scheduler = get_delay_scheduler(settings)
    await delay_scheduler.start()


async def stop_automation_poller() -> None:
    global _automation_poller
    global _schema_poller
    global _delay_scheduler
    if _automation_poller is None:
        pass
    else:
        await _automation_poller.stop()
        _automation_poller = None

    if _schema_poller is None:
        pass
    else:
        await _schema_poller.stop()
        _schema_poller = None

    if _delay_scheduler is None:
        return
    await _delay_scheduler.stop()
    _delay_scheduler = None


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
        if not str(table_id or "").strip() and not str(app_token or "").strip():
            return await service.scan_once_all_tables()
        return await service.scan_table(table_id=table_id, app_token=app_token)
    except AutomationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FeishuAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/automation/sync")
async def automation_sync(
    table_id: str | None = Query(default=None),
    app_token: str | None = Query(default=None),
) -> dict[str, Any]:
    settings = get_settings()
    service = get_automation_service(settings)
    try:
        if not str(table_id or "").strip() and not str(app_token or "").strip():
            return await service.sync_once_all_tables()
        return await service.sync_table(table_id=table_id, app_token=app_token)
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


@router.get("/automation/auth/health")
async def automation_auth_health() -> dict[str, Any]:
    settings = get_settings()
    client = get_feishu_client(settings)
    result = await client.auth_health()
    return {
        "status": result.get("status"),
        "result": result,
        "automation_enabled": bool(settings.automation.enabled),
        "api_base": str(settings.feishu.api_base or ""),
    }


@router.get("/automation/delay/tasks")
async def automation_delay_tasks(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    settings = get_settings()
    service = get_automation_service(settings)
    raw_body = await request.body()
    headers = {str(key).lower(): str(value) for key, value in request.headers.items()}
    try:
        service.verify_management_auth(headers, raw_body)
    except AutomationValidationError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    try:
        items = service.list_delay_tasks(status=status, limit=limit)
    except AutomationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "status": "ok",
        "count": len(items),
        "items": items,
    }


@router.post("/automation/delay/{task_id}/cancel")
async def automation_delay_cancel(task_id: str, request: Request) -> dict[str, Any]:
    settings = get_settings()
    service = get_automation_service(settings)
    raw_body = await request.body()
    headers = {str(key).lower(): str(value) for key, value in request.headers.items()}

    try:
        service.verify_management_auth(headers, raw_body)
    except AutomationValidationError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    try:
        result = service.cancel_delay_task(task_id)
    except AutomationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"delay task not found: {task_id}")
    return {
        "status": "ok",
        "result": result,
    }


@router.post("/automation/webhook/{rule_id}")
async def automation_webhook_trigger(
    rule_id: str,
    request: Request,
    force: bool = Query(default=False),
) -> dict[str, Any]:
    settings = get_settings()
    service = get_automation_service(settings)

    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid json payload: {exc}")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="webhook payload must be object")

    headers = {str(key).lower(): str(value) for key, value in request.headers.items()}

    try:
        return await service.trigger_rule_webhook(
            rule_id=rule_id,
            payload=payload,
            headers=headers,
            raw_body=raw_body,
            force=bool(force),
        )
    except AutomationValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FeishuAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
