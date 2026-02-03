"""
QuerySkill - 案件查询技能

职责：调用 MCP 多维表格/文档搜索工具，返回查询结果
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.skills.base import BaseSkill
from src.core.types import SkillContext, SkillResult

logger = logging.getLogger(__name__)


# ============================================
# region QuerySkill
# ============================================
class QuerySkill(BaseSkill):
    """
    案件查询技能
    
    功能：
    - 解析查询条件（关键词、时间范围）
    - 调用 MCP feishu.v1.bitable.search 或 feishu.v1.doc.search
    - 格式化返回结果
    """
    
    name: str = "QuerySkill"
    description: str = "查询案件、开庭、当事人等信息"

    def __init__(
        self,
        mcp_client: Any,
        settings: Any = None,
    ) -> None:
        """
        Args:
            mcp_client: MCP 客户端实例
            settings: 配置（可选）
        """
        self._mcp = mcp_client
        self._settings = settings

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        执行案件查询
        
        Args:
            context: 执行上下文
            
        Returns:
            SkillResult: 查询结果
        """
        query = context.query
        extra = context.extra

        # 判断查询类型（文档 or 多维表格）
        tool_name = self._select_tool(query)
        
        # 构建查询参数
        params = self._build_params(query, extra)
        
        try:
            # 调用 MCP 工具
            result = await self._mcp.call_tool(tool_name, params)
            
            # 检查结果
            if tool_name == "feishu.v1.doc.search":
                documents = result.get("documents", [])
                if not documents:
                    return self._empty_result("未找到相关文档")
                return self._format_doc_result(documents)
            else:
                records = result.get("records", [])
                if not records:
                    return self._empty_result("未找到相关案件记录")
                return self._format_case_result(records)
                
        except Exception as e:
            logger.error(f"QuerySkill execution error: {e}")
            return SkillResult(
                success=False,
                skill_name=self.name,
                message=str(e),
                reply_text="查询失败，请稍后重试。",
            )

    def _select_tool(self, query: str) -> str:
        """选择查询工具"""
        doc_keywords = ["文档", "资料", "文件", "合同"]
        if any(kw in query for kw in doc_keywords):
            return "feishu.v1.doc.search"
        return "feishu.v1.bitable.search"

    def _build_params(self, query: str, extra: dict[str, Any]) -> dict[str, Any]:
        """构建查询参数"""
        params: dict[str, Any] = {}
        
        # 提取关键词
        keyword = self._extract_keyword(query)
        if keyword:
            params["keyword"] = keyword
            
        # 时间范围（从 extra 获取）
        if extra.get("date_from"):
            params["date_from"] = extra["date_from"]
        if extra.get("date_to"):
            params["date_to"] = extra["date_to"]
            
        return params

    def _extract_keyword(self, query: str) -> str:
        """
        提取关键词（去除常见无效词）
        
        如果过滤后没有有效关键词，返回空字符串（MCP 会查询全部）
        """
        keyword = query
        
        # 查询动作词（需要去除）
        action_words = [
            "找一下", "查一下", "查询", "搜索", "帮我", "请帮我", 
            "一下", "你能", "能不能", "可以", "请",
        ]
        
        # 通用语义词（需要去除，但不是关键词）
        general_words = [
            "案子", "案件", "有什么", "有哪些", "都有哪些", "目前",
            "庭要开", "庭审", "信息", "详情", "的", "吗", "呢",
            "看看", "告诉我", "列出",
        ]
        
        for word in action_words + general_words:
            keyword = keyword.replace(word, "")
        
        keyword = keyword.strip()
        
        # 如果关键词太短或只是常见词，返回空（查询全部）
        if len(keyword) <= 1:
            return ""
            
        return keyword

    def _empty_result(self, message: str) -> SkillResult:
        """空结果"""
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"records": [], "total": 0},
            message=message,
            reply_text=f"{message}，请尝试调整查询条件。",
        )

    def _format_case_result(self, records: list[dict[str, Any]]) -> SkillResult:
        """格式化案件查询结果"""
        count = len(records)
        title = f"📌 案件查询结果（共 {count} 条）"
        
        items = []
        for i, record in enumerate(records, start=1):
            fields = record.get("fields_text") or record.get("fields", {})
            item = (
                f"{i}️⃣ {fields.get('委托人及联系方式', '')} vs {fields.get('对方当事人', '')}｜{fields.get('案由', '')}\n"
                f"   • 案号：{fields.get('案号', '')}\n"
                f"   • 法院：{fields.get('审理法院', '')}\n"
                f"   • 程序：{fields.get('程序阶段', '')}\n"
                f"   • 🔗 查看详情：{record.get('record_url', '')}"
            )
            items.append(item)
        
        reply_text = "\n\n".join([title] + items)
        
        # 构建卡片
        card = self._build_card(title, items)
        
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"records": records, "total": count},
            message=f"查询到 {count} 条记录",
            reply_type="card",
            reply_text=reply_text,
            reply_card=card,
        )

    def _format_doc_result(self, documents: list[dict[str, Any]]) -> SkillResult:
        """格式化文档查询结果"""
        count = len(documents)
        title = f"📄 文档搜索结果（共 {count} 条）"
        
        items = []
        for i, doc in enumerate(documents, start=1):
            item = (
                f"{i}. {doc.get('title', '未命名文档')}\n"
                f"   {doc.get('preview', '')}\n"
                f"   🔗 {doc.get('url', '')}"
            )
            items.append(item)
        
        reply_text = "\n\n".join([title] + items)
        
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"documents": documents, "total": count},
            message=f"搜索到 {count} 篇文档",
            reply_type="text",
            reply_text=reply_text,
        )

    def _build_card(self, title: str, items: list[str]) -> dict[str, Any]:
        """构建飞书消息卡片"""
        elements = [{"tag": "markdown", "content": item} for item in items]
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": elements,
        }
# endregion
# ============================================
