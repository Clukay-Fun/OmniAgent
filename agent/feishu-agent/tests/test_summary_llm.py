import pytest

from src.core.skills.summary import SummarySkill
from src.core.types import SkillContext


class FakeLLM:
    async def chat(self, messages, timeout=None):
        return "这是一个总结"


@pytest.mark.asyncio
async def test_summary_llm_path() -> None:
    llm = FakeLLM()
    skill = SummarySkill(llm_client=llm)
    context = SkillContext(
        query="总结一下",
        user_id="u1",
        last_result={
            "records": [
                {"fields_text": {"案号": "123", "案由": "合同纠纷", "当事人": "张三"}}
            ]
        },
        extra={"soul_prompt": "你是助手", "user_memory": "简洁", "shared_memory": "团队"},
    )

    result = await skill.execute(context)

    assert result.success is True
    assert "总结" in result.reply_text
