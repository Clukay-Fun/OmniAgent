from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Protocol


logger = logging.getLogger(__name__)


@dataclass
class ComplexityScore:
    level: str
    reasons: list[str] = field(default_factory=list)


class ComplexityScorer(Protocol):
    def score(self, query: str) -> ComplexityScore:
        ...


class RuleBasedComplexityScorer:
    _SIMPLE_KEYWORDS = (
        "吗",
        "好的",
        "谢谢",
        "收到",
        "确认",
        "在吗",
        "hello",
        "hi",
    )
    _COMPLEX_KEYWORDS = (
        "对比",
        "比较",
        "分析",
        "总结",
        "风险",
        "建议",
        "附件",
        "合同",
        "历史",
        "原因",
        "方案",
        "解释",
        "推理",
        "步骤",
        "report",
    )
    _MEDIUM_KEYWORDS = (
        "汇总",
        "最近",
        "筛选",
        "范围",
        "分页",
        "按",
        "统计",
        "条件",
        "今天",
        "本周",
        "本月",
        "最近",
        "list",
    )

    def score(self, query: str) -> ComplexityScore:
        text = str(query or "").strip()
        lowered = text.lower()
        reasons: list[str] = []

        if any(token in lowered for token in self._SIMPLE_KEYWORDS) and len(text) <= 24:
            return ComplexityScore(level="simple", reasons=["simple_keyword"])

        date_like = any(token in lowered for token in ("今天", "本周", "本月", "最近", "上周", "下周"))
        query_like = any(token in lowered for token in ("查", "查询", "找", "列出", "有哪些"))
        if date_like and query_like:
            reasons.append("query_with_date_range")

        if len(text) >= 50:
            reasons.append("long_query")
        if any(token in lowered for token in self._COMPLEX_KEYWORDS):
            reasons.append("complex_keyword")
        if "\n" in text:
            reasons.append("multi_line")
        if any(token in lowered for token in self._MEDIUM_KEYWORDS):
            reasons.append("medium_keyword")

        if "complex_keyword" in reasons or ("long_query" in reasons and len(reasons) >= 2):
            return ComplexityScore(level="complex", reasons=reasons)
        if reasons:
            return ComplexityScore(level="medium", reasons=reasons)
        return ComplexityScore(level="simple", reasons=["default_simple"])


@dataclass
class RoutingDecision:
    model_selected: str
    route_label: str
    complexity: str
    reason: str
    in_ab_bucket: bool
    metadata: dict[str, str]


class ModelRouter:
    def __init__(
        self,
        *,
        enabled: bool,
        ratio: float,
        primary_model: str,
        model_a: str | None = None,
        model_b: str | None = None,
        scorer: ComplexityScorer | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._ratio = max(0.0, min(1.0, float(ratio)))
        self._primary_model = str(primary_model or "").strip()
        self._model_a = str(model_a or "").strip()
        self._model_b = str(model_b or "").strip()
        self._scorer = scorer or RuleBasedComplexityScorer()

    def decide(self, user_id: str, query: str) -> RoutingDecision:
        if not self._enabled or self._ratio <= 0:
            return self._primary_decision(complexity="medium", route_label="primary_default", reason="routing_disabled")

        try:
            score = self._scorer.score(query)
            complexity = score.level if score.level in {"simple", "medium", "complex"} else "medium"
            score_reason = ",".join(score.reasons)
        except Exception as exc:
            logger.warning(
                "complexity scorer failed: %s",
                exc,
                extra={"event_code": "model_router.complexity.failed"},
            )
            return self._primary_decision(complexity="medium", route_label="primary_fallback", reason="scorer_error")

        in_bucket = self._is_in_ab_bucket(user_id=user_id, query=query)
        if not in_bucket:
            return self._primary_decision(complexity=complexity, route_label="primary_default", reason="ratio_not_matched")

        if complexity == "simple" and self._model_a:
            return self._build(self._model_a, "ab_simple", complexity, f"simple:{score_reason}", True)
        if complexity == "complex" and self._model_b:
            return self._build(self._model_b, "ab_complex", complexity, f"complex:{score_reason}", True)
        return self._primary_decision(complexity=complexity, route_label="primary_default", reason=f"fallback:{score_reason}")

    def _primary_decision(self, complexity: str, route_label: str, reason: str) -> RoutingDecision:
        return self._build(self._primary_model, route_label, complexity, reason, False)

    def _build(
        self,
        model_selected: str,
        route_label: str,
        complexity: str,
        reason: str,
        in_ab_bucket: bool,
    ) -> RoutingDecision:
        metadata = {
            "route_label": route_label,
            "model_selected": model_selected,
            "complexity": complexity,
            "route_reason": reason,
            "ab_ratio": f"{self._ratio:.3f}",
            "in_ab_bucket": "true" if in_ab_bucket else "false",
        }
        return RoutingDecision(
            model_selected=model_selected,
            route_label=route_label,
            complexity=complexity,
            reason=reason,
            in_ab_bucket=in_ab_bucket,
            metadata=metadata,
        )

    def _is_in_ab_bucket(self, user_id: str, query: str) -> bool:
        key = f"{user_id}:{query}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        value = int(digest[:8], 16) / 0xFFFFFFFF
        return value < self._ratio
