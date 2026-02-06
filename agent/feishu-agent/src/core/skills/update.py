"""
描述: 案件更新技能
主要功能:
    - 更新案件记录字段
    - 先搜索定位记录，再执行更新
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.skills.base import BaseSkill
from src.core.types import SkillContext, SkillResult

logger = logging.getLogger(__name__)


# ============================================
# region 案件更新技能
# ============================================
class UpdateSkill(BaseSkill):
    """
    案件更新技能
    
    功能:
        - 识别更新意图
        - 先搜索定位目标记录
        - 执行字段更新
    """
    
    name: str = "UpdateSkill"
    description: str = "更新案件记录的字段信息"
    
    def __init__(
        self,
        mcp_client: Any,
        settings: Any = None,
    ) -> None:
        """
        初始化更新技能
        
        参数:
            mcp_client: MCP 客户端实例
            settings: 配置信息
        """
        self._mcp = mcp_client
        self._settings = settings
    
    async def execute(self, context: SkillContext) -> SkillResult:
        """
        执行更新逻辑
        
        参数:
            context: 技能上下文
            
        返回:
            更新结果
        """
        query = context.query.strip()
        extra = context.extra or {}
        
        # 从上下文获取待更新的记录
        last_result = context.last_result or {}
        records = last_result.get("records", [])
        
        # 如果没有上下文记录，需要先搜索
        if not records:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="需要先查询要更新的记录",
                reply_text="请先查询要更新的案件，例如：查询案号XXX的案件",
            )
        
        # 如果有多条记录，需要用户明确
        if len(records) > 1:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="找到多条记录，无法确定更新目标",
                reply_text=f"找到 {len(records)} 条记录，请明确要更新哪一条。",
            )
        
        # 获取记录 ID
        record = records[0]
        record_id = record.get("record_id")
        if not record_id:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="记录缺少 record_id",
                reply_text="无法获取记录 ID，更新失败。",
            )
        
        # 解析更新字段（简化版：从查询中提取）
        # TODO: 可以使用 LLM 解析更复杂的更新意图
        fields = self._parse_update_fields(query)
        if not fields:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="无法解析更新字段",
                reply_text="请明确要更新的字段和值，例如：把开庭日改成2024-12-01",
            )
        
        # 调用 MCP 更新工具
        try:
            result = await self._mcp.call_tool(
                "feishu.v1.bitable.record.update",
                {
                    "record_id": record_id,
                    "fields": fields,
                }
            )
            
            if not result.get("success"):
                error = result.get("error", "未知错误")
                return SkillResult(
                    success=False,
                    skill_name=self.name,
                    message=f"更新失败: {error}",
                    reply_text=f"更新失败：{error}",
                )
            
            record_url = result.get("record_url", "")
            updated_fields = result.get("fields", {})
            
            # 构建回复
            field_list = "\n".join([f"  • {k}: {v}" for k, v in fields.items()])
            reply_text = (
                f"✅ 更新成功！\n\n"
                f"已更新字段：\n{field_list}\n\n"
                f"🔗 查看详情：{record_url}"
            )
            
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={
                    "record_id": record_id,
                    "updated_fields": fields,
                    "record_url": record_url,
                },
                message="更新成功",
                reply_text=reply_text,
            )
            
        except Exception as e:
            logger.error(f"UpdateSkill execution error: {e}", exc_info=True)
            return SkillResult(
                success=False,
                skill_name=self.name,
                message=str(e),
                reply_text="更新失败，请稍后重试。",
            )
    
    def _parse_update_fields(self, query: str) -> dict[str, Any]:
        """
        解析更新字段（简化版）
        
        参数:
            query: 用户查询
            
        返回:
            字段字典
        """
        fields: dict[str, Any] = {}
        
        # 简单规则：识别"把X改成Y"、"修改X为Y"等模式
        import re
        
        # 模式1: 把X改成Y
        pattern1 = re.compile(r"把(.+?)改成(.+)")
        match = pattern1.search(query)
        if match:
            field_name = match.group(1).strip()
            field_value = match.group(2).strip()
            fields[field_name] = field_value
            return fields
        
        # 模式2: 修改X为Y
        pattern2 = re.compile(r"修改(.+?)为(.+)")
        match = pattern2.search(query)
        if match:
            field_name = match.group(1).strip()
            field_value = match.group(2).strip()
            fields[field_name] = field_value
            return fields
        
        # 模式3: 更新X=Y
        pattern3 = re.compile(r"更新(.+?)[=为](.+)")
        match = pattern3.search(query)
        if match:
            field_name = match.group(1).strip()
            field_value = match.group(2).strip()
            fields[field_name] = field_value
            return fields
        
        return fields
# endregion
