from __future__ import annotations

import asyncio
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.understanding.intent.parser import IntentParser  # noqa: E402


def test_intent_parser_merges_top_level_skill_keywords_when_skills_section_present() -> None:
    # skills.yaml 形态：`skills` 常放结构化配置（如 timeout），关键词在顶层 skill 节点
    skills_config = {
        "defaults": {"timeout_seconds": 10},
        "skills": {
            "query": {"timeout_seconds": 15},
            "chitchat": {"timeout_seconds": 10},
        },
        "intent": {"thresholds": {"direct_execute": 0.5, "llm_confirm": 0.3}},
        "query": {"keywords": ["查", "找", "查询", "搜索"], "time_keywords": []},
        "chitchat": {"whitelist": ["你好", "在吗"]},
    }

    parser = IntentParser(skills_config=skills_config, llm_client=None)
    result = asyncio.run(parser.parse("查找JFTD-20260023"))

    assert result.method == "rule"
    assert result.top_skill() is not None
    assert result.top_skill().name == "QuerySkill"
