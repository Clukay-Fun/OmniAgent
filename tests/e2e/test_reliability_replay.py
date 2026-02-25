"""
E2E 可靠性回放测试。

从 fixtures/reliability_replay_cases.yaml 加载 8 条用例，
逐条执行并断言结果。
"""

from __future__ import annotations

import time
from pathlib import Path
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.locator_triplet import validate_locator_triplet  # noqa: E402
from src.core.state.models import PendingActionState, PendingActionStatus  # noqa: E402
from src.core.errors import (  # noqa: E402
    CoreError,
    PendingActionExpiredError,
    get_user_message,
)
from src.api.callback_deduper import CallbackDeduper  # noqa: E402


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "reliability_replay_cases.yaml"


def _load_cases() -> list[dict]:
    raw = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8")) or []
    assert isinstance(raw, list), "fixture must be a list of cases"
    return raw


CASES = _load_cases()


def _case_ids() -> list[str]:
    return [str(c.get("id") or f"case_{i}") for i, c in enumerate(CASES)]


# ── replay dispatcher ──────────────────────────────────────────────


def _run_triplet_missing(case: dict) -> None:
    ctx = case["given"]["context"]
    with pytest.raises(ValueError, match=case["expect"]["error_contains"]):
        validate_locator_triplet(
            app_token=ctx.get("app_token"),
            table_id=ctx.get("table_id"),
            record_id=ctx.get("record_id"),
            require_record_id=bool(ctx.get("require_record_id", False)),
        )


def _run_pending_lifecycle(case: dict) -> None:
    ctx = case["given"]["context"]
    now = time.time()
    ttl = int(ctx["ttl_seconds"])
    state = PendingActionState(
        action="test",
        status=PendingActionStatus(ctx["initial_status"]),
        created_at=now - 10 if ttl < 0 else now,
        expires_at=now + ttl,
    )
    target = PendingActionStatus(ctx["transition"])
    if case["expect"]["success"]:
        state.transition_to(target, now=now)
        assert state.status == PendingActionStatus(case["expect"]["final_status"])
    else:
        with pytest.raises(ValueError):
            state.transition_to(target, now=now)
        assert state.status == PendingActionStatus(case["expect"]["final_status"])


def _run_error_catalog(case: dict) -> None:
    ctx = case["given"]["context"]
    err = CoreError(code=ctx["error_code"])
    msg = get_user_message(err)
    assert case["expect"]["message_contains"] in msg


def _run_callback_dedup(case: dict) -> None:
    ctx = case["given"]["context"]
    deduper = CallbackDeduper(window_seconds=60)
    key = deduper.build_key(
        user_id=ctx["user_id"],
        action=ctx["callback_action"],
        payload=ctx.get("payload"),
    )
    first = deduper.is_duplicate(key)
    deduper.mark(key)
    second = deduper.is_duplicate(key)
    assert first == case["expect"]["first_is_duplicate"]
    assert second == case["expect"]["second_is_duplicate"]


def _run_happy_path(case: dict) -> None:
    ctx = case["given"]["context"]
    triplet = validate_locator_triplet(
        app_token=ctx["triplet"]["app_token"],
        table_id=ctx["triplet"]["table_id"],
    )
    assert triplet is not None

    now = time.time()
    state = PendingActionState(action="create_record", created_at=now, expires_at=now + 300)
    assert state.status == PendingActionStatus.CONFIRMABLE

    deduper = CallbackDeduper(window_seconds=60)
    key = deduper.build_key(user_id="u1", action="create_record_confirm")
    assert deduper.is_duplicate(key) is False


_DISPATCHERS = {
    "create_record": _run_triplet_missing,
    "update_record": _run_triplet_missing,
    "pending_lifecycle": _run_pending_lifecycle,
    "error_catalog": _run_error_catalog,
    "callback_dedup": _run_callback_dedup,
    "happy_path": _run_happy_path,
}


@pytest.mark.parametrize("case", CASES, ids=_case_ids())
def test_reliability_replay(case: dict) -> None:
    action = case["given"]["action"]
    dispatcher = _DISPATCHERS.get(action)
    assert dispatcher is not None, f"unknown replay action: {action}"
    dispatcher(case)
