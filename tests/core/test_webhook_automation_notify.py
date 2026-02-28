from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))
crypto_module = types.ModuleType("Crypto")
crypto_cipher_module = types.ModuleType("Crypto.Cipher")
setattr(crypto_cipher_module, "AES", object())
setattr(crypto_module, "Cipher", crypto_cipher_module)
sys.modules.setdefault("Crypto", crypto_module)
sys.modules.setdefault("Crypto.Cipher", crypto_cipher_module)

import src.api.channels.feishu.webhook_router as webhook_module  # noqa: E402


class _FakeRequest:
    def __init__(self, payload: dict[str, object], headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.headers = headers or {}

    async def json(self) -> dict[str, object]:
        return self._payload


def _notify_settings(*, enabled: bool = True, api_key: str = "notify-key") -> SimpleNamespace:
    return SimpleNamespace(
        automation_notify=SimpleNamespace(enabled=enabled, api_key=api_key),
    )


def test_notify_endpoint_forwards_to_chat_id(monkeypatch) -> None:
    sent_calls: list[dict[str, object]] = []

    async def _fake_send_message(settings, receive_id, msg_type, content, **kwargs):
        sent_calls.append(
            {
                "settings": settings,
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": content,
                "kwargs": kwargs,
            }
        )
        return {"message_id": "omni-notify-1"}

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: _notify_settings())
    monkeypatch.setattr(webhook_module, "send_message", _fake_send_message)

    request = _FakeRequest(
        payload={
            "event": "automation.completed",
            "job_type": "rule",
            "job_id": "R001",
            "status": "success",
            "notify_target": {"chat_id": "oc_notify"},
            "summary": "规则执行完成",
        },
        headers={"x-automation-key": "notify-key"},
    )

    result = asyncio.run(webhook_module.automation_notify(request))

    assert result["status"] == "ok"
    assert result["receive_id_type"] == "chat_id"
    assert len(sent_calls) == 1
    assert sent_calls[0]["receive_id"] == "oc_notify"
    assert sent_calls[0]["msg_type"] == "text"
    assert sent_calls[0]["kwargs"]["receive_id_type"] == "chat_id"


def test_notify_endpoint_falls_back_to_open_id(monkeypatch) -> None:
    sent_calls: list[dict[str, object]] = []

    async def _fake_send_message(settings, receive_id, msg_type, content, **kwargs):
        sent_calls.append(
            {
                "settings": settings,
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": content,
                "kwargs": kwargs,
            }
        )
        return {"message_id": "omni-notify-2"}

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: _notify_settings())
    monkeypatch.setattr(webhook_module, "send_message", _fake_send_message)

    request = _FakeRequest(
        payload={
            "event": "automation.completed",
            "job_type": "cron",
            "job_id": "job-1",
            "status": "failed",
            "notify_target": {"user_id": "ou_123"},
            "summary": "定时任务失败",
            "error": "JobID=job-1，请重试",
        },
        headers={"x-automation-key": "notify-key"},
    )

    result = asyncio.run(webhook_module.automation_notify(request))

    assert result["status"] == "ok"
    assert result["receive_id_type"] == "open_id"
    assert len(sent_calls) == 1
    assert sent_calls[0]["receive_id"] == "ou_123"
    assert sent_calls[0]["kwargs"]["receive_id_type"] == "open_id"


def test_notify_endpoint_requires_valid_api_key(monkeypatch) -> None:
    monkeypatch.setattr(webhook_module, "_get_settings", lambda: _notify_settings())

    request = _FakeRequest(
        payload={
            "event": "automation.completed",
            "status": "success",
            "notify_target": {"chat_id": "oc_notify"},
            "summary": "ok",
        },
        headers={"x-automation-key": "wrong"},
    )

    with pytest.raises(Exception) as excinfo:
        asyncio.run(webhook_module.automation_notify(request))

    assert getattr(excinfo.value, "status_code", None) == 401


def test_notify_endpoint_rejects_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(webhook_module, "_get_settings", lambda: _notify_settings(enabled=False, api_key="notify-key"))

    request = _FakeRequest(
        payload={"event": "automation.completed", "status": "success", "summary": "ok"},
        headers={"x-automation-key": "notify-key"},
    )

    with pytest.raises(Exception) as excinfo:
        asyncio.run(webhook_module.automation_notify(request))

    assert getattr(excinfo.value, "status_code", None) == 503


def test_notify_endpoint_rejects_when_target_missing(monkeypatch) -> None:
    monkeypatch.setattr(webhook_module, "_get_settings", lambda: _notify_settings())

    request = _FakeRequest(
        payload={
            "event": "automation.completed",
            "job_type": "delay",
            "job_id": "task-1",
            "status": "success",
            "summary": "执行完成",
        },
        headers={"x-automation-key": "notify-key"},
    )

    with pytest.raises(Exception) as excinfo:
        asyncio.run(webhook_module.automation_notify(request))

    assert getattr(excinfo.value, "status_code", None) == 400
