"""
ChitchatSkill - 自由对话技能

职责：处理问候、帮助请求，以及使用 LLM 进行自由对话
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
    自由对话技能
    
    策略：
    - 问候：友好响应
    - 帮助请求：返回功能引导
    - 其他：使用 LLM 自由对话
    """
    
    name: str = "ChitchatSkill"
    description: str = "闲聊、问候、自由对话"

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
    ]

    # 响应模板
    RESPONSES = {
        "greeting": "您好！我是小律，您的智能助理。有什么可以帮您的？",
        "thanks": "不客气！如果还有其他问题，随时问我。",
        "goodbye": "好的，再见！如有需要随时找我。",
        "help": (
            "📋 **我可以帮您：**\n\n"
            "1. **查询案件** - 查看案件信息、进展\n"
            "   - \"今天有什么庭\"\n"
            "   - \"查一下张三的案件\"\n\n"
            "2. **庭审日程** - 查看开庭安排\n"
            "   - \"明天有什么庭\"\n"
            "   - \"本周开庭安排\"\n\n"
            "3. **设置提醒** - 待办事项管理\n"
            "   - \"提醒我明天准备材料\"\n\n"
            "4. **自由对话** - 随便聊聊\n"
            "   - 任何问题都可以问我\n\n"
            "请问需要什么帮助？"
        ),
    }

    def __init__(
        self,
        skills_config: dict[str, Any] | None = None,
        llm_client: Any | None = None,
    ) -> None:
        """
        Args:
            skills_config: skills.yaml 配置
            llm_client: LLM 客户端（用于自由对话）
        """
        self._config = skills_config or {}
        self._llm_client = llm_client
        
        # 从配置加载自定义设置
        chitchat_cfg = self._config.get("chitchat", {})
        if not chitchat_cfg:
            chitchat_cfg = self._config.get("skills", {}).get("chitchat", {})

        self._greetings = chitchat_cfg.get("greetings", self.GREETINGS)
        self._help_triggers = chitchat_cfg.get("help_triggers", self.HELP_TRIGGERS)

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        执行对话响应
        
        Args:
            context: 执行上下文
            
        Returns:
            SkillResult: 响应结果
        """
        query = context.query.strip()
        
        # 1. 检查帮助请求
        if self._is_help_request(query):
            return self._create_result("help", "帮助响应")

        # 2. 检查感谢
        if self._is_thanks(query):
            return self._create_result("thanks", "感谢响应")

        # 3. 检查告别
        if self._is_goodbye(query):
            return self._create_result("goodbye", "告别响应")

        # 4. 检查问候
        if self._is_greeting(query):
            return self._create_result("greeting", "问候响应")
        
        # 5. 使用 LLM 自由对话
        return await self._llm_chat(query, context)

    async def _llm_chat(self, query: str, context: SkillContext) -> SkillResult:
        """使用 LLM 进行自由对话"""
        if not self._llm_client:
            # 如果没有 LLM 客户端，返回友好提示
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"type": "no_llm"},
                message="无 LLM 客户端",
                reply_text="抱歉，我暂时无法回答这个问题。试试问我\"帮助\"看看我能做什么。",
            )
        
        try:
            # 构建对话消息
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一个友好、智能的助理。请用简洁、自然的中文回答用户的问题。"
                        "如果用户的问题涉及案件查询、开庭安排等，"
                        "可以告诉他们使用相关功能，比如\"你可以问我'今天有什么庭'\"。"
                    ),
                },
                {"role": "user", "content": query},
            ]
            
            # 调用 LLM
            response = await self._llm_client.chat(messages)
            reply_text = response if isinstance(response, str) else response.get("content", "")
            
            if not reply_text:
                reply_text = "我理解了您的问题，但暂时不太确定怎么回答。换个方式问问我？"
            
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"type": "llm_chat", "query": query},
                message="LLM 对话",
                reply_text=reply_text,
            )
            
        except Exception as e:
            logger.error(f"LLM chat error: {e}", exc_info=True)
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"type": "llm_error", "error": str(e)},
                message="LLM 调用失败",
                reply_text="抱歉，我遇到了一些问题。请稍后再试。",
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
            t in query or t.lower() in query_lower
            for t in self._help_triggers
        )

    def _create_result(self, response_type: str, message: str) -> SkillResult:
        """创建模板响应结果"""
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"type": response_type},
            message=message,
            reply_text=self.RESPONSES.get(response_type, ""),
        )
# endregion
# ============================================
