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
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}


def test_reload_skill_metadata_endpoint_returns_report(monkeypatch) -> None:
    class _FakeAgentCore:
        def reload_skill_metadata(self) -> dict[str, object]:
            return {
                "loaded": ["query", "create"],
                "failed": [],
                "loaded_count": 2,
                "failed_count": 0,
            }

    monkeypatch.setattr(webhook_module, "agent_core", _FakeAgentCore())
    monkeypatch.setattr(
        webhook_module,
        "_get_settings",
        lambda: SimpleNamespace(feishu=SimpleNamespace(verification_token="reload-secret")),
    )

    response = asyncio.run(
        webhook_module.reload_skill_metadata(_FakeRequest(headers={"x-reload-token": "reload-secret"}))
    )

    assert response["status"] == "ok"
    assert response["scope"] == "skill_metadata"
    assert response["result"]["loaded"] == ["query", "create"]


def test_reload_skill_metadata_endpoint_returns_500_on_failure(monkeypatch) -> None:
    class _BrokenAgentCore:
        def reload_skill_metadata(self) -> dict[str, object]:
            raise RuntimeError("boom")

    monkeypatch.setattr(webhook_module, "agent_core", _BrokenAgentCore())
    monkeypatch.setattr(
        webhook_module,
        "_get_settings",
        lambda: SimpleNamespace(feishu=SimpleNamespace(verification_token="reload-secret")),
    )

    with pytest.raises(Exception) as excinfo:
        asyncio.run(
            webhook_module.reload_skill_metadata(_FakeRequest(headers={"x-reload-token": "reload-secret"}))
        )

    assert getattr(excinfo.value, "status_code", None) == 500
    assert "skill metadata reload failed" in str(getattr(excinfo.value, "detail", ""))


def test_reload_skill_metadata_endpoint_requires_valid_token(monkeypatch) -> None:
    class _FakeAgentCore:
        def reload_skill_metadata(self) -> dict[str, object]:
            return {
                "loaded": ["query"],
                "failed": [],
                "loaded_count": 1,
                "failed_count": 0,
            }

    monkeypatch.setattr(webhook_module, "agent_core", _FakeAgentCore())
    monkeypatch.setattr(
        webhook_module,
        "_get_settings",
        lambda: SimpleNamespace(feishu=SimpleNamespace(verification_token="reload-secret")),
    )

    with pytest.raises(Exception) as excinfo:
        asyncio.run(webhook_module.reload_skill_metadata(_FakeRequest(headers={"x-reload-token": "wrong"})))

    assert getattr(excinfo.value, "status_code", None) == 401


def test_reload_skill_metadata_endpoint_rejects_when_token_not_configured(monkeypatch) -> None:
    class _FakeAgentCore:
        def reload_skill_metadata(self) -> dict[str, object]:
            return {
                "loaded": ["query"],
                "failed": [],
                "loaded_count": 1,
                "failed_count": 0,
            }

    monkeypatch.setattr(webhook_module, "agent_core", _FakeAgentCore())
    monkeypatch.setattr(
        webhook_module,
        "_get_settings",
        lambda: SimpleNamespace(feishu=SimpleNamespace(verification_token="")),
    )

    with pytest.raises(Exception) as excinfo:
        asyncio.run(webhook_module.reload_skill_metadata(_FakeRequest(headers={"x-reload-token": "any"})))

    assert getattr(excinfo.value, "status_code", None) == 503
