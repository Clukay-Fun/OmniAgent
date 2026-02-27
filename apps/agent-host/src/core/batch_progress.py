"""Batch progress event abstractions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Protocol


class BatchProgressPhase(str, Enum):
    START = "start"
    COMPLETE = "complete"


@dataclass
class BatchProgressEvent:
    phase: BatchProgressPhase
    user_id: str
    total: int
    succeeded: int = 0
    failed: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class BatchProgressEmitter(Protocol):
    def __call__(self, event: BatchProgressEvent) -> Awaitable[None] | None: ...
