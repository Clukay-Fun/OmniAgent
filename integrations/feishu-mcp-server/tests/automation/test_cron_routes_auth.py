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

    def list_cron_jobs(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        _ = (status, limit)
        return [{"job_id": "job-1", "status": "active"}]

    def resume_cron_job(self, job_id: str) -> dict[str, Any]:
        if job_id == "missing":
            return {"status": "not_found", "job_id": job_id}
        return {"status": "resumed", "job_id": job_id}

    def cancel_cron_job(self, job_id: str) -> dict[str, Any]:
        if job_id == "missing":
            return {"status": "not_found", "job_id": job_id}
        return {"status": "cancelled", "job_id": job_id}


def test_cron_jobs_route_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakeService(auth_ok=False)
    monkeypatch.setattr(automation_server, "get_settings", lambda: object())
    monkeypatch.setattr(automation_server, "get_automation_service", lambda _settings: service)

    with pytest.raises(Exception) as excinfo:
        asyncio.run(automation_server.automation_cron_jobs(request=_FakeRequest(), status=None, limit=10))

    assert getattr(excinfo.value, "status_code", None) == 401


def test_cron_jobs_route_returns_items_when_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakeService(auth_ok=True)
    monkeypatch.setattr(automation_server, "get_settings", lambda: object())
    monkeypatch.setattr(automation_server, "get_automation_service", lambda _settings: service)

    response = asyncio.run(
        automation_server.automation_cron_jobs(
            request=_FakeRequest(headers={"x-automation-key": "k"}),
            status="active",
            limit=10,
        )
    )

    assert response["status"] == "ok"
    assert response["count"] == 1


def test_cron_resume_route_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakeService(auth_ok=True)
    monkeypatch.setattr(automation_server, "get_settings", lambda: object())
    monkeypatch.setattr(automation_server, "get_automation_service", lambda _settings: service)

    with pytest.raises(Exception) as excinfo:
        asyncio.run(automation_server.automation_cron_resume("missing", request=_FakeRequest()))

    assert getattr(excinfo.value, "status_code", None) == 404


def test_cron_resume_route_returns_ok_when_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakeService(auth_ok=True)
    monkeypatch.setattr(automation_server, "get_settings", lambda: object())
    monkeypatch.setattr(automation_server, "get_automation_service", lambda _settings: service)

    response = asyncio.run(
        automation_server.automation_cron_resume(
            "job-1",
            request=_FakeRequest(headers={"x-automation-key": "k"}),
        )
    )

    assert response["status"] == "ok"
    assert response["result"]["status"] == "resumed"


def test_cron_cancel_route_returns_ok_when_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakeService(auth_ok=True)
    monkeypatch.setattr(automation_server, "get_settings", lambda: object())
    monkeypatch.setattr(automation_server, "get_automation_service", lambda _settings: service)

    response = asyncio.run(
        automation_server.automation_cron_cancel(
            "job-1",
            request=_FakeRequest(headers={"x-automation-key": "k"}),
        )
    )

    assert response["status"] == "ok"
    assert response["result"]["status"] == "cancelled"
