"""
L1 Planner 输出结构定义与校验。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


PlannerIntent = Literal[
    "query_all",
    "query_view",
    "query_my_cases",
    "query_person",
    "query_exact",
    "query_date_range",
    "query_advanced",
    "create_record",
    "update_record",
    "delete_record",
    "create_reminder",
    "list_reminders",
    "cancel_reminder",
    "out_of_scope",
    "clarify_needed",
]


PlannerTool = Literal[
    "search",
    "search_exact",
    "search_keyword",
    "search_person",
    "search_date_range",
    "search_advanced",
    "record.create",
    "record.update",
    "record.delete",
    "reminder.create",
    "reminder.list",
    "reminder.cancel",
    "none",
]


class PlannerOutput(BaseModel):
    """Planner 输出。"""

    intent: PlannerIntent
    tool: PlannerTool
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    clarify_question: str = ""

    def to_context(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "tool": self.tool,
            "params": self.params,
            "confidence": self.confidence,
            "clarify_question": self.clarify_question,
        }
