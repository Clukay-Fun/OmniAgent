"""自动化模块入口。"""

from src.automation.service import AutomationService, AutomationValidationError
from src.automation.snapshot import SnapshotStore
from src.automation.store import IdempotencyStore
from src.automation.checkpoint import CheckpointStore
from src.automation.engine import AutomationEngine
from src.automation.rules import RuleMatcher, RuleStore
from src.automation.actions import ActionExecutionError, ActionExecutor
from src.automation.poller import AutomationPoller
from src.automation.schema import SchemaStateStore, SchemaWatcher, WebhookNotifier
from src.automation.schema_poller import SchemaPoller
from src.automation.deadletter import DeadLetterStore
from src.automation.runlog import RunLogStore
from src.automation.delay_store import DelayedTask, DelayStore
from src.automation.delay_scheduler import DelayScheduler
from src.automation.cron_store import CronJob, CronStore
from src.automation.cron_scheduler import CronScheduler

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
    "SchemaPoller",
    "SchemaStateStore",
    "SchemaWatcher",
    "WebhookNotifier",
    "DeadLetterStore",
    "RunLogStore",
    "DelayedTask",
    "DelayStore",
    "DelayScheduler",
    "CronJob",
    "CronStore",
    "CronScheduler",
]
