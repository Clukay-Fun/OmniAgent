"""
描述: 案件删除技能
主要功能:
    - 删除案件记录
    - 二次确认机制
"""

from __future__ import annotations

import logging
import os
import re
import hashlib
import json
from typing import Any

from src.core.errors import get_user_message_by_code
from src.core.skills.bitable_adapter import BitableAdapter
from src.core.skills.action_execution_service import ActionExecutionService
from src.core.skills.base import BaseSkill
from src.core.skills.data_writer import DataWriter
from src.core.skills.multi_table_linker import MultiTableLinker
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
        llm_client: Any = None,
        settings: Any = None,
        skills_config: dict[str, Any] | None = None,
        data_writer: DataWriter | None = None,
    ) -> None:
        """
        初始化删除技能
        
        参数:
            mcp_client: MCP 客户端实例
            llm_client: LLM 客户端实例
            settings: 配置信息
            skills_config: 技能配置
        """
        self._mcp = mcp_client
        self._llm = llm_client
        self._settings = settings
        self._skills_config = skills_config or {}
        if data_writer is None:
            raise ValueError("DeleteSkill requires an injected data_writer")
        self._data_writer: DataWriter = data_writer
        self._table_adapter = BitableAdapter(mcp_client, skills_config=self._skills_config)
        self._linker = MultiTableLinker(mcp_client, skills_config=self._skills_config, data_writer=data_writer)
        self._action_service = ActionExecutionService(data_writer=self._data_writer, linker=self._linker)
        
        from src.core.skills.entity_extractor import EntityExtractor
        self._extractor = EntityExtractor(llm_client)
        
        # 确认短语配置
        delete_cfg = self._skills_config.get("delete", {})
        full_confirm_phrases = delete_cfg.get("full_confirm_phrases", ["确认删除"])
        self._full_confirm_phrases = {
            str(x).strip().lower()
            for x in full_confirm_phrases
            if str(x).strip()
        }
        self._confirm_ttl_seconds = 60

    def _delete_enabled(self) -> bool:
        value = str(os.getenv("CRUD_DELETE_ENABLED", "false") or "false").strip().lower()
        return value in {"1", "true", "yes", "on"}
    
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
        app_token = self._resolve_app_token(
            table_ctx=table_ctx,
            pending_payload={},
            extra=extra,
            planner_plan=planner_plan,
            last_result=last_result,
        )
        denied_text = self._action_service.validate_write_allowed(table_ctx.table_name)
        if denied_text:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="写入受限",
                reply_text=denied_text,
            )

        if not self._delete_enabled():
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"error_code": "delete_disabled"},
                message="删除能力已关闭",
                reply_text=get_user_message_by_code("delete_disabled"),
            )

        pending_action_raw = extra.get("pending_action")
        pending_action: dict[str, Any] = pending_action_raw if isinstance(pending_action_raw, dict) else {}
        callback_intent = str(extra.get("callback_intent") or "").strip().lower()
        pending_payload_raw = pending_action.get("payload")
        pending_payload: dict[str, Any] = pending_payload_raw if isinstance(pending_payload_raw, dict) else {}
        if str(pending_action.get("action") or "") == "delete_record" and pending_payload:
            pending_table_name = str(pending_payload.get("table_name") or table_ctx.table_name or "").strip() or None
            pending_denied_text = self._action_service.validate_write_allowed(pending_table_name)
            if pending_denied_text:
                return SkillResult(
                    success=False,
                    skill_name=self.name,
                    data={"clear_pending_action": True, "clear_pending_delete": True},
                    message="写入受限",
                    reply_text=pending_denied_text,
                )
            if callback_intent == "cancel":
                return SkillResult(
                    success=True,
                    skill_name=self.name,
                    data={"clear_pending_action": True, "clear_pending_delete": True},
                    message="已取消删除",
                    reply_text="好的，已取消删除操作。",
                )
            if callback_intent == "confirm":
                merged_last = dict(last_result)
                merged_last["pending_delete"] = pending_payload
                callback_ctx = SkillContext(
                    query="确认删除",
                    user_id=context.user_id,
                    last_result=merged_last,
                    last_skill=context.last_skill,
                    extra=context.extra,
                )
                return await self._execute_delete(callback_ctx)
        
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
            if app_token and not planner_pending.get("app_token"):
                planner_pending["app_token"] = app_token
            idempotency_key = self._build_delete_idempotency_key(
                record_id=str(planner_pending.get("record_id") or ""),
                table_id=str(planner_pending.get("table_id") or ""),
            )
            pending_data = self._action_service.build_pending_delete_action_data(
                record_id=str(planner_pending.get("record_id") or ""),
                case_no=str(planner_pending.get("case_no") or "未知案号"),
                table_id=planner_pending.get("table_id"),
                table_name=planner_pending.get("table_name"),
                idempotency_key=idempotency_key,
                app_token=str(planner_pending.get("app_token") or "").strip() or app_token,
                ttl_seconds=self._confirm_ttl_seconds,
            )
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={
                    **pending_data,
                    "records": records,
                },
                message="等待用户确认删除",
                reply_text=(
                    f"⚠️ 请确认删除\n\n"
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
                data={"error_code": "delete_target_required"},
                message="需要先查询要删除的记录",
                reply_text=get_user_message_by_code("delete_target_required"),
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
                data={"error_code": "delete_record_id_missing"},
                message="记录缺少 record_id",
                reply_text=get_user_message_by_code("delete_record_id_missing"),
            )
        
        # 构建确认提示
        fields = record.get("fields_text", {})
        case_no = fields.get("案号", "未知案号")
        
        reply_text = (
            f"⚠️ 请确认删除\n\n"
            f"您即将删除案件：{case_no}\n\n"
            f"此操作不可撤销，请回复'确认删除'以继续。"
        )
        idempotency_key = self._build_delete_idempotency_key(record_id=str(record_id), table_id=str(table_ctx.table_id or ""))
        pending_data = self._action_service.build_pending_delete_action_data(
            record_id=str(record_id),
            case_no=str(case_no),
            table_id=table_ctx.table_id,
            table_name=table_ctx.table_name,
            idempotency_key=idempotency_key,
            app_token=app_token,
            ttl_seconds=self._confirm_ttl_seconds,
        )
        
        # 保存待删除的记录 ID 到上下文
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={
                **pending_data,
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
            pending_action = context.extra.get("pending_action") if isinstance(context.extra, dict) else None
            if isinstance(pending_action, dict) and str(pending_action.get("action") or "") == "delete_record":
                payload = pending_action.get("payload")
                if isinstance(payload, dict):
                    pending = payload
        
        if not pending:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"error_code": "delete_pending_not_found"},
                message="没有待删除的记录",
                reply_text=get_user_message_by_code("delete_pending_not_found"),
            )
        
        record_id = pending.get("record_id")
        case_no = pending.get("case_no", "未知案号")
        table_id = pending.get("table_id")
        app_token = str(pending.get("app_token") or "").strip() or None
        idempotency_key = str(pending.get("idempotency_key") or "").strip() or None
        if not table_id or not app_token:
            table_ctx = await self._table_adapter.resolve_table_context(
                context.query,
                context.extra or {},
                last_result,
            )
            if not table_id:
                table_id = table_ctx.table_id
            if not app_token:
                app_token = str(getattr(table_ctx, "app_token", "") or "").strip() or None
        app_token = app_token or self._resolve_app_token(
            table_ctx=type("Ctx", (), {"app_token": app_token})(),
            pending_payload=pending if isinstance(pending, dict) else {},
            extra=context.extra if isinstance(context.extra, dict) else {},
            planner_plan=(context.extra or {}).get("planner_plan") if isinstance(context.extra, dict) else None,
            last_result=last_result,
        )
        if not idempotency_key:
            idempotency_key = self._build_delete_idempotency_key(
                record_id=str(record_id or ""),
                table_id=str(table_id or ""),
            )
        
        try:
            outcome = await self._action_service.execute_delete(
                table_id=table_id,
                table_name=str(pending.get("table_name") or "").strip() or None,
                record_id=str(record_id),
                case_no=str(case_no),
                idempotency_key=idempotency_key,
                app_token=app_token,
            )
            if not outcome.success:
                failure_data = dict(outcome.data) if isinstance(outcome.data, dict) else {}
                failure_data.setdefault("error_code", "delete_record_failed")
                return SkillResult(
                    success=False,
                    skill_name=self.name,
                    data=failure_data,
                    message=outcome.message,
                    reply_text=outcome.reply_text,
                )
            return SkillResult(
                success=True,
                skill_name=self.name,
                data=outcome.data,
                message=outcome.message,
                reply_text=outcome.reply_text,
            )
            
        except Exception as e:
            logger.error(f"DeleteSkill execution error: {e}", exc_info=True)
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"error_code": "delete_record_failed"},
                message=str(e),
                reply_text=get_user_message_by_code("delete_record_failed", detail=str(e)),
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
        return normalized in self._full_confirm_phrases

    def _build_delete_idempotency_key(self, *, record_id: str, table_id: str | None = None) -> str:
        payload = {
            "record_id": str(record_id or "").strip(),
            "table_id": str(table_id or "").strip(),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]
        return f"delete-{digest}"

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
        app_token = str(params.get("app_token") or "").strip() or None
        if not record_id:
            return None
        return {
            "record_id": record_id,
            "case_no": case_no,
            "table_id": table_id,
            "table_name": table_name,
            "app_token": app_token,
        }

    def _resolve_app_token(
        self,
        *,
        table_ctx: Any,
        pending_payload: dict[str, Any],
        extra: dict[str, Any],
        planner_plan: dict[str, Any] | None,
        last_result: dict[str, Any],
    ) -> str | None:
        candidates: list[Any] = [
            pending_payload.get("app_token") if isinstance(pending_payload, dict) else None,
            getattr(table_ctx, "app_token", None),
            extra.get("app_token") if isinstance(extra, dict) else None,
        ]
        active_record = extra.get("active_record") if isinstance(extra, dict) else None
        if isinstance(active_record, dict):
            candidates.append(active_record.get("app_token"))
        if isinstance(planner_plan, dict):
            params = planner_plan.get("params")
            if isinstance(params, dict):
                candidates.append(params.get("app_token"))
        if isinstance(last_result, dict):
            candidates.append(last_result.get("app_token"))

        for key in ("BITABLE_APP_TOKEN", "FEISHU_BITABLE_APP_TOKEN", "APP_TOKEN"):
            candidates.append(os.getenv(key))

        for raw in candidates:
            token = str(raw or "").strip()
            if token:
                return token
        return None

    async def _search_records_by_query(self, query: str, table_id: str | None = None) -> list[dict[str, Any]]:
        """根据查询文本尝试搜索待删除记录。"""
        exact_field = await self._extractor.extract_exact_match_field(query)

        tool_name = None
        params: dict[str, Any] = {}
        if exact_field:
            tool_name = "data.bitable.search_exact"
            params.update(exact_field)

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
