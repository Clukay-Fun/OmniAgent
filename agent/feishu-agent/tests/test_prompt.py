from src.config import PromptSettings
from src.core.prompt import build_system_prompt


def test_build_system_prompt() -> None:
    settings = PromptSettings(
        role="角色",
        capabilities="能力A",
        constraints="限制B",
        output_format="格式C",
    )

    prompt = build_system_prompt(settings)

    assert "角色" in prompt
    assert "能力" in prompt
    assert "限制" in prompt
    assert "输出格式" in prompt
