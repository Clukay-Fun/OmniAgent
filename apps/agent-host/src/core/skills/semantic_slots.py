from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SemanticSlotKey(str, Enum):
    CASE_IDENTIFIER = "case_identifier"
    PARTY_A = "party_a"
    PARTY_B = "party_b"
    COURT = "court"
    STAGE = "stage"
    OWNER = "owner"
    STATUS = "status"
    HEARING_DATE = "hearing_date"


@dataclass
class SemanticSlotExtraction:
    slots: dict[SemanticSlotKey, str] = field(default_factory=dict)
    missing_required: list[SemanticSlotKey] = field(default_factory=list)
    confidence: Optional[float] = None
