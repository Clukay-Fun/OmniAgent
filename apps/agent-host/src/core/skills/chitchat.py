"""
描述: 自由对话技能
主要功能:
    - 处理日常问候和感谢
    - 提供功能帮助引导
    - 使用 LLM 进行开放式对话
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from src.core.skills.base import BaseSkill
from src.core.types import SkillContext, SkillResult
from src.utils.metrics import record_chitchat_guard

logger = logging.getLogger(__name__)


# region 自由对话技能
class ChitchatSkill(BaseSkill):
    """
    自由对话技能

    策略:
        - 问候：返回预设友好响应
        - 帮助：返回功能使用指南
        - 其他：调用 LLM 进行自由回答
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
        "你是谁",
        "你是干嘛的",
        "你会什么",
        "你有什么功能",
        "你能帮我什么",
        "功能",
        "help",
    ]

    DOMAIN_HINTS = [
        "案件", "案号", "项目", "开庭", "庭审", "法院", "律师", "当事人",
        "委托人", "主办", "协办", "进展", "待办", "提醒", "查询", "新增", "修改", "删除",
    ]

    # ============================================
    # region 默认回复随机池（YAML 加载失败时的兆底）
    # ============================================
    DEFAULT_RESPONSES = {
        "greeting": ["您好！有什么可以帮您的？"],
        "greeting_morning": ["早上好！今天有什么需要处理的吗？"],
        "greeting_evening": ["晚上好！还有什么需要处理的吗？"],
        "thanks": ["不客气！有其他问题随时问我。"],
        "goodbye": ["好的，再见！如有需要随时找我。"],
        "out_of_scope": ["案件相关的事可以问我哦～"],
        "help": ["请问需要什么帮助？"],
        "result_opener": ["查到啦~ "],
        "empty_result": ["未找到相关记录。"],
    }
    # endregion
    # ============================================

    def __init__(
        self,
        skills_config: dict[str, Any] | None = None,
        llm_client: Any | None = None,
    ) -> None:
        """
        初始化自由对话技能

        参数:
            skills_config: 技能配置
            llm_client: LLM 客户端实例
        """
        self._config = skills_config or {}
        self._llm_client = llm_client

        # ============================================
        # region 加载回复模板配置
        # ============================================
        self._responses = self._load_responses()
        # endregion
        # ============================================
        
        # 从配置加载自定义设置
        chitchat_cfg = self._config.get("chitchat", {})
        if not chitchat_cfg:
            chitchat_cfg = self._config.get("skills", {}).get("chitchat", {})

        self._greetings = chitchat_cfg.get("greetings", self.GREETINGS)
        self._help_triggers = chitchat_cfg.get("help_triggers", self.HELP_TRIGGERS)
        self._allow_llm = bool(chitchat_cfg.get("allow_llm", False))

        casual_pool = self._load_casual_pool()
        if casual_pool:
            self._responses["out_of_scope"] = casual_pool

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        执行对话响应

        参数:
            context: 技能上下文

        返回:
            对话响应结果
        """
        query = context.query.strip()
        
        # 1. 检查帮助请求
        if self._is_help_request(query):
            record_chitchat_guard("pool")
            return self._create_result("help", "帮助响应")

        # 2. 检查感谢
        if self._is_thanks(query):
            record_chitchat_guard("pool")
            return self._create_result("thanks", "感谢响应")

        # 3. 检查告别
        if self._is_goodbye(query):
            record_chitchat_guard("pool")
            return self._create_result("goodbye", "告别响应")

        # 4. 检查问候（带时间感知）
        if self._is_greeting(query):
            greeting_type = self._get_time_greeting_type()
            record_chitchat_guard("pool")
            return self._create_result(greeting_type, "问候响应")

        # 4.1 明显离题时柔性收敛到业务域
        if not self._is_domain_related(query):
            record_chitchat_guard("blocked")
            return self._create_result("out_of_scope", "离题请求")

        if not self._allow_llm:
            record_chitchat_guard("blocked")
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"type": "guard_blocked"},
                message="闲聊门控拦截",
                reply_text=self.get_response("out_of_scope") or "我先不展开闲聊啦，我们继续案件相关内容吧。",
            )

        # 5. 使用 LLM 自由对话
        record_chitchat_guard("llm")
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

    def _is_domain_related(self, query: str) -> bool:
        query_lower = query.lower()
        return any(token in query or token.lower() in query_lower for token in self.DOMAIN_HINTS)

    # ============================================
    # region 配置加载 + 时间感知 + 随机选择
    # ============================================
    def _load_responses(self) -> dict[str, list[str]]:
        """从 config/responses.yaml 加载回复模板，加载失败时用默认值"""
        responses_path = Path("config/responses.yaml")
        if not responses_path.exists():
            logger.warning("responses.yaml not found at %s, using defaults", responses_path)
            return dict(self.DEFAULT_RESPONSES)
        try:
            data = yaml.safe_load(responses_path.read_text(encoding="utf-8")) or {}
            # 确保每个 key 的值都是列表
            result = dict(self.DEFAULT_RESPONSES)
            for key, value in data.items():
                if isinstance(value, list) and value:
                    result[key] = value
                elif isinstance(value, str) and value:
                    result[key] = [value]
            logger.info("Loaded responses from %s (%d types)", responses_path, len(result))
            return result
        except Exception as exc:
            logger.error("Failed to load responses.yaml: %s, using defaults", exc)
            return dict(self.DEFAULT_RESPONSES)

    def _load_casual_pool(self) -> list[str]:
        """加载闲聊降级随机池（可选）。"""
        casual_path = Path("config/responses/casual.yaml")
        if not casual_path.exists():
            return []
        try:
            payload = yaml.safe_load(casual_path.read_text(encoding="utf-8")) or {}
            if isinstance(payload, list):
                return [str(item).strip() for item in payload if str(item).strip()]
            if isinstance(payload, dict):
                values = payload.get("responses")
                if isinstance(values, list):
                    return [str(item).strip() for item in values if str(item).strip()]
            return []
        except Exception as exc:
            logger.warning("Failed to load casual pool: %s", exc)
            return []

    def get_response(self, response_type: str) -> str:
        """公开方法：从随机池中随机选择一条回复（供其他 Skill 调用）"""
        pool = self._responses.get(response_type)
        if not pool:
            return ""
        return random.choice(pool)

    def _get_time_greeting_type(self) -> str:
        """根据当前时间选择问候类型"""
        hour = datetime.now().hour
        if hour < 11:
            return "greeting_morning"
        elif hour >= 18:
            return "greeting_evening"
        return "greeting"

    def _create_result(self, response_type: str, message: str) -> SkillResult:
        """从随机池中选择一条回复"""
        reply = self.get_response(response_type)
        if not reply:
            reply = self.get_response("greeting")
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"type": response_type},
            message=message,
            reply_text=reply,
        )
    # endregion
    # ============================================
# endregion
