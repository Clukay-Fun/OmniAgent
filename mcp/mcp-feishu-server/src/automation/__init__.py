"""自动化模块入口。"""

from src.automation.service import AutomationService, AutomationValidationError
from src.automation.snapshot import SnapshotStore
from src.automation.store import IdempotencyStore
from src.automation.checkpoint import CheckpointStore

__all__ = [
    "AutomationService",
    "AutomationValidationError",
    "SnapshotStore",
    "IdempotencyStore",
    "CheckpointStore",
]
