from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.capabilities.skills.base.semantic_slots import SemanticSlotExtraction, SemanticSlotKey  # noqa: E402


def test_semantic_slot_keys_cover_minimum_set() -> None:
    expected = {
        "case_identifier",
        "party_a",
        "party_b",
        "court",
        "stage",
        "owner",
        "status",
        "hearing_date",
    }
    actual = {key.value for key in SemanticSlotKey}
    assert expected.issubset(actual)


def test_semantic_slot_extraction_dataclass_constructs() -> None:
    extraction = SemanticSlotExtraction(
        slots={
            SemanticSlotKey.CASE_IDENTIFIER: "JFTD-20260001",
            SemanticSlotKey.HEARING_DATE: "2026-03-01",
        },
        missing_required=[SemanticSlotKey.PARTY_A],
        confidence=0.75,
    )

    assert extraction.slots[SemanticSlotKey.CASE_IDENTIFIER] == "JFTD-20260001"
    assert extraction.missing_required == [SemanticSlotKey.PARTY_A]
    assert extraction.confidence == 0.75
