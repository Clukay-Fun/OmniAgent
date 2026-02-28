from __future__ import annotations

from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

# orchestrator imports Postgres client, which requires asyncpg at import time.
sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

from src.core.brain.orchestration.orchestrator import AgentOrchestrator  # noqa: E402


def test_orchestrator_requires_data_writer() -> None:
    """data_writer 未注入时必须报错，防止回退到静默兜底。"""
    with pytest.raises(TypeError):
        AgentOrchestrator()
