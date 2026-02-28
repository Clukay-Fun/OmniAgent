from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.foundation.telemetry.usage_cost import compute_usage_cost, load_model_pricing


def test_compute_usage_cost_with_input_output_pricing() -> None:
    pricing = load_model_pricing(
        model_pricing_json='{"models":{"gpt-4o-mini":{"input_per_1k":0.15,"output_per_1k":0.60}}}'
    )

    cost, estimated, warning = compute_usage_cost(
        model="gpt-4o-mini",
        prompt_tokens=1000,
        completion_tokens=500,
        token_count=1500,
        pricing_map=pricing,
    )

    assert round(cost, 6) == 0.45
    assert estimated is False
    assert warning == ""


def test_compute_usage_cost_unknown_model_is_safe_fallback() -> None:
    cost, estimated, warning = compute_usage_cost(
        model="unknown-model",
        prompt_tokens=123,
        completion_tokens=45,
        token_count=168,
        pricing_map={},
    )

    assert cost == 0.0
    assert estimated is True
    assert warning == "unknown_model_pricing"
