from src.core.intent.rules import compile_trigger_patterns


def test_compile_trigger_patterns() -> None:
    patterns = compile_trigger_patterns([r"a.*b", ""])
    assert len(patterns) == 1
    assert patterns[0].search("a123b") is not None
