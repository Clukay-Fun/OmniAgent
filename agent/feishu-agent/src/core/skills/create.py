"""
描述: 案件记录创建技能
主要功能:
    - 解析用户输入中的字段信息
    - 调用 MCP 接口创建多维表格记录
    - 返回创建结果及记录链接
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.core.skills.bitable_adapter import BitableAdapter
from src.core.skills.base import BaseSkill
from src.core.types import SkillContext, SkillResult

logger = logging.getLogger(__name__)


# region 案件创建技能
class CreateSkill(BaseSkill):
    """
    新建案件技能

    功能:
        - 识别自然语言中的案件信息（如律师、当事人等）
        - 映射用户别名到标准字段名
        - 调用 MCP 执行创建操作
    """
    
    name: str = "CreateSkill"
    description: str = "创建新的案件记录"

    def __init__(
        self,
        mcp_client: Any,
        settings: Any = None,
        skills_config: dict[str, Any] | None = None,
    ) -> None:
        """
        初始化创建技能

        参数:
            mcp_client: MCP 客户端实例
            settings: 配置信息
        """
        self._mcp = mcp_client
        self._settings = settings
        self._table_adapter = BitableAdapter(mcp_client, skills_config=skills_config)
        
        # 字段映射：用户可能使用的别名 -> 实际字段名
        self._field_aliases = {
            "律师": "主办律师",
            "主办律师": "主办律师",
            "委托人": "委托人",
            "客户": "委托人",
            "对方": "对方当事人",
            "被告": "对方当事人",
            "原告": "对方当事人",
            "案号": "案号",
            "案由": "案由",
            "法院": "审理法院",
            "阶段": "程序阶段",
            "程序": "程序阶段",
            "开庭日": "开庭日",
            "开庭": "开庭日",
            "法官": "承办法官",
            "进展": "进展",
            "待办": "待做事项",
            "备注": "备注",
        }

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        执行创建逻辑

        参数:
            context: 技能上下文

        返回:
            技能执行结果
        """
        query = context.query
        extra = context.extra or {}
        planner_plan = extra.get("planner_plan") if isinstance(extra.get("planner_plan"), dict) else None
        table_ctx = await self._table_adapter.resolve_table_context(query, extra, context.last_result)

        # 优先使用 planner 参数，规则解析做补充
        fields = self._extract_fields_from_planner(planner_plan)
        parsed_fields = self._parse_fields(query)
        for k, v in parsed_fields.items():
            fields.setdefault(k, v)
        
        if not fields:
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"parsed_fields": {}},
                message="未能解析出有效字段",
                reply_text="请告诉我要创建的案件信息，例如：\n"
                           "「新增案件，主办律师是张三，委托人是XX公司，案由是合同纠纷」",
            )

        adapted_fields, unresolved, available = await self._table_adapter.adapt_fields_for_table(
            fields,
            table_ctx.table_id,
        )
        if unresolved:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={
                    "table_id": table_ctx.table_id,
                    "table_name": table_ctx.table_name,
                    "unresolved_fields": unresolved,
                    "available_fields": available,
                },
                message="字段名与目标表不匹配",
                reply_text=self._table_adapter.build_field_not_found_message(
                    unresolved,
                    available,
                    table_ctx.table_name,
                ),
            )

        if adapted_fields:
            fields = adapted_fields
        
        try:
            # 调用 MCP 创建记录
            params: dict[str, Any] = {"fields": fields}
            if table_ctx.table_id:
                params["table_id"] = table_ctx.table_id
            result = await self._mcp.call_tool(
                "feishu.v1.bitable.record.create",
                params,
            )
            
            if not result.get("success"):
                return SkillResult(
                    success=False,
                    skill_name=self.name,
                    message=result.get("error", "创建失败"),
                    reply_text="创建记录失败，请稍后重试。",
                )
            
            record_url = result.get("record_url", "")
            record_id = result.get("record_id", "")
            
            # 格式化已创建的字段
            fields_text = "\n".join([f"• {k}：{v}" for k, v in fields.items()])
            
            reply_text = (
                f"✅ 案件记录已创建！\n\n"
                f"{fields_text}\n\n"
            )
            if record_url:
                reply_text += f"🔗 查看详情：{record_url}"
            
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={
                    "record_id": record_id,
                    "fields": fields,
                    "record_url": record_url,
                    "table_id": table_ctx.table_id,
                    "table_name": table_ctx.table_name,
                },
                message="创建成功",
                reply_text=reply_text,
            )
                
        except Exception as e:
            logger.error(f"CreateSkill execution error: {e}")
            return SkillResult(
                success=False,
                skill_name=self.name,
                message=str(e),
                reply_text="创建记录失败，请稍后重试。",
            )

    def _parse_fields(self, query: str) -> dict[str, Any]:
        """
        解析用户输入字段

        支持格式:
            - "主办律师是张三，委托人是XX公司"
            - "律师：张三，委托人：XX公司"

        参数:
            query: 用户输入文本
        返回:
            解析后的字段字典
        """
        fields: dict[str, Any] = {}
        
        # 模式1：字段是/为值
        pattern1 = r"([^\s,，、]+?)(?:是|为|：|:)\s*([^\s,，、是为：:]+)"
        matches = re.findall(pattern1, query)
        
        for alias, value in matches:
            alias = alias.strip()
            value = value.strip()
            
            # 查找实际字段名
            actual_field = self._field_aliases.get(alias, alias)
            if actual_field and value:
                fields[actual_field] = value
        
        return fields

    def _extract_fields_from_planner(self, planner_plan: dict[str, Any] | None) -> dict[str, Any]:
        """从 planner 输出中提取 fields。"""
        if not isinstance(planner_plan, dict):
            return {}
        if planner_plan.get("tool") != "record.create":
            return {}

        params = planner_plan.get("params")
        if not isinstance(params, dict):
            return {}

        fields_raw = params.get("fields")
        if not isinstance(fields_raw, dict):
            return {}

        fields: dict[str, Any] = {}
        for key, value in fields_raw.items():
            field_name = str(key).strip()
            if not field_name:
                continue
            fields[field_name] = value
        return fields
# endregion
