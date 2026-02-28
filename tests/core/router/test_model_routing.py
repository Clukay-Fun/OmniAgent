from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.understanding.router.model_routing import ModelRouter, RuleBasedComplexityScorer


def test_router_defaults_to_primary_when_disabled() -> None:
    router = ModelRouter(enabled=False, ratio=1.0, primary_model="primary", model_a="small", model_b="large")

    decision = router.decide(user_id="u1", query="帮我查一下今天待办")

    assert decision.model_selected == "primary"
    assert decision.route_label == "primary_default"


def test_complexity_scorer_returns_simple_medium_complex() -> None:
    scorer = RuleBasedComplexityScorer()

    assert scorer.score("帮我查张三案件").level == "simple"
    assert scorer.score("请汇总最近一周案件并按法院统计").level == "medium"
    assert scorer.score("请结合附件合同和历史记录，输出风险条款对比与下一步建议").level == "complex"


def test_router_uses_ab_ratio_and_model_aliases() -> None:
    router = ModelRouter(enabled=True, ratio=1.0, primary_model="primary", model_a="small", model_b="large")

    simple_decision = router.decide(user_id="u1", query="查案件")
    complex_decision = router.decide(user_id="u1", query="请结合附件合同和历史记录输出完整分析报告")

    assert simple_decision.model_selected == "small"
    assert simple_decision.route_label == "ab_simple"
    assert complex_decision.model_selected == "large"
    assert complex_decision.route_label == "ab_complex"


def test_router_falls_back_when_scorer_errors() -> None:
    class _BrokenScorer:
        def score(self, query: str):
            raise RuntimeError("boom")

    router = ModelRouter(
        enabled=True,
        ratio=1.0,
        primary_model="primary",
        model_a="small",
        model_b="large",
        scorer=_BrokenScorer(),
    )

    decision = router.decide(user_id="u2", query="任何问题")

    assert decision.model_selected == "primary"
    assert decision.complexity == "medium"
    assert decision.route_label == "primary_fallback"


def test_router_respects_rollout_ratio_zero() -> None:
    router = ModelRouter(enabled=True, ratio=0.0, primary_model="primary", model_a="small", model_b="large")

    decision = router.decide(user_id="u3", query="请分析附件并给出建议")

    assert decision.model_selected == "primary"
    assert decision.route_label == "primary_default"


def test_complexity_scorer_handles_empty_query() -> None:
    scorer = RuleBasedComplexityScorer()

    score = scorer.score("")

    assert score.level == "simple"
