"""
Intent parser with rule-based matching and LLM fallback.
Output fixed JSON: skills Top-3 + score + reason + is_chain.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


_SKILL_NAME_MAP: dict[str, str] = {
    "query": "QuerySkill",
    "summary": "SummarySkill",
    "reminder": "ReminderSkill",
    "chitchat": "ChitchatSkill",
}


# ============================================
# region 数据结构
# ============================================
@dataclass
class SkillMatch:
    """单个技能匹配结果"""

    name: str
    score: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 2),
            "reason": self.reason,
        }


@dataclass
class IntentResult:
    """意图识别结果（固定 JSON 格式）"""

    skills: list[SkillMatch] = field(default_factory=list)
    is_chain: bool = False
    requires_llm_confirm: bool = False
    method: str = "rule"  # rule / llm / fallback

    def to_dict(self) -> dict[str, Any]:
        return {
            "skills": [s.to_dict() for s in self.skills],
            "is_chain": self.is_chain,
            "requires_llm_confirm": self.requires_llm_confirm,
            "method": self.method,
        }

    @property
    def top_skills(self) -> list[SkillMatch]:
        return self.skills

    def top_skill(self) -> SkillMatch | None:
        return self.skills[0] if self.skills else None


# endregion
# ============================================


# ============================================
# region IntentParser 核心类
# ============================================
class IntentParser:
    """
    意图解析器：规则优先 + LLM 兜底

    支持两种配置结构：
    - v1: routing + skills + chains
    - v2: intent + query/summary/reminder/chitchat + chain
    """

    def __init__(
        self,
        skills_config: dict[str, Any],
        llm_client: Any = None,
    ) -> None:
        self._config = skills_config or {}
        self._llm = llm_client

        self._routing = self._config.get("routing", {})
        intent_cfg = self._config.get("intent", {})
        thresholds = intent_cfg.get("thresholds", {})

        self._direct_threshold = float(
            thresholds.get("direct_execute", self._routing.get("rule_threshold", 0.7))
        )
        self._llm_confirm_threshold = float(
            thresholds.get("llm_confirm", self._routing.get("llm_confirm_threshold", 0.4))
        )
        self._fallback_skill = self._routing.get("fallback_skill", "chitchat")

        self._skills = self._normalize_skills_config(self._config)
        self._chains = self._config.get("chains", {})

    async def parse(self, query: str) -> IntentResult:
        """解析用户输入，返回意图识别结果"""

        rule_matches = self._rule_match(query)
        top_score = rule_matches[0].score if rule_matches else 0.0
        is_chain = self._detect_chain(query)

        requires_llm_confirm = (
            self._llm is not None
            and top_score >= self._llm_confirm_threshold
            and top_score < self._direct_threshold
        )

        if top_score >= self._direct_threshold:
            logger.info(
                "Intent parsed by rule",
                extra={
                    "query": query,
                    "top_skill": rule_matches[0].name if rule_matches else "",
                    "score": top_score,
                    "method": "rule",
                },
            )
            return IntentResult(
                skills=rule_matches[:3],
                is_chain=is_chain,
                requires_llm_confirm=False,
                method="rule",
            )

        if requires_llm_confirm:
            try:
                llm_result = await self._llm_classify(query, rule_matches[:3])
                if llm_result:
                    llm_result.method = "llm"
                    llm_result.is_chain = llm_result.is_chain or is_chain
                    llm_result.requires_llm_confirm = True
                    return llm_result
            except Exception as e:
                logger.warning(f"LLM classification failed: {e}, falling back to rule")

            return IntentResult(
                skills=rule_matches[:3],
                is_chain=is_chain,
                requires_llm_confirm=True,
                method="rule",
            )

        if self._llm:
            try:
                llm_result = await self._llm_classify(query)
                if llm_result:
                    llm_result.method = "llm"
                    return llm_result
            except Exception as e:
                logger.warning(f"LLM classification failed: {e}")

        fallback_match = SkillMatch(
            name=self._get_skill_name(self._fallback_skill),
            score=0.0,
            reason="无法识别意图，使用兜底技能",
        )
        return IntentResult(
            skills=[fallback_match],
            is_chain=False,
            requires_llm_confirm=False,
            method="fallback",
        )

    def _normalize_skills_config(self, config: dict[str, Any]) -> dict[str, Any]:
        if "skills" in config:
            return config.get("skills", {})

        skills: dict[str, Any] = {}
        for key in ("query", "summary", "reminder", "chitchat"):
            cfg = config.get(key)
            if not isinstance(cfg, dict):
                continue
            merged = dict(cfg)
            merged.setdefault("name", _SKILL_NAME_MAP.get(key, key))
            if key == "chitchat" and "keywords" not in merged:
                merged["keywords"] = merged.get("whitelist", [])
            skills[key] = merged
        return skills

    def _rule_match(self, query: str) -> list[SkillMatch]:
        matches: list[SkillMatch] = []
        query_lower = query.lower()

        for skill_key, skill_cfg in self._skills.items():
            keywords = skill_cfg.get("keywords", [])
            time_keywords = skill_cfg.get("time_keywords", [])
            weights = skill_cfg.get("weights", {})
            skill_name = skill_cfg.get("name", skill_key)

            if not keywords:
                continue

            hit_count = 0
            hit_keywords: list[str] = []
            total_weight = 0.0

            for kw in keywords:
                weight = weights.get(kw, 1.0)
                if kw in query or kw.lower() in query_lower:
                    hit_count += 1
                    hit_keywords.append(kw)
                    total_weight += weight

            time_hits = [kw for kw in time_keywords if kw in query or kw.lower() in query_lower]
            if hit_count == 0:
                continue

            # 新评分算法：
            # - 基础分：命中任意关键词给 0.6 分
            # - 命中越多越好：每多命中一个加 0.1，上限 0.3
            # - 时间关键词加成：0.1
            base_score = 0.6
            hit_bonus = min((hit_count - 1) * 0.1, 0.3)
            time_bonus = 0.1 if time_hits else 0.0
            score = min(base_score + hit_bonus + time_bonus, 1.0)

            reason = f"命中关键词: {', '.join(hit_keywords[:3])}"
            if len(hit_keywords) > 3:
                reason += f" 等{len(hit_keywords)}个"
            if time_hits:
                reason += f"，时间: {', '.join(time_hits[:2])}"

            matches.append(SkillMatch(name=skill_name, score=score, reason=reason))

        matches.sort(key=lambda x: x.score, reverse=True)
        return matches

    def _detect_chain(self, query: str) -> bool:
        chain_cfg = self._config.get("chain", {})
        triggers = chain_cfg.get("triggers", [])
        for trigger in triggers:
            pattern = trigger.get("pattern")
            if pattern and re.search(pattern, query):
                return True

        for chain_key, chain_cfg in self._chains.items():
            trigger_keywords = chain_cfg.get("trigger_keywords", [])
            for trigger in trigger_keywords:
                if trigger in query:
                    return True
        return False

    def _get_skill_name(self, skill_key: str) -> str:
        if skill_key in self._skills:
            skill_cfg = self._skills.get(skill_key, {})
            return skill_cfg.get("name", skill_key)
        return _SKILL_NAME_MAP.get(skill_key, skill_key)

    async def _llm_classify(
        self,
        query: str,
        hints: list[SkillMatch] | None = None,
    ) -> IntentResult | None:
        if not self._llm:
            return None

        skill_list = "\n".join(
            f"- {cfg.get('name', key)}: {cfg.get('description', '')}"
            for key, cfg in self._skills.items()
        )

        hint_text = ""
        if hints:
            hint_text = "\n规则初步匹配结果（仅供参考）：\n" + "\n".join(
                f"- {h.name}: {h.score:.2f} ({h.reason})" for h in hints
            )

        prompt = f"""你是一个意图分类器。根据用户输入，判断最匹配的技能。

可用技能：
{skill_list}

用户输入：{query}
{hint_text}

请返回 JSON（不要输出其他内容）：
{{
  \"skills\": [
    {{\"name\": \"技能名\", \"score\": 0.0-1.0, \"reason\": \"简短理由\"}}
  ],
  \"is_chain\": false
}}

注意：
1. skills 数组最多包含 3 个最匹配的技能，按置信度降序排列
2. score 表示匹配置信度（0.0-1.0）
3. reason 用简短中文说明判断依据
4. is_chain 表示是否需要链式执行多个技能"""

        try:
            response = await self._llm.chat_json(prompt)
            if not response:
                return None

            skills_data = response.get("skills", [])
            skills: list[SkillMatch] = []
            for s in skills_data[:3]:
                skills.append(
                    SkillMatch(
                        name=s.get("name", ""),
                        score=float(s.get("score", 0.0)),
                        reason=s.get("reason", ""),
                    )
                )

            return IntentResult(
                skills=skills,
                is_chain=response.get("is_chain", False),
                method="llm",
            )
        except Exception as e:
            logger.error(f"LLM classify error: {e}")
            return None


# endregion
# ============================================


# ============================================
# region 配置加载辅助
# ============================================
def load_skills_config(config_path: str = "config/skills.yaml") -> dict[str, Any]:
    import yaml
    from pathlib import Path

    path = Path(config_path)
    if not path.exists():
        logger.warning(f"Skills config not found: {config_path}, using defaults")
        return _default_skills_config()

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _default_skills_config() -> dict[str, Any]:
    return {
        "intent": {
            "thresholds": {
                "direct_execute": 0.7,
                "llm_confirm": 0.4,
            },
            "llm_timeout": 10,
        },
        "query": {
            "keywords": ["查", "找", "搜索", "案件", "案子", "开庭"],
            "time_keywords": ["今天", "明天", "后天", "本周", "下周"],
        },
        "summary": {
            "keywords": ["总结", "汇总", "概括", "整理"],
            "default_fields": ["案号", "案由", "当事人", "开庭日", "主办律师"],
        },
        "reminder": {
            "keywords": ["提醒", "记得", "别忘了"],
            "default_time": "18:00",
        },
        "chitchat": {
            "whitelist": ["你好", "早上好", "下午好", "谢谢", "帮助", "你能做什么"],
            "sensitive_reject": ["能赢吗", "判多久", "法律建议"],
        },
        "chain": {
            "triggers": [
                {"pattern": r"(查|找).*(总结|汇总)", "skills": ["QuerySkill", "SummarySkill"]},
                {"pattern": r"(总结|汇总).*(今天|明天|案)", "skills": ["QuerySkill", "SummarySkill"]},
            ],
            "max_hops": 2,
        },
    }


# endregion
# ============================================
