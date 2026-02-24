"""Core processing status abstractions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Protocol


class ProcessingStatus(str, Enum):
    THINKING = "thinking"
    SEARCHING = "searching"
    DONE = "done"


@dataclass
class ProcessingStatusEvent:
    status: ProcessingStatus
    user_id: str
    chat_id: str | None = None
    chat_type: str | None = None
    message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ProcessingStatusEmitter(Protocol):
    def __call__(self, event: ProcessingStatusEvent) -> Awaitable[None] | None: ...
