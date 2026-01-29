"""
Intent parser with rule-based matching and LLM fallback.
Output fixed JSON: skills Top-3 + score + reason + is_chain
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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
    method: str = "rule"  # rule / llm / fallback

    def to_dict(self) -> dict[str, Any]:
        return {
            "skills": [s.to_dict() for s in self.skills],
            "is_chain": self.is_chain,
            "method": self.method,
        }

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
    
    策略：
    1. 规则匹配：基于关键词计算各技能得分
    2. 分数 >= rule_threshold（0.7）：直接命中
    3. 分数介于 llm_confirm_threshold（0.4）和 rule_threshold 之间：调用 LLM 确认
    4. 分数 < llm_confirm_threshold：LLM 完全判断
    5. LLM 超时或失败：降级到 fallback_skill（chitchat）
    """

    def __init__(
        self,
        skills_config: dict[str, Any],
        llm_client: Any = None,
    ) -> None:
        """
        Args:
            skills_config: skills.yaml 加载后的配置字典
            llm_client: LLM 客户端（用于兜底分类）
        """
        self._config = skills_config
        self._llm = llm_client
        self._routing = skills_config.get("routing", {})
        self._skills = skills_config.get("skills", {})
        self._chains = skills_config.get("chains", {})

        # 路由阈值
        self._rule_threshold = self._routing.get("rule_threshold", 0.7)
        self._llm_confirm_threshold = self._routing.get("llm_confirm_threshold", 0.4)
        self._fallback_skill = self._routing.get("fallback_skill", "chitchat")

    async def parse(self, query: str) -> IntentResult:
        """
        解析用户输入，返回意图识别结果
        
        Args:
            query: 用户输入文本
            
        Returns:
            IntentResult: 包含 skills Top-3、is_chain、method
        """
        # Step 1: 规则匹配
        rule_matches = self._rule_match(query)
        top_score = rule_matches[0].score if rule_matches else 0.0

        # Step 2: 检测链式意图
        is_chain = self._detect_chain(query)

        # Step 3: 根据分数决定策略
        if top_score >= self._rule_threshold:
            # 规则直接命中
            logger.info(
                "Intent parsed by rule",
                extra={
                    "query": query,
                    "top_skill": rule_matches[0].name,
                    "score": top_score,
                    "method": "rule",
                },
            )
            return IntentResult(
                skills=rule_matches[:3],
                is_chain=is_chain,
                method="rule",
            )

        if top_score >= self._llm_confirm_threshold:
            if self._llm:
                # 需要 LLM 确认
                try:
                    llm_result = await self._llm_classify(query, rule_matches[:3])
                    if llm_result:
                        llm_result.method = "llm"
                        llm_result.is_chain = is_chain or llm_result.is_chain
                        return llm_result
                except Exception as e:
                    logger.warning(f"LLM classification failed: {e}, falling back to rule")

            # 无 LLM 或 LLM 失败，使用规则结果
            return IntentResult(
                skills=rule_matches[:3],
                is_chain=is_chain,
                method="rule",
            )

        if self._llm:
            # 规则分数过低，完全依赖 LLM
            try:
                llm_result = await self._llm_classify(query)
                if llm_result:
                    llm_result.method = "llm"
                    return llm_result
            except Exception as e:
                logger.warning(f"LLM classification failed: {e}")

        # 兜底：返回 fallback_skill
        fallback_match = SkillMatch(
            name=self._get_skill_name(self._fallback_skill),
            score=0.0,
            reason="无法识别意图，使用兜底技能",
        )
        return IntentResult(
            skills=[fallback_match],
            is_chain=False,
            method="fallback",
        )

    def _rule_match(self, query: str) -> list[SkillMatch]:
        """
        基于关键词的规则匹配
        
        算法：
        1. 遍历各技能的关键词
        2. 统计命中关键词数量 * 权重
        3. 归一化为 0-1 分数
        """
        matches: list[SkillMatch] = []
        query_lower = query.lower()

        for skill_key, skill_cfg in self._skills.items():
            keywords = skill_cfg.get("keywords", [])
            weights = skill_cfg.get("weights", {})
            skill_name = skill_cfg.get("name", skill_key)

            if not keywords:
                continue

            # 计算匹配得分
            hit_count = 0
            hit_keywords = []
            total_weight = 0.0

            for kw in keywords:
                weight = weights.get(kw, 1.0)
                if kw in query or kw.lower() in query_lower:
                    hit_count += 1
                    hit_keywords.append(kw)
                    total_weight += weight

            if hit_count == 0:
                continue

            # 归一化分数：命中权重 / (关键词数量 * 平均权重)
            avg_weight = sum(weights.get(kw, 1.0) for kw in keywords) / len(keywords)
            max_possible = len(keywords) * avg_weight
            score = min(total_weight / max_possible, 1.0) if max_possible > 0 else 0.0

            # 增加命中密度加成（命中多个关键词时加分）
            density_bonus = min(hit_count * 0.1, 0.3)
            score = min(score + density_bonus, 1.0)

            reason = f"命中关键词: {', '.join(hit_keywords[:3])}"
            if len(hit_keywords) > 3:
                reason += f" 等{len(hit_keywords)}个"

            matches.append(SkillMatch(name=skill_name, score=score, reason=reason))

        # 按分数降序排列
        matches.sort(key=lambda x: x.score, reverse=True)
        return matches

    def _detect_chain(self, query: str) -> bool:
        """检测是否为链式意图（如：查询+总结）"""
        for chain_key, chain_cfg in self._chains.items():
            triggers = chain_cfg.get("trigger_keywords", [])
            for trigger in triggers:
                if trigger in query:
                    return True
        return False

    def _get_skill_name(self, skill_key: str) -> str:
        """根据 skill key 获取 skill name"""
        skill_cfg = self._skills.get(skill_key, {})
        return skill_cfg.get("name", skill_key)

    async def _llm_classify(
        self,
        query: str,
        hints: list[SkillMatch] | None = None,
    ) -> IntentResult | None:
        """
        使用 LLM 进行意图分类
        
        Args:
            query: 用户输入
            hints: 规则匹配的初步结果（可作为参考）
            
        Returns:
            IntentResult 或 None（失败时）
        """
        if not self._llm:
            return None

        # 构建 Prompt
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
  "skills": [
    {{"name": "技能名", "score": 0.0-1.0, "reason": "简短理由"}}
  ],
  "is_chain": false
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

            # 解析 LLM 响应
            skills_data = response.get("skills", [])
            skills = []
            for s in skills_data[:3]:
                skills.append(SkillMatch(
                    name=s.get("name", ""),
                    score=float(s.get("score", 0.0)),
                    reason=s.get("reason", ""),
                ))

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
    """
    加载 skills.yaml 配置
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        配置字典
    """
    import yaml
    from pathlib import Path

    path = Path(config_path)
    if not path.exists():
        logger.warning(f"Skills config not found: {config_path}, using defaults")
        return _default_skills_config()

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _default_skills_config() -> dict[str, Any]:
    """默认配置（配置文件不存在时使用）"""
    return {
        "routing": {
            "rule_threshold": 0.7,
            "llm_confirm_threshold": 0.4,
            "max_hops": 2,
            "llm_timeout": 10,
            "fallback_skill": "chitchat",
        },
        "skills": {
            "query": {
                "name": "QuerySkill",
                "description": "查询案件、开庭、当事人等信息",
                "keywords": ["查", "案", "开庭", "案号"],
            },
            "summary": {
                "name": "SummarySkill",
                "description": "总结、汇总查询结果",
                "keywords": ["总结", "汇总", "概括"],
            },
            "reminder": {
                "name": "ReminderSkill",
                "description": "创建提醒、待办",
                "keywords": ["提醒", "待办", "记得"],
            },
            "chitchat": {
                "name": "ChitchatSkill",
                "description": "闲聊、问候",
                "keywords": ["你好", "帮助"],
            },
        },
        "chains": {},
    }
# endregion
# ============================================
