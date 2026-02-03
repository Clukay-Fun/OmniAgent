"""
ChitchatSkill - 闲聊/兜底技能

职责：处理问候、帮助请求、无法识别的输入
采用受限聊天策略：白名单问候 + 敏感话题拒答 + 引导到核心功能
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.skills.base import BaseSkill
from src.core.types import SkillContext, SkillResult

logger = logging.getLogger(__name__)


# ============================================
# region ChitchatSkill
# ============================================
class ChitchatSkill(BaseSkill):
    """
    闲聊/兜底技能
    
    策略：
    - 白名单问候：直接友好响应
    - 帮助请求：返回功能引导
    - 敏感话题：礼貌拒答
    - 其他：引导到核心功能
    """
    
    name: str = "ChitchatSkill"
    description: str = "闲聊、问候、无法识别的请求"

    # 问候词
    GREETINGS = [
        "你好", "您好", "嗨", "hi", "hello",
        "早上好", "上午好", "中午好", "下午好", "晚上好",
        "在吗", "在不在",
    ]
    
    # 感谢词
    THANKS = ["谢谢", "多谢", "感谢", "辛苦", "thank"]
    
    # 告别词
    GOODBYES = ["再见", "拜拜", "bye", "回头见", "下次见"]
    
    # 帮助请求
    HELP_TRIGGERS = [
        "帮助",
        "怎么用",
        "能做什么",
        "你能做什么",
        "功能",
        "help",
        "?",
        "？",
    ]
    
    # 敏感话题（拒答）
    SENSITIVE_TOPICS = ["政治", "敏感", "违法", "色情"]

    # 响应模板
    RESPONSES = {
        "greeting": "您好！我是小律，您的智能律师助理。\n\n有什么可以帮您的？输入“帮助”查看功能列表。",
        "thanks": "不客气！如需查询案件或文档，随时告诉我。",
        "goodbye": "好的，如有需要随时找我。再见！",
        "help": (
            "📋 **我可以帮您：**\n\n"
            "1. **查询案件** - 查看案件信息、进展\n"
            "   - \"今天有什么庭\"\n"
            "   - \"查一下张三的案件\"\n\n"
            "2. **庭审日程** - 查看开庭安排\n"
            "   - \"明天有什么庭\"\n"
            "   - \"本周开庭安排\"\n\n"
            "3. **设置提醒** - 待办事项管理\n"
            "   - \"提醒我明天准备材料\"\n"
            "   - \"我有哪些提醒\"\n\n"
            "4. **生成摘要** - 案件信息汇总\n"
            "   - \"帮我总结今天的案子\"\n\n"
            "请问需要什么帮助？"
        ),
        "sensitive": "抱歉，这个话题我无法回答。我是案件助手，专注于帮您查询案件和文档。",
        "fallback": '抱歉，我暂时无法理解您的问题。试试问我"本周有什么庭"或"帮助"查看功能介绍。',
    }

    def __init__(
        self,
        skills_config: dict[str, Any] | None = None,
    ) -> None:
        """
        Args:
            skills_config: skills.yaml 配置
        """
        self._config = skills_config or {}
        
        # 从配置加载自定义设置
        chitchat_cfg = self._config.get("chitchat", {})
        if not chitchat_cfg:
            chitchat_cfg = self._config.get("skills", {}).get("chitchat", {})

        self._greetings = chitchat_cfg.get("greetings", self.GREETINGS)
        self._help_triggers = chitchat_cfg.get("help_triggers", self.HELP_TRIGGERS)
        self._sensitive_topics = chitchat_cfg.get(
            "sensitive_reject",
            chitchat_cfg.get("sensitive_topics", self.SENSITIVE_TOPICS),
        )
        fallback_response = chitchat_cfg.get("fallback_response")
        if not fallback_response:
            fallback_response = self.RESPONSES["fallback"]
        self._fallback_response = fallback_response

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        执行闲聊响应
        
        Args:
            context: 执行上下文
            
        Returns:
            SkillResult: 响应结果
        """
        query = context.query.lower().strip()
        original_query = context.query
        
        # 1. 检查敏感话题
        if self._is_sensitive(original_query):
            return self._create_result("sensitive", "敏感话题拒答")
        
        # 2. 检查帮助请求
        if self._is_help_request(original_query):
            return self._create_result("help", "帮助响应")

        # 3. 检查感谢
        if self._is_thanks(original_query):
            return self._create_result("thanks", "感谢响应")

        # 4. 检查告别
        if self._is_goodbye(original_query):
            return self._create_result("goodbye", "告别响应")

        # 5. 检查问候
        if self._is_greeting(original_query):
            return self._create_result("greeting", "问候响应")
        
        # 6. 兜底
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"type": "fallback"},
            message="兜底响应",
            reply_text=self._fallback_response,
        )

    def _is_greeting(self, query: str) -> bool:
        """检查是否为问候"""
        query_lower = query.lower()
        return any(
            g in query or g.lower() in query_lower
            for g in self._greetings
        )

    def _is_thanks(self, query: str) -> bool:
        """检查是否为感谢"""
        query_lower = query.lower()
        return any(
            t in query or t.lower() in query_lower
            for t in self.THANKS
        )

    def _is_goodbye(self, query: str) -> bool:
        """检查是否为告别"""
        query_lower = query.lower()
        return any(
            g in query or g.lower() in query_lower
            for g in self.GOODBYES
        )

    def _is_help_request(self, query: str) -> bool:
        """检查是否为帮助请求"""
        query_lower = query.lower()
        return any(
            h in query or h.lower() in query_lower
            for h in self._help_triggers
        )

    def _is_sensitive(self, query: str) -> bool:
        """检查是否为敏感话题"""
        return any(topic in query for topic in self._sensitive_topics)

    def _create_result(self, response_type: str, message: str) -> SkillResult:
        """创建响应结果"""
        reply_text = self.RESPONSES.get(response_type) or self._fallback_response
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"type": response_type},
            message=message,
            reply_text=reply_text,
        )
# endregion
# ============================================
