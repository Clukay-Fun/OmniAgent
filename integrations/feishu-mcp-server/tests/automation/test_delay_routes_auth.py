from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any

import pytest


MCP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_ROOT))

from src.automation.service import AutomationValidationError
from src.server import automation as automation_server


class _FakeRequest:
    def __init__(self, headers: dict[str, str] | None = None, body: bytes = b"") -> None:
        self.headers = headers or {}
        self._body = body

    async def body(self) -> bytes:
        return self._body


class _FakeService:
    def __init__(self, *, auth_ok: bool = True) -> None:
        self._auth_ok = auth_ok

    def verify_management_auth(self, headers: dict[str, str], raw_body: bytes) -> None:
        _ = (headers, raw_body)
        if not self._auth_ok:
            raise AutomationValidationError("invalid webhook api key")

    def list_delay_tasks(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        _ = (status, limit)
        return [{"task_id": "task-1", "status": "scheduled"}]

    def cancel_delay_task(self, task_id: str) -> dict[str, Any]:
        if task_id == "missing":
            return {"status": "not_found", "task_id": task_id}
        return {"status": "cancelled", "task_id": task_id}


def test_delay_tasks_route_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakeService(auth_ok=False)
    monkeypatch.setattr(automation_server, "get_settings", lambda: object())
    monkeypatch.setattr(automation_server, "get_automation_service", lambda _settings: service)

    with pytest.raises(Exception) as excinfo:
        asyncio.run(automation_server.automation_delay_tasks(request=_FakeRequest(), status=None, limit=10))

    assert getattr(excinfo.value, "status_code", None) == 401


def test_delay_tasks_route_returns_items_when_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakeService(auth_ok=True)
    monkeypatch.setattr(automation_server, "get_settings", lambda: object())
    monkeypatch.setattr(automation_server, "get_automation_service", lambda _settings: service)

    response = asyncio.run(
        automation_server.automation_delay_tasks(
            request=_FakeRequest(headers={"x-automation-key": "k"}),
            status="scheduled",
            limit=10,
        )
    )

    assert response["status"] == "ok"
    assert response["count"] == 1


def test_delay_cancel_route_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakeService(auth_ok=False)
    monkeypatch.setattr(automation_server, "get_settings", lambda: object())
    monkeypatch.setattr(automation_server, "get_automation_service", lambda _settings: service)

    with pytest.raises(Exception) as excinfo:
        asyncio.run(automation_server.automation_delay_cancel("task-1", request=_FakeRequest()))

    assert getattr(excinfo.value, "status_code", None) == 401


def test_delay_cancel_route_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakeService(auth_ok=True)
    monkeypatch.setattr(automation_server, "get_settings", lambda: object())
    monkeypatch.setattr(automation_server, "get_automation_service", lambda _settings: service)

    with pytest.raises(Exception) as excinfo:
        asyncio.run(automation_server.automation_delay_cancel("missing", request=_FakeRequest()))

    assert getattr(excinfo.value, "status_code", None) == 404
