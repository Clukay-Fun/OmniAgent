from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Callable


logger = logging.getLogger(__name__)


@dataclass
class UsageRecord:
    ts: str
    user_id: str
    conversation_id: str
    model: str
    skill: str
    token_count: int
    cost: float
    usage_source: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    business_metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "ts": self.ts,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "model": self.model,
            "skill": self.skill,
            "token_count": max(0, int(self.token_count)),
            "prompt_tokens": max(0, int(self.prompt_tokens)),
            "completion_tokens": max(0, int(self.completion_tokens)),
            "cost": float(self.cost),
            "usage_source": self.usage_source,
            "estimated": bool(self.estimated),
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        if self.business_metadata:
            payload["business_metadata"] = self.business_metadata
        return json.dumps(payload, ensure_ascii=False)


class UsageLogger:
    def __init__(
        self,
        enabled: bool,
        path_template: str,
        fail_open: bool = True,
        on_record_written: Callable[[UsageRecord], None] | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._path_template = str(path_template or "workspace/usage/usage_log-{date}.jsonl")
        self._fail_open = bool(fail_open)
        self._on_record_written = on_record_written

    def log(self, record: UsageRecord) -> bool:
        if not self._enabled:
            return False

        path = self._resolve_path(record.ts)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(record.to_json())
                f.write("\n")
            if callable(self._on_record_written):
                try:
                    self._on_record_written(record)
                except Exception as exc:
                    logger.warning(
                        "usage post-write hook failed: %s",
                        exc,
                        extra={"event_code": "usage_log.post_write_hook_failed"},
                    )
            return True
        except Exception as exc:
            logger.warning(
                "usage log write failed: %s",
                exc,
                extra={"event_code": "usage_log.write_failed", "path": str(path)},
            )
            if self._fail_open:
                return False
            raise

    def _resolve_path(self, timestamp: str) -> Path:
        day = _extract_date(timestamp)
        rendered = self._path_template.replace("{date}", day)
        return Path(rendered)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _extract_date(ts: str) -> str:
    value = str(ts or "")
    if len(value) >= 10:
        candidate = value[:10]
        if candidate[4:5] == "-" and candidate[7:8] == "-":
            return candidate
    return datetime.now().strftime("%Y-%m-%d")
