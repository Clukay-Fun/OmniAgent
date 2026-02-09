"""自动化模块入口。"""

from src.automation.service import AutomationService, AutomationValidationError
from src.automation.snapshot import SnapshotStore
from src.automation.store import IdempotencyStore
from src.automation.checkpoint import CheckpointStore
from src.automation.engine import AutomationEngine
from src.automation.rules import RuleMatcher, RuleStore
from src.automation.actions import ActionExecutionError, ActionExecutor
from src.automation.poller import AutomationPoller
from src.automation.deadletter import DeadLetterStore

__all__ = [
    "AutomationService",
    "AutomationValidationError",
    "SnapshotStore",
    "IdempotencyStore",
    "CheckpointStore",
    "AutomationEngine",
    "RuleMatcher",
    "RuleStore",
    "ActionExecutor",
    "ActionExecutionError",
    "AutomationPoller",
    "DeadLetterStore",
]
