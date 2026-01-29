"""
Skill System 单元测试

测试范围：
- IntentParser（意图解析）
- SkillRouter（技能路由）
- 各 Skill（QuerySkill, SummarySkill, ReminderSkill, ChitchatSkill）
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from src.core.intent import IntentParser, IntentResult, SkillMatch, load_skills_config
from src.core.router import SkillRouter, SkillContext, SkillResult, BaseSkill
from src.core.skills import QuerySkill, SummarySkill, ReminderSkill, ChitchatSkill


# ============================================
# region 测试配置
# ============================================
@pytest.fixture
def skills_config() -> dict:
    """测试用技能配置"""
    return {
        "routing": {
            "rule_threshold": 0.7,
            "llm_confirm_threshold": 0.4,
            "max_hops": 2,
            "fallback_skill": "chitchat",
        },
        "skills": {
            "query": {
                "name": "QuerySkill",
                "description": "查询案件、开庭、当事人等信息",
                "keywords": ["查", "查询", "找", "案件", "案号", "开庭"],
                "weights": {"案号": 1.5, "开庭": 1.3},
            },
            "summary": {
                "name": "SummarySkill",
                "description": "总结、汇总查询结果",
                "keywords": ["总结", "汇总", "概括"],
                "default_fields": ["案号", "案由", "当事人"],
            },
            "reminder": {
                "name": "ReminderSkill",
                "description": "创建提醒、待办",
                "keywords": ["提醒", "待办", "记得"],
                "default_time": "18:00",
            },
            "chitchat": {
                "name": "ChitchatSkill",
                "description": "闲聊、问候",
                "keywords": ["你好", "帮助"],
            },
        },
        "chains": {
            "query_summary": {
                "trigger_keywords": ["帮我总结", "汇总"],
                "sequence": ["query", "summary"],
            }
        },
    }


@pytest.fixture
def mock_mcp_client() -> MagicMock:
    """Mock MCP 客户端"""
    client = MagicMock()
    client.call_tool = AsyncMock(return_value={
        "records": [
            {
                "record_id": "rec123",
                "fields_text": {
                    "案号": "(2025)粤0306民初123号",
                    "案由": "合同纠纷",
                    "委托人及联系方式": "张三",
                    "对方当事人": "李四",
                    "审理法院": "深圳市宝安区人民法院",
                },
                "record_url": "https://example.com/rec123",
            }
        ],
        "total": 1,
    })
    return client


@pytest.fixture
def mock_llm_client() -> MagicMock:
    """Mock LLM 客户端"""
    client = MagicMock()
    client.chat = AsyncMock(return_value="这是一个测试总结")
    client.chat_json = AsyncMock(return_value={
        "skills": [{"name": "QuerySkill", "score": 0.9, "reason": "查询案件"}],
        "is_chain": False,
    })
    return client
# endregion
# ============================================


# ============================================
# region IntentParser 测试
# ============================================
class TestIntentParser:
    """意图解析器测试"""

    def test_rule_match_query(self, skills_config: dict) -> None:
        """测试规则匹配 - 查询意图"""
        parser = IntentParser(skills_config)
        
        # 同步包装异步方法
        import asyncio
        result = asyncio.run(parser.parse("查一下本周的开庭案件"))
        
        assert result.method == "rule"
        assert len(result.skills) > 0
        assert result.skills[0].name == "QuerySkill"
        assert result.skills[0].score >= 0.7

    def test_rule_match_reminder(self, skills_config: dict) -> None:
        """测试规则匹配 - 提醒意图"""
        parser = IntentParser(skills_config)
        
        import asyncio
        result = asyncio.run(parser.parse("提醒我明天开会"))
        
        assert result.skills[0].name == "ReminderSkill"

    def test_chain_detection(self, skills_config: dict) -> None:
        """测试链式意图检测"""
        parser = IntentParser(skills_config)
        
        import asyncio
        result = asyncio.run(parser.parse("帮我总结本周的庭审"))
        
        assert result.is_chain is True

    def test_fallback(self, skills_config: dict) -> None:
        """测试兜底技能"""
        parser = IntentParser(skills_config)
        
        import asyncio
        result = asyncio.run(parser.parse("这是一个无法识别的句子xyz"))
        
        # 应该返回兜底技能
        assert result.method == "fallback"
        assert result.skills[0].name == "ChitchatSkill"
# endregion
# ============================================


# ============================================
# region SkillRouter 测试
# ============================================
class TestSkillRouter:
    """技能路由器测试"""

    def test_skill_registration(self, skills_config: dict) -> None:
        """测试技能注册"""
        router = SkillRouter(skills_config)
        
        # 创建 Mock 技能
        skill = MagicMock(spec=BaseSkill)
        skill.name = "TestSkill"
        
        router.register(skill)
        
        assert "TestSkill" in router.list_skills()
        assert router.get_skill("TestSkill") == skill

    @pytest.mark.asyncio
    async def test_route_single_skill(
        self,
        skills_config: dict,
        mock_mcp_client: MagicMock,
    ) -> None:
        """测试单技能路由"""
        router = SkillRouter(skills_config)
        
        # 注册 QuerySkill
        query_skill = QuerySkill(mcp_client=mock_mcp_client)
        router.register(query_skill)
        
        # 构建意图和上下文
        intent = IntentResult(
            skills=[SkillMatch(name="QuerySkill", score=0.9, reason="test")],
            is_chain=False,
            method="rule",
        )
        context = SkillContext(query="查一下案件", user_id="test_user")
        
        # 执行路由
        result = await router.route(intent, context)
        
        assert result.success is True
        assert result.skill_name == "QuerySkill"
        assert "records" in result.data
# endregion
# ============================================


# ============================================
# region QuerySkill 测试
# ============================================
class TestQuerySkill:
    """案件查询技能测试"""

    @pytest.mark.asyncio
    async def test_execute_bitable_search(self, mock_mcp_client: MagicMock) -> None:
        """测试多维表格查询"""
        skill = QuerySkill(mcp_client=mock_mcp_client)
        context = SkillContext(query="查一下张三的案子", user_id="test")
        
        result = await skill.execute(context)
        
        assert result.success is True
        assert result.data["total"] == 1
        mock_mcp_client.call_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_doc_search(self, mock_mcp_client: MagicMock) -> None:
        """测试文档搜索"""
        mock_mcp_client.call_tool = AsyncMock(return_value={
            "documents": [{"title": "合同模板", "url": "https://..."}],
        })
        
        skill = QuerySkill(mcp_client=mock_mcp_client)
        context = SkillContext(query="找一下合同文档", user_id="test")
        
        result = await skill.execute(context)
        
        assert result.success is True
        assert "documents" in result.data
# endregion
# ============================================


# ============================================
# region SummarySkill 测试
# ============================================
class TestSummarySkill:
    """汇总技能测试"""

    @pytest.mark.asyncio
    async def test_no_data(self, skills_config: dict) -> None:
        """测试无数据时的处理"""
        skill = SummarySkill(skills_config=skills_config)
        context = SkillContext(query="总结一下", user_id="test", last_result=None)
        
        result = await skill.execute(context)
        
        assert result.success is False
        assert "请先查询" in result.reply_text

    @pytest.mark.asyncio
    async def test_template_summary(self, skills_config: dict) -> None:
        """测试模板汇总（无 LLM）"""
        skill = SummarySkill(skills_config=skills_config)
        context = SkillContext(
            query="总结一下",
            user_id="test",
            last_result={
                "records": [
                    {"fields_text": {"案号": "123", "案由": "合同纠纷", "当事人": "张三"}}
                ]
            },
        )
        
        result = await skill.execute(context)
        
        assert result.success is True
        assert "案件汇总" in result.reply_text
# endregion
# ============================================


# ============================================
# region ReminderSkill 测试
# ============================================
class TestReminderSkill:
    """提醒技能测试"""

    @pytest.mark.asyncio
    async def test_create_reminder(self, skills_config: dict) -> None:
        """测试创建提醒"""
        skill = ReminderSkill(skills_config=skills_config)
        context = SkillContext(query="提醒我明天下午3点开会", user_id="test")
        
        result = await skill.execute(context)
        
        assert result.success is True
        assert "提醒已创建" in result.reply_text
        assert "开会" in result.data["content"]

    @pytest.mark.asyncio
    async def test_default_time(self, skills_config: dict) -> None:
        """测试默认时间"""
        skill = ReminderSkill(skills_config=skills_config)
        context = SkillContext(query="提醒我开会", user_id="test")
        
        result = await skill.execute(context)
        
        assert result.success is True
        assert "18:00" in result.reply_text or "已设置为" in result.reply_text
# endregion
# ============================================


# ============================================
# region ChitchatSkill 测试
# ============================================
class TestChitchatSkill:
    """闲聊技能测试"""

    @pytest.mark.asyncio
    async def test_greeting(self, skills_config: dict) -> None:
        """测试问候响应"""
        skill = ChitchatSkill(skills_config=skills_config)
        context = SkillContext(query="你好", user_id="test")
        
        result = await skill.execute(context)
        
        assert result.success is True
        assert "你好" in result.reply_text

    @pytest.mark.asyncio
    async def test_help(self, skills_config: dict) -> None:
        """测试帮助响应"""
        skill = ChitchatSkill(skills_config=skills_config)
        context = SkillContext(query="帮助", user_id="test")
        
        result = await skill.execute(context)
        
        assert result.success is True
        assert "查询案件" in result.reply_text

    @pytest.mark.asyncio
    async def test_fallback(self, skills_config: dict) -> None:
        """测试兜底响应"""
        skill = ChitchatSkill(skills_config=skills_config)
        context = SkillContext(query="随便说点什么", user_id="test")
        
        result = await skill.execute(context)
        
        assert result.success is True
        assert result.data["type"] == "fallback"
# endregion
# ============================================


# ============================================
# region 集成测试
# ============================================
class TestIntegration:
    """集成测试"""

    @pytest.mark.asyncio
    async def test_full_flow(
        self,
        skills_config: dict,
        mock_mcp_client: MagicMock,
        mock_llm_client: MagicMock,
    ) -> None:
        """测试完整流程：解析 -> 路由 -> 执行"""
        # 初始化
        parser = IntentParser(skills_config)
        router = SkillRouter(skills_config)
        
        # 注册技能
        router.register(QuerySkill(mcp_client=mock_mcp_client))
        router.register(SummarySkill(llm_client=mock_llm_client, skills_config=skills_config))
        router.register(ChitchatSkill(skills_config=skills_config))
        
        # 解析意图
        intent = await parser.parse("查一下本周的开庭案件")
        assert intent.skills[0].name == "QuerySkill"
        
        # 路由执行
        context = SkillContext(query="查一下本周的开庭案件", user_id="test")
        result = await router.route(intent, context)
        
        assert result.success is True
        assert result.skill_name == "QuerySkill"
# endregion
# ============================================
