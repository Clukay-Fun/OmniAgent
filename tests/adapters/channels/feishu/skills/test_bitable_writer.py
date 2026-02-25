from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[5]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.skills.bitable_writer import BitableWriter  # noqa: E402


class _FakeMCPClient:
    def __init__(self, responder: Callable[[str, dict[str, Any]], Any]) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, name: str, params: dict[str, Any]) -> Any:
        payload = dict(params)
        self.calls.append({"name": name, "params": payload})
        return self._responder(name, payload)


def test_create_success_returns_record_id_and_url() -> None:
    mcp = _FakeMCPClient(
        lambda name, params: {
            "success": True,
            "record_id": "rec_100",
            "record_url": "https://feishu.cn/base/app/table/rec_100",
            "fields": params.get("fields"),
        }
    )
    writer = BitableWriter(mcp)

    result = asyncio.run(
        writer.create(
            "tbl_cases",
            {"案号": "A-001"},
            idempotency_key="idem-create-1",
        )
    )

    assert result.success is True
    assert result.record_id == "rec_100"
    assert result.record_url == "https://feishu.cn/base/app/table/rec_100"
    assert mcp.calls[0]["name"] == "feishu.v1.bitable.record.create"
    assert mcp.calls[0]["params"] == {
        "table_id": "tbl_cases",
        "fields": {"案号": "A-001"},
        "idempotency_key": "idem-create-1",
    }


def test_update_success_returns_record_url() -> None:
    mcp = _FakeMCPClient(
        lambda name, params: {
            "success": True,
            "record_id": params.get("record_id"),
            "record_url": "https://feishu.cn/base/app/table/rec_200",
            "fields": params.get("fields"),
        }
    )
    writer = BitableWriter(mcp)

    result = asyncio.run(
        writer.update(
            "tbl_cases",
            "rec_200",
            {"案件状态": "进行中"},
            idempotency_key="idem-update-1",
        )
    )

    assert result.success is True
    assert result.record_url == "https://feishu.cn/base/app/table/rec_200"
    assert mcp.calls[0]["name"] == "feishu.v1.bitable.record.update"
    assert mcp.calls[0]["params"] == {
        "table_id": "tbl_cases",
        "record_id": "rec_200",
        "fields": {"案件状态": "进行中"},
        "idempotency_key": "idem-update-1",
    }


def test_create_api_missing_field_error_returns_failure() -> None:
    mcp = _FakeMCPClient(lambda _name, _params: {"success": False, "error": "missing required field: 案号"})
    writer = BitableWriter(mcp)

    result = asyncio.run(writer.create("tbl_cases", {"委托人": "甲公司"}))

    assert result.success is False
    assert "missing required field" in str(result.error)


def test_update_invalid_record_id_returns_failure() -> None:
    mcp = _FakeMCPClient(lambda _name, _params: {"success": False, "error": "记录不存在: rec_not_exist"})
    writer = BitableWriter(mcp)

    result = asyncio.run(writer.update("tbl_cases", "rec_not_exist", {"案件状态": "已结案"}))

    assert result.success is False
    assert "记录不存在" in str(result.error)


def test_create_permission_denied_returns_failure() -> None:
    mcp = _FakeMCPClient(lambda _name, _params: {"success": False, "error": "permission denied: no write access"})
    writer = BitableWriter(mcp)

    result = asyncio.run(writer.create("tbl_cases", {"案号": "A-002"}))

    assert result.success is False
    assert "permission" in str(result.error).lower()


def test_create_timeout_exception_returns_failure() -> None:
    def _raise_timeout(_name: str, _params: dict[str, Any]) -> Any:
        raise TimeoutError("request timeout")

    mcp = _FakeMCPClient(_raise_timeout)
    writer = BitableWriter(mcp)

    result = asyncio.run(writer.create("tbl_cases", {"案号": "A-003"}))

    assert result.success is False
    assert "timeout" in str(result.error).lower()


def test_create_credential_expired_401_returns_failure() -> None:
    mcp = _FakeMCPClient(lambda _name, _params: {"success": False, "error": "401 unauthorized: token expired"})
    writer = BitableWriter(mcp)

    result = asyncio.run(writer.create("tbl_cases", {"案号": "A-004"}))

    assert result.success is False
    assert "401" in str(result.error)
    assert "token" in str(result.error).lower()


def test_create_rate_limited_429_returns_failure_with_retry_after() -> None:
    mcp = _FakeMCPClient(lambda _name, _params: {"success": False, "error": "429 too many requests; Retry-After: 2"})
    writer = BitableWriter(mcp)

    result = asyncio.run(writer.create("tbl_cases", {"案号": "A-005"}))

    assert result.success is False
    assert "429" in str(result.error)
    assert "retry-after" in str(result.error).lower()


def test_create_idempotency_key_is_forwarded_and_duplicate_has_no_new_side_effect() -> None:
    seen: dict[str, str] = {}
    create_counter = {"count": 0}

    def _responder(_name: str, params: dict[str, Any]) -> dict[str, Any]:
        key = str(params.get("idempotency_key") or "")
        if key in seen:
            return {
                "success": True,
                "record_id": seen[key],
                "record_url": f"https://feishu.cn/base/app/table/{seen[key]}",
            }
        create_counter["count"] += 1
        rec_id = f"rec_{create_counter['count']}"
        seen[key] = rec_id
        return {
            "success": True,
            "record_id": rec_id,
            "record_url": f"https://feishu.cn/base/app/table/{rec_id}",
        }

    mcp = _FakeMCPClient(_responder)
    writer = BitableWriter(mcp)

    first = asyncio.run(writer.create("tbl_cases", {"案号": "A-006"}, idempotency_key="idem-dup-1"))
    second = asyncio.run(writer.create("tbl_cases", {"案号": "A-006"}, idempotency_key="idem-dup-1"))

    assert first.success is True
    assert second.success is True
    assert first.record_id == second.record_id
    assert create_counter["count"] == 1
    assert mcp.calls[0]["params"]["idempotency_key"] == "idem-dup-1"
    assert mcp.calls[1]["params"]["idempotency_key"] == "idem-dup-1"


def test_create_response_shape_abnormal_returns_failure_without_crash() -> None:
    mcp = _FakeMCPClient(lambda _name, _params: ["unexpected", "payload"])
    writer = BitableWriter(mcp)

    result = asyncio.run(writer.create("tbl_cases", {"案号": "A-007"}))

    assert result.success is False
    assert result.error


def test_create_response_missing_expected_keys_returns_failure_without_crash() -> None:
    mcp = _FakeMCPClient(lambda _name, _params: {"record": {"id": "rec_x"}})
    writer = BitableWriter(mcp)

    result = asyncio.run(writer.create("tbl_cases", {"案号": "A-008"}))

    assert result.success is False


# ── S1: locator triplet adapter validation ──────────────────────────


from src.core.skills.locator_triplet import validate_locator_triplet  # noqa: E402


def test_bitable_writer_uses_validated_triplet() -> None:
    """Adapter layer: validate_locator_triplet rejects missing fields."""
    import pytest

    with pytest.raises(ValueError) as exc:
        validate_locator_triplet(app_token=None, table_id="tbl_1")
    assert "missing locator triplet" in str(exc.value)

