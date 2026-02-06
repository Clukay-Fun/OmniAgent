"""
描述: 案件删除技能
主要功能:
    - 删除案件记录
    - 二次确认机制
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.skills.base import BaseSkill
from src.core.types import SkillContext, SkillResult

logger = logging.getLogger(__name__)


# ============================================
# region 案件删除技能
# ============================================
class DeleteSkill(BaseSkill):
    """
    案件删除技能
    
    功能:
        - 识别删除意图
        - 二次确认流程
        - 执行删除操作
    """
    
    name: str = "DeleteSkill"
    description: str = "删除案件记录（需二次确认）"
    
    def __init__(
        self,
        mcp_client: Any,
        settings: Any = None,
        skills_config: dict[str, Any] | None = None,
    ) -> None:
        """
        初始化删除技能
        
        参数:
            mcp_client: MCP 客户端实例
            settings: 配置信息
            skills_config: 技能配置
        """
        self._mcp = mcp_client
        self._settings = settings
        self._skills_config = skills_config or {}
        
        # 确认短语配置
        delete_cfg = self._skills_config.get("delete", {})
        self._confirm_phrases = set(delete_cfg.get("confirm_phrases", [
            "确认删除",
            "确认",
            "是的",
            "是",
            "删除",
            "ok",
            "yes",
        ]))
    
    async def execute(self, context: SkillContext) -> SkillResult:
        """
        执行删除逻辑
        
        参数:
            context: 技能上下文
            
        返回:
            删除结果
        """
        query = context.query.strip()
        extra = context.extra or {}
        
        # 检查是否为确认回复
        if self._is_confirmation(query):
            return await self._execute_delete(context)
        
        # 首次删除请求：需要确认
        last_result = context.last_result or {}
        records = last_result.get("records", [])
        
        # 如果没有上下文记录，需要先搜索
        if not records:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="需要先查询要删除的记录",
                reply_text="请先查询要删除的案件，例如：查询案号XXX的案件",
            )
        
        # 如果有多条记录，需要用户明确
        if len(records) > 1:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="找到多条记录，无法确定删除目标",
                reply_text=f"找到 {len(records)} 条记录，请明确要删除哪一条。",
            )
        
        # 获取记录信息
        record = records[0]
        record_id = record.get("record_id")
        if not record_id:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="记录缺少 record_id",
                reply_text="无法获取记录 ID，删除失败。",
            )
        
        # 构建确认提示
        fields = record.get("fields_text", {})
        case_no = fields.get("案号", "未知案号")
        
        reply_text = (
            f"⚠️ 确认删除\n\n"
            f"您即将删除案件：{case_no}\n\n"
            f"此操作不可撤销，请回复'确认删除'以继续。"
        )
        
        # 保存待删除的记录 ID 到上下文
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={
                "pending_delete": {
                    "record_id": record_id,
                    "case_no": case_no,
                },
                "records": records,  # 保留记录供确认后使用
            },
            message="等待用户确认删除",
            reply_text=reply_text,
        )
    
    async def _execute_delete(self, context: SkillContext) -> SkillResult:
        """
        执行实际删除操作
        
        参数:
            context: 技能上下文
            
        返回:
            删除结果
        """
        # 从上下文获取待删除的记录
        last_result = context.last_result or {}
        pending = last_result.get("pending_delete")
        
        if not pending:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="没有待删除的记录",
                reply_text="没有找到待删除的记录，请重新操作。",
            )
        
        record_id = pending.get("record_id")
        case_no = pending.get("case_no", "未知案号")
        
        # 调用 MCP 删除工具
        try:
            result = await self._mcp.call_tool(
                "feishu.v1.bitable.record.delete",
                {
                    "record_id": record_id,
                }
            )
            
            if not result.get("success"):
                error = result.get("error", "未知错误")
                return SkillResult(
                    success=False,
                    skill_name=self.name,
                    message=f"删除失败: {error}",
                    reply_text=f"删除失败：{error}",
                )
            
            reply_text = f"✅ 已成功删除案件：{case_no}"
            
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={
                    "record_id": record_id,
                    "case_no": case_no,
                },
                message="删除成功",
                reply_text=reply_text,
            )
            
        except Exception as e:
            logger.error(f"DeleteSkill execution error: {e}", exc_info=True)
            return SkillResult(
                success=False,
                skill_name=self.name,
                message=str(e),
                reply_text="删除失败，请稍后重试。",
            )
    
    def _is_confirmation(self, query: str) -> bool:
        """
        检查是否为确认短语
        
        参数:
            query: 用户输入
            
        返回:
            是否为确认
        """
        normalized = query.strip().lower()
        return normalized in self._confirm_phrases
# endregion
