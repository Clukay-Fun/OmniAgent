import asyncio
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.capabilities.skills.implementations.chitchat import ChitchatSkill
from src.core.foundation.common.types import SkillContext


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


def test_chitchat_help_trigger_for_who_are_you() -> None:
    llm = _FakeLLM()
    skill = ChitchatSkill(skills_config={"chitchat": {"allow_llm": False}}, llm_client=llm)

    result = asyncio.run(skill.execute(SkillContext(query="你是谁", user_id="u3")))

    assert result.success is True
    assert result.data.get("type") == "help"
    assert any(token in result.reply_text for token in ["帮助", "可以帮你"])
    assert llm.called is False


def test_chitchat_open_domain_allowed_for_discord_profile() -> None:
    llm = _FakeLLM()
    skill = ChitchatSkill(skills_config={"chitchat": {"allow_llm": True}}, llm_client=llm)

    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="你觉得今天晚饭吃什么好？",
                user_id="u4",
                extra={"user_profile": {"channel_type": "discord", "allow_open_domain_chat": True}},
            )
        )
    )

    assert result.success is True
    assert result.data.get("type") == "llm_chat"
    assert llm.called is True
