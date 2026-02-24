import asyncio
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.chitchat import ChitchatSkill
from src.core.types import SkillContext


class _FakeLLM:
    def __init__(self) -> None:
        self.called = False

    async def chat(self, _messages):
        self.called = True
        return "可以的，我们继续。"


def test_chitchat_guard_blocks_llm_when_allow_llm_false() -> None:
    llm = _FakeLLM()
    skill = ChitchatSkill(skills_config={"chitchat": {"allow_llm": False}}, llm_client=llm)

    result = asyncio.run(skill.execute(SkillContext(query="这个案件先聊聊", user_id="u1")))

    assert result.success is True
    assert result.data.get("type") == "guard_blocked"
    assert llm.called is False


def test_chitchat_guard_allows_llm_when_switch_on() -> None:
    llm = _FakeLLM()
    skill = ChitchatSkill(skills_config={"chitchat": {"allow_llm": True}}, llm_client=llm)

    result = asyncio.run(skill.execute(SkillContext(query="这个案件先聊聊", user_id="u2")))

    assert result.success is True
    assert result.data.get("type") == "llm_chat"
    assert llm.called is True
