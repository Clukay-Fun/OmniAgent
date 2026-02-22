"""
描述: 案件删除技能
主要功能:
    - 删除案件记录
    - 二次确认机制
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.core.skills.bitable_adapter import BitableAdapter
from src.core.skills.base import BaseSkill
from src.core.skills.data_writer import DataWriter
from src.core.skills.multi_table_linker import MultiTableLinker
from src.core.skills.response_pool import pool
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
        data_writer: DataWriter | None = None,
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
        if data_writer is None:
            raise ValueError("DeleteSkill requires an injected data_writer")
        self._table_adapter = BitableAdapter(mcp_client, skills_config=self._skills_config)
        self._linker = MultiTableLinker(mcp_client, skills_config=self._skills_config, data_writer=data_writer)
        
        # 确认短语配置
        delete_cfg = self._skills_config.get("delete", {})
        self._confirm_phrases = {
            str(x).strip().lower()
            for x in delete_cfg.get("confirm_phrases", [
            "确认删除",
            "确认",
            "是的",
            "是",
            "删除",
            "ok",
            "yes",
        ])
            if str(x).strip()
        }
    
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
        planner_plan = extra.get("planner_plan") if isinstance(extra.get("planner_plan"), dict) else None
        last_result = context.last_result or {}
        table_ctx = await self._table_adapter.resolve_table_context(query, extra, last_result)
        
        # 检查是否为确认回复
        if self._is_confirmation(query):
            return await self._execute_delete(context)
        
        # 首次删除请求：需要确认
        records = last_result.get("records", [])
        if not records:
            active_record = extra.get("active_record")
            if isinstance(active_record, dict) and active_record.get("record_id"):
                records = [active_record]

        # Planner 直接给 record_id 时，直接进入确认
        planner_pending = self._extract_pending_from_planner(planner_plan)
        if planner_pending:
            if table_ctx.table_id and not planner_pending.get("table_id"):
                planner_pending["table_id"] = table_ctx.table_id
            if table_ctx.table_name and not planner_pending.get("table_name"):
                planner_pending["table_name"] = table_ctx.table_name
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={
                    "pending_delete": planner_pending,
                    "records": records,
                },
                message="等待用户确认删除",
                reply_text=(
                    f"⚠️ 确认删除\n\n"
                    f"您即将删除案件：{planner_pending.get('case_no', '未知案号')}\n\n"
                    f"此操作不可撤销，请回复'确认删除'以继续。"
                ),
            )

        # 如果没有上下文记录，尝试按案号/项目ID快速定位
        if not records:
            records = await self._search_records_by_query(query, table_ctx.table_id)

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
                success=True,
                skill_name=self.name,
                data={"records": records[:10]},
                message="找到多条记录，无法确定删除目标",
                reply_text=self._build_multi_record_reply(records),
            )
        
        # 获取记录信息
        record = records[0]
        record_id = record.get("record_id")
        record_table_id = self._table_adapter.extract_table_id_from_record(record)
        if record_table_id and not table_ctx.table_id:
            table_ctx.table_id = record_table_id
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
                    "table_id": table_ctx.table_id,
                    "table_name": table_ctx.table_name,
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
        table_id = pending.get("table_id")
        if not table_id:
            table_ctx = await self._table_adapter.resolve_table_context(
                context.query,
                context.extra or {},
                last_result,
            )
            table_id = table_ctx.table_id
        
        # 调用 MCP 删除工具
        try:
            params: dict[str, Any] = {
                "record_id": record_id,
            }
            if table_id:
                params["table_id"] = table_id

            result = await self._mcp.call_tool(
                "feishu.v1.bitable.record.delete",
                params,
            )
            
            if not result.get("success"):
                error = result.get("error", "未知错误")
                return SkillResult(
                    success=False,
                    skill_name=self.name,
                    message=f"删除失败: {error}",
                    reply_text=f"删除失败：{error}",
                )
            
            link_sync = await self._linker.sync_after_delete(
                parent_table_id=table_id,
                parent_table_name=str(pending.get("table_name") or "").strip() or None,
                parent_fields={"案号": case_no},
            )
            link_summary = self._linker.summarize(link_sync)
            reply_text = f"{pool.pick('delete_success', '✅ 已删除')}\n案件：{case_no}"
            if link_summary:
                reply_text += f"\n\n{link_summary}"
            
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={
                    "record_id": record_id,
                    "case_no": case_no,
                    "table_id": table_id,
                    "link_sync": link_sync,
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
                reply_text=pool.pick("error", "删除失败，请稍后重试。"),
            )
    
    def _is_confirmation(self, query: str) -> bool:
        """
        检查是否为确认短语
        
        参数:
            query: 用户输入
            
        返回:
            是否为确认
        """
        normalized = query.strip().lower().strip("，。！？!?,. ")
        return normalized in self._confirm_phrases

    def _extract_pending_from_planner(self, planner_plan: dict[str, Any] | None) -> dict[str, Any] | None:
        """从 planner 输出提取待删除目标。"""
        if not isinstance(planner_plan, dict):
            return None
        if planner_plan.get("tool") != "record.delete":
            return None
        params = planner_plan.get("params")
        if not isinstance(params, dict):
            return None

        record_id = str(params.get("record_id") or "").strip()
        case_no = str(params.get("case_no") or params.get("value") or "未知案号").strip()
        table_id = str(params.get("table_id") or "").strip() or None
        table_name = str(params.get("table_name") or "").strip() or None
        if not record_id:
            return None
        return {
            "record_id": record_id,
            "case_no": case_no,
            "table_id": table_id,
            "table_name": table_name,
        }

    async def _search_records_by_query(self, query: str, table_id: str | None = None) -> list[dict[str, Any]]:
        """根据查询文本尝试搜索待删除记录。"""
        exact_case = re.search(r"(?:案号|案件号)[是为:：\s]*([A-Za-z0-9\-_/（）()_\u4e00-\u9fa5]+)", query)
        exact_project = re.search(r"(?:项目ID|项目编号|项目号)[是为:：\s]*([A-Za-z0-9\-_/（）()_\u4e00-\u9fa5]+)", query)

        tool_name = None
        params: dict[str, Any] = {}
        if exact_case:
            tool_name = "feishu.v1.bitable.search_exact"
            params = {"field": "案号", "value": exact_case.group(1).strip()}
        elif exact_project:
            tool_name = "feishu.v1.bitable.search_exact"
            params = {"field": "项目ID", "value": exact_project.group(1).strip()}

        if table_id:
            params["table_id"] = table_id

        if not tool_name:
            return []

        try:
            result = await self._mcp.call_tool(tool_name, params)
            records = result.get("records", [])
            if isinstance(records, list):
                return records
            return []
        except Exception as exc:
            logger.warning("DeleteSkill pre-search failed: %s", exc)
            return []

    def _build_multi_record_reply(self, records: list[dict[str, Any]]) -> str:
        lines = [f"找到 {len(records)} 条记录，请指定要删除哪一条："]
        for index, record in enumerate(records[:5], start=1):
            fields = record.get("fields_text") or record.get("fields") or {}
            case_no = str(fields.get("案号") or fields.get("项目ID") or "未知")
            cause = str(fields.get("案由") or fields.get("案件分类") or "")
            if cause:
                lines.append(f"{index}. {case_no} - {cause}")
            else:
                lines.append(f"{index}. {case_no}")
        lines.append("可回复“删除第一个”或“第一个删除”继续。")
        return "\n".join(lines)
# endregion
