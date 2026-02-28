"""
描述: 该模块负责根据查询的复杂性来决定使用哪个模型进行处理。
主要功能:
    - 计算查询的复杂性评分
    - 根据复杂性评分和配置的路由规则决定使用哪个模型
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Protocol


logger = logging.getLogger(__name__)


@dataclass
class ComplexityScore:
    """
    表示查询的复杂性评分及其原因。

    功能:
        - 包含复杂性等级（level）
        - 包含评分的原因（reasons）
    """
    level: str
    reasons: list[str] = field(default_factory=list)


class ComplexityScorer(Protocol):
    """
    定义复杂性评分器的协议。

    功能:
        - 必须实现 score 方法，该方法接受一个查询字符串并返回一个 ComplexityScore 对象
    """
    def score(self, query: str) -> ComplexityScore:
        ...


class RuleBasedComplexityScorer:
    """
    基于规则的复杂性评分器。

    功能:
        - 根据预定义的简单、中等和复杂关键词来评分查询的复杂性
        - 返回一个 ComplexityScore 对象，包含复杂性等级和评分原因
    """
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
        """
        根据查询内容计算复杂性评分。

        功能:
            - 将查询转换为小写并去除首尾空格
            - 检查查询中是否包含简单、中等或复杂关键词
            - 根据关键词和查询长度决定复杂性等级
            - 返回一个 ComplexityScore 对象
        """
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
    """
    表示路由决策的结果。

    功能:
        - 包含选择的模型（model_selected）
        - 包含路由标签（route_label）
        - 包含复杂性等级（complexity）
        - 包含路由原因（reason）
        - 包含是否在 A/B 测试桶中（in_ab_bucket）
        - 包含额外的元数据（metadata）
    """
    model_selected: str
    route_label: str
    complexity: str
    reason: str
    in_ab_bucket: bool
    metadata: dict[str, str]


class ModelRouter:
    """
    模型路由器，根据查询复杂性决定使用哪个模型。

    功能:
        - 初始化时配置路由规则和复杂性评分器
        - 根据用户ID和查询内容决定路由
        - 返回一个 RoutingDecision 对象
    """
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
        """
        初始化模型路由器。

        功能:
            - 设置路由是否启用
            - 设置 A/B 测试的比例
            - 设置主模型和可选的 A/B 测试模型
            - 设置复杂性评分器，默认使用 RuleBasedComplexityScorer
        """
        self._enabled = bool(enabled)
        self._ratio = max(0.0, min(1.0, float(ratio)))
        self._primary_model = str(primary_model or "").strip()
        self._model_a = str(model_a or "").strip()
        self._model_b = str(model_b or "").strip()
        self._scorer = scorer or RuleBasedComplexityScorer()

    def decide(self, user_id: str, query: str) -> RoutingDecision:
        """
        根据用户ID和查询内容决定路由。

        功能:
            - 检查路由是否启用
            - 使用复杂性评分器计算查询复杂性
            - 根据复杂性和 A/B 测试比例决定使用哪个模型
            - 返回一个 RoutingDecision 对象
        """
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
        """
        返回主模型的路由决策。

        功能:
            - 使用主模型构建一个 RoutingDecision 对象
        """
        return self._build(self._primary_model, route_label, complexity, reason, False)

    def _build(
        self,
        model_selected: str,
        route_label: str,
        complexity: str,
        reason: str,
        in_ab_bucket: bool,
    ) -> RoutingDecision:
        """
        构建一个 RoutingDecision 对象。

        功能:
            - 创建包含路由信息的元数据字典
            - 返回一个 RoutingDecision 对象
        """
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
        """
        判断用户是否在 A/B 测试桶中。

        功能:
            - 根据用户ID和查询内容生成一个哈希值
            - 根据哈希值判断用户是否在 A/B 测试桶中
        """
        key = f"{user_id}:{query}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        value = int(digest[:8], 16) / 0xFFFFFFFF
        return value < self._ratio
