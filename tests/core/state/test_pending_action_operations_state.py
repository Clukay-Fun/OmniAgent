from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.state import ConversationStateManager, MemoryStateStore  # noqa: E402
from src.core.state.models import (  # noqa: E402
    ConversationState,
    OperationEntry,
    OperationExecutionStatus,
    PendingActionState,
)


def test_operation_entry_defaults() -> None:
    entry = OperationEntry(index=0, payload={"record_id": "rec_1"})

    assert entry.index == 0
    assert entry.payload == {"record_id": "rec_1"}
    assert entry.status == OperationExecutionStatus.PENDING
    assert entry.error_code is None
    assert entry.error_detail is None
    assert entry.executed_at is None


def test_pending_action_state_converts_legacy_plain_operations() -> None:
    state = PendingActionState(
        action="batch_update_records",
        operations=[{"record_id": "rec_1"}, {"record_id": "rec_2"}],
        created_at=0,
        expires_at=300,
    )

    assert [item.payload for item in state.operations] == [
        {"record_id": "rec_1"},
        {"record_id": "rec_2"},
    ]
    assert [item.index for item in state.operations] == [0, 1]


def test_pending_action_state_roundtrip_keeps_operation_status() -> None:
    now = time.time()
    source = ConversationState(
        user_id="u1",
        created_at=now,
        updated_at=now,
        expires_at=now + 300,
        pending_action=PendingActionState(
            action="batch_update_records",
            operations=[
                OperationEntry(index=0, payload={"record_id": "rec_1"}, status=OperationExecutionStatus.SUCCEEDED),
                OperationEntry(index=1, payload={"record_id": "rec_2"}, status=OperationExecutionStatus.FAILED, error_code="x"),
            ],
            created_at=now,
            expires_at=now + 120,
        ),
    )

    loaded = ConversationState.from_dict(asdict(source))

    assert loaded.pending_action is not None
    assert len(loaded.pending_action.operations) == 2
    assert loaded.pending_action.operations[0].status == OperationExecutionStatus.SUCCEEDED
    assert loaded.pending_action.operations[1].status == OperationExecutionStatus.FAILED
    assert loaded.pending_action.operations[1].error_code == "x"


def test_manager_update_pending_action_operations_persists_status() -> None:
    manager = ConversationStateManager(store=MemoryStateStore())
    manager.set_pending_action(
        "u_batch",
        action="batch_update_records",
        payload={"table_id": "tbl_main"},
        operations=[{"record_id": "rec_1"}, {"record_id": "rec_2"}],
    )

    pending = manager.get_pending_action("u_batch")
    assert pending is not None
    pending.operations[0].status = OperationExecutionStatus.SUCCEEDED
    pending.operations[1].status = OperationExecutionStatus.FAILED
    pending.operations[1].error_code = "update_record_failed"
    manager.update_pending_action_operations("u_batch", pending)

    loaded = manager.get_pending_action("u_batch")
    assert loaded is not None
    assert loaded.operations[0].status == OperationExecutionStatus.SUCCEEDED
    assert loaded.operations[1].status == OperationExecutionStatus.FAILED
    assert loaded.operations[1].error_code == "update_record_failed"
