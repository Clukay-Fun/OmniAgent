import pytest

from src.core.intent import IntentResult, SkillMatch
from src.core.router import SkillContext, SkillRouter


class FakeSkill:
    def __init__(self, name: str, payload: dict) -> None:
        self.name = name
        self._payload = payload

    async def execute(self, context: SkillContext):
        from src.core.types import SkillResult

        return SkillResult(
            success=True,
            skill_name=self.name,
            data=self._payload,
            message="ok",
            reply_text="ok",
        )


@pytest.mark.asyncio
async def test_chain_execution_with_pattern() -> None:
    skills_config = {
        "chain": {
            "triggers": [
                {"pattern": "链式", "skills": ["SkillA", "SkillB"]},
            ],
            "max_hops": 2,
        }
    }

    router = SkillRouter(skills_config, max_hops=2)
    router.register(FakeSkill("SkillA", {"step": "a"}))
    router.register(FakeSkill("SkillB", {"step": "b"}))

    intent = IntentResult(
        skills=[SkillMatch(name="SkillA", score=0.9, reason="test")],
        is_chain=True,
        method="rule",
    )

    context = SkillContext(query="触发链式", user_id="u1")
    result = await router.route(intent, context)

    assert result.success is True
    assert result.skill_name == "SkillB"
    assert result.data.get("step") == "b"
