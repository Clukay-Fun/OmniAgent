"""
描述: 案件更新技能
主要功能:
    - 更新案件记录字段
    - 先搜索定位记录，再执行更新
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import date
from typing import Any

from src.core.errors import get_user_message_by_code
from src.core.skills.base import BaseSkill
from src.core.skills.action_execution_service import ActionExecutionService
from src.core.skills.data_writer import DataWriter
from src.core.skills.multi_table_linker import MultiTableLinker
from src.core.skills.table_adapter import TableAdapter
from src.core.types import SkillContext, SkillResult
from src.utils.time_parser import parse_time_range

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
        skills_config: dict[str, Any] | None = None,
        *,
        data_writer: DataWriter,
    ) -> None:
        """
        初始化更新技能
        
        参数:
            mcp_client: MCP 客户端实例
            settings: 配置信息
        """
        self._mcp = mcp_client
        self._settings = settings
        self._skills_config = skills_config or {}
        if data_writer is None:
            raise ValueError("UpdateSkill requires an injected data_writer")
        self._data_writer = data_writer
        self._table_adapter = TableAdapter(mcp_client, skills_config=skills_config)
        self._linker = MultiTableLinker(
            mcp_client,
            skills_config=skills_config,
            data_writer=self._data_writer,
        )
        self._action_service = ActionExecutionService(data_writer=self._data_writer, linker=self._linker)

        update_cfg = self._skills_config.get("update", {}) if isinstance(self._skills_config, dict) else {}
        if not isinstance(update_cfg, dict):
            update_cfg = {}
        default_options = {
            "案件状态": ["进行中", "已结案", "执行终本", "暂停"],
        }
        raw_options = update_cfg.get("field_options")
        options_cfg: dict[str, Any] = dict(raw_options) if isinstance(raw_options, dict) else {}
        merged_options: dict[str, list[str]] = {}
        all_options: dict[str, Any] = dict(default_options)
        for key, values in options_cfg.items():
            all_options[str(key)] = values
        for key, values in all_options.items():
            if isinstance(values, list):
                merged_options[str(key)] = [str(item).strip() for item in values if str(item).strip()]
        self._field_options = merged_options

        self._confirm_phrases = {"确认", "是", "是的", "ok", "yes"}
        self._cancel_phrases = {"取消", "算了", "不了", "不用了"}
        confirm_ttl_seconds = update_cfg.get("confirm_ttl_seconds", 60)
        try:
            self._confirm_ttl_seconds = max(1, int(confirm_ttl_seconds))
        except Exception:
            self._confirm_ttl_seconds = 60
        self._field_aliases = {
            "状态": "案件状态",
            "案件状态": "案件状态",
            "进展": "进展",
            "案由": "案由",
            "开庭": "开庭日",
            "开庭日": "开庭日",
            "法院": "审理法院",
            "审理法院": "审理法院",
            "委托人": "委托人",
            "主办": "主办律师",
            "主办律师": "主办律师",
            "协办": "协办律师",
            "协办律师": "协办律师",
            "备注": "备注",
            "金额": "金额",
            "费用": "金额",
        }
        self._update_value_prefixes = (
            "改成",
            "改为",
            "变成",
            "变为",
            "更新为",
            "修改为",
            "设为",
            "设成",
            "调整为",
        )
        self._date_field_names = {
            "开庭日",
            "截止日",
            "上诉截止日",
            "举证截止日",
            "签约日期",
            "到期日期",
            "付款截止",
        }
    
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
        raw_idempotency_key = extra.get("idempotency_key")
        idempotency_key = str(raw_idempotency_key).strip() if raw_idempotency_key else None
        planner_plan = extra.get("planner_plan") if isinstance(extra.get("planner_plan"), dict) else None
        last_result = context.last_result or {}
        table_ctx = await self._table_adapter.resolve_table_context(query, extra, last_result)
        app_token = self._resolve_app_token(
            table_ctx=table_ctx,
            pending_payload=None,
            extra=extra,
            planner_plan=planner_plan,
        )
        denied_text = self._action_service.validate_write_allowed(table_ctx.table_name)
        if denied_text:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="写入受限",
                reply_text=denied_text,
            )

        pending_action, pending_payload = self._extract_pending_update(extra)
        if pending_action == "repair_child_update" and pending_payload:
            return await self._execute_pending_repair(
                query=query,
                pending_action=pending_action,
                pending_payload=pending_payload,
                table_ctx=table_ctx,
            )
        if pending_action == "update_collect_fields" and pending_payload:
            return await self._execute_pending_collect_fields(
                query=query,
                pending_payload=pending_payload,
                table_ctx=table_ctx,
            )
        if pending_action in {"update_record", "close_record"} and pending_payload:
            return await self._execute_pending_update(
                query=query,
                action_name=pending_action,
                pending_payload=pending_payload,
                table_ctx=table_ctx,
            )

        planner_params = planner_plan.get("params") if isinstance(planner_plan, dict) else None
        planner_record_id = None
        if isinstance(planner_params, dict):
            rid = planner_params.get("record_id")
            planner_record_id = str(rid).strip() if rid else None
        has_identifier_hint = self._has_record_identifier_hint(query)

        records = []
        if not planner_record_id:
            exact_records = await self._search_records_by_query(query, table_ctx.table_id)
            if not exact_records and has_identifier_hint and table_ctx.table_id:
                exact_records = await self._search_records_by_query(query, None)
            if exact_records:
                records = exact_records

        if not records and not planner_record_id and not has_identifier_hint:
            active_record = extra.get("active_record")
            if isinstance(active_record, dict) and active_record.get("record_id"):
                records = [active_record]

        if not records and not planner_record_id and not has_identifier_hint:
            last_records = last_result.get("records", [])
            if isinstance(last_records, list):
                records = last_records

        if not records and not planner_record_id:
            if has_identifier_hint:
                return SkillResult(
                    success=False,
                    skill_name=self.name,
                    data={"error_code": "update_record_not_found_hint"},
                    message="未找到匹配记录",
                    reply_text=get_user_message_by_code("update_record_not_found_hint"),
                )
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"error_code": "update_target_required"},
                message="需要先定位要更新的记录",
                reply_text=get_user_message_by_code("update_target_required"),
            )

        if len(records) > 1 and not planner_record_id:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"records": records[:5]},
                message="找到多条记录，无法确定更新目标",
                reply_text=self._build_multi_record_reply(records),
            )

        if planner_record_id:
            record_id = planner_record_id
            record = records[0] if records else {}
            active_record = extra.get("active_record")
            if isinstance(active_record, dict):
                active_record_id = str(active_record.get("record_id") or "").strip()
                if active_record_id and active_record_id == planner_record_id:
                    record = active_record
        else:
            record = records[0]
            record_id = record.get("record_id")

        record_table_id = self._table_adapter.extract_table_id_from_record(record)
        if record_table_id:
            table_ctx.table_id = record_table_id
        record_table_name = str(record.get("table_name") or "").strip()
        if record_table_name:
            table_ctx.table_name = record_table_name
        if not record_id:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"error_code": "update_record_id_missing"},
                message="记录缺少 record_id",
                reply_text=get_user_message_by_code("update_record_id_missing"),
            )

        # 解析更新字段（简化版：从查询中提取）
        pending_action_name, close_semantic = self._resolve_pending_action_name(
            query=query,
            planner_plan=planner_plan,
            table_name=table_ctx.table_name,
        )
        fields = self._collect_update_fields(query=query, planner_plan=planner_plan)

        if pending_action_name == "close_record":
            close_profile_data = self._action_service.build_pending_close_action_data(
                record_id=str(record_id),
                fields=fields,
                source_fields=self._extract_source_fields(record),
                diff_items=[],
                table_id=table_ctx.table_id,
                table_name=table_ctx.table_name,
                idempotency_key=idempotency_key,
                app_token=app_token,
                created_at=time.time(),
                ttl_seconds=self._confirm_ttl_seconds,
                append_date=date.today().isoformat(),
                close_semantic=close_semantic,
                intent_text=query,
            )
            close_payload = close_profile_data.get("pending_action", {}).get("payload", {})
            if isinstance(close_payload, dict):
                status_field = str(close_payload.get("close_status_field") or "案件状态").strip() or "案件状态"
                target_status = str(close_payload.get("close_status_value") or "已结案").strip() or "已结案"
                if status_field not in fields:
                    fields[status_field] = target_status

        if not fields:
            source_fields = self._extract_source_fields(record)
            return self._build_update_collect_fields_result(
                record_id=str(record_id),
                source_fields=source_fields,
                table_id=table_ctx.table_id,
                table_name=table_ctx.table_name,
                table_type=self._resolve_table_type(table_ctx.table_name),
                app_token=app_token,
            )

        validation_error = self._validate_fields(fields)
        if validation_error:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"record_id": record_id, "invalid_fields": fields},
                message="字段值校验失败",
                reply_text=validation_error,
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
                    "record_id": record_id,
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

        source_fields = self._extract_source_fields(record)
        effective_preview_fields, diff_items, append_date = self._action_service.build_update_preview(
            table_name=table_ctx.table_name,
            fields=fields,
            source_fields=source_fields,
            append_date=None,
        )
        if not diff_items:
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={
                    "clear_pending_action": True,
                    "record_id": record_id,
                    "table_id": table_ctx.table_id,
                    "table_name": table_ctx.table_name,
                },
                message="无字段变更",
                reply_text="该字段已是目标值，无需更新。",
            )

        if not idempotency_key:
            idempotency_key = self._build_update_idempotency_key(record_id=record_id, fields=fields)

        return self._build_pending_update_result(
            action_name=pending_action_name,
            record_id=record_id,
            fields=fields,
            preview_fields=effective_preview_fields,
            source_fields=source_fields,
            diff_items=diff_items,
            table_id=table_ctx.table_id,
            table_name=table_ctx.table_name,
            idempotency_key=idempotency_key,
            app_token=app_token,
            created_at=time.time(),
            ttl_seconds=self._confirm_ttl_seconds,
            append_date=append_date,
            close_semantic=close_semantic,
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
        
        # 模式1: 把X改成Y / 把X设成Y / 把X设置为Y
        pattern1 = re.compile(r"把(.+?)(?:改成|改为|设成|设置为|设为)(.+)")
        match = pattern1.search(query)
        if match:
            field_name = self._normalize_field_segment(match.group(1).strip())
            field_value = self._normalize_field_value(field_name, match.group(2).strip())
            fields[field_name] = field_value
            return fields

        # 模式1.1: X改成Y / X改为Y / X调整为Y
        pattern1_1 = re.compile(
            r"(案号|案件状态|状态|开庭日|开庭|审理法院|法院|主办律师|主办|协办律师|协办|进展|备注|案由|金额|费用)(?:内容)?\s*(?:改成|改为|变成|变为|更新为|修改为|设为|设成|调整为|为|:|：)(.+)"
        )
        match = pattern1_1.search(query)
        if match:
            field_name = self._normalize_field_segment(match.group(1).strip())
            field_value = self._normalize_field_value(field_name, match.group(2).strip())
            fields[field_name] = field_value
            return fields
        
        # 模式2: 修改X为Y / 更新X为Y
        pattern2 = re.compile(r"(?:修改|更新)(.+?)为(.+)")
        match = pattern2.search(query)
        if match:
            field_name = self._normalize_field_segment(match.group(1).strip())
            field_value = self._normalize_field_value(field_name, match.group(2).strip())
            fields[field_name] = field_value
            return fields
        
        # 模式3: 更新X=Y
        pattern3 = re.compile(r"更新(.+?)[=为](.+)")
        match = pattern3.search(query)
        if match:
            field_name = self._normalize_field_segment(match.group(1).strip())
            field_value = self._normalize_field_value(field_name, match.group(2).strip())
            fields[field_name] = field_value
            return fields
        
        return fields

    def _extract_pending_update(self, extra: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        pending = extra.get("pending_action")
        if not isinstance(pending, dict):
            return None, {}
        action = str(pending.get("action") or "").strip()
        if action not in {"repair_child_update", "update_record", "close_record", "update_collect_fields"}:
            return None, {}
        payload = pending.get("payload")
        if not isinstance(payload, dict):
            return None, {}
        return action, payload

    async def _execute_pending_update(
        self,
        *,
        query: str,
        action_name: str,
        pending_payload: dict[str, Any],
        table_ctx: Any,
    ) -> SkillResult:
        if self._is_cancel(query):
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"clear_pending_action": True},
                message="已取消更新",
                reply_text="好的，已取消更新操作。",
            )

        if self._is_pending_expired(pending_payload):
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"clear_pending_action": True, "error_code": "update_pending_expired"},
                message="更新确认已超时",
                reply_text=get_user_message_by_code("update_pending_expired"),
            )

        app_token = self._resolve_app_token(table_ctx=table_ctx, pending_payload=pending_payload)

        if not self._is_confirm(query):
            created_at_raw = pending_payload.get("created_at")
            try:
                created_at = float(str(created_at_raw))
            except Exception:
                created_at = time.time()
            fields_raw = pending_payload.get("fields")
            fields: dict[str, Any] = dict(fields_raw) if isinstance(fields_raw, dict) else {}
            source_fields_raw = pending_payload.get("source_fields")
            source_fields: dict[str, Any] = dict(source_fields_raw) if isinstance(source_fields_raw, dict) else {}
            diff_raw = pending_payload.get("diff")
            diff_items: list[dict[str, str]] = []
            if isinstance(diff_raw, list):
                for item in diff_raw:
                    if not isinstance(item, dict):
                        continue
                    normalized_item = {
                        "field": str(item.get("field") or ""),
                        "old": str(item.get("old") or ""),
                        "new": str(item.get("new") or ""),
                    }
                    mode = str(item.get("mode") or "").strip()
                    delta = str(item.get("delta") or "").strip()
                    if mode:
                        normalized_item["mode"] = mode
                    if delta:
                        normalized_item["delta"] = delta
                    diff_items.append(normalized_item)
            preview_fields_raw = pending_payload.get("preview_fields")
            preview_fields = dict(preview_fields_raw) if isinstance(preview_fields_raw, dict) else dict(fields)
            append_date = str(pending_payload.get("append_date") or "").strip() or date.today().isoformat()
            close_semantic = str(pending_payload.get("close_semantic") or "").strip() or self._action_service.resolve_close_semantic(
                query,
                str(pending_payload.get("table_name") or table_ctx.table_name or ""),
                default="default",
            )
            return self._build_pending_update_result(
                action_name=action_name,
                record_id=str(pending_payload.get("record_id") or "").strip(),
                fields=fields,
                preview_fields=preview_fields,
                source_fields=source_fields,
                diff_items=diff_items,
                table_id=str(pending_payload.get("table_id") or table_ctx.table_id or "").strip() or None,
                table_name=str(pending_payload.get("table_name") or table_ctx.table_name or "").strip() or None,
                idempotency_key=str(pending_payload.get("idempotency_key") or "").strip() or None,
                app_token=app_token,
                created_at=created_at,
                ttl_seconds=self._resolve_pending_ttl(pending_payload.get("pending_ttl_seconds")),
                append_date=append_date,
                close_semantic=close_semantic,
            )

        record_id = str(pending_payload.get("record_id") or "").strip()
        if not record_id:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"clear_pending_action": True, "error_code": "update_pending_record_missing"},
                message="更新确认缺少 record_id",
                reply_text=get_user_message_by_code("update_pending_record_missing"),
            )

        table_id = str(pending_payload.get("table_id") or table_ctx.table_id or "").strip() or None
        table_name = str(pending_payload.get("table_name") or table_ctx.table_name or "").strip() or None
        app_token = self._resolve_app_token(table_ctx=table_ctx, pending_payload=pending_payload)
        denied_text = self._action_service.validate_write_allowed(table_name)
        if denied_text:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"clear_pending_action": True},
                message="写入受限",
                reply_text=denied_text,
            )
        fields_raw = pending_payload.get("fields")
        fields: dict[str, Any] = dict(fields_raw) if isinstance(fields_raw, dict) else {}
        source_fields_raw = pending_payload.get("source_fields")
        source_fields: dict[str, Any] = dict(source_fields_raw) if isinstance(source_fields_raw, dict) else {}
        idempotency_key = str(pending_payload.get("idempotency_key") or "").strip() or None
        if not idempotency_key:
            idempotency_key = self._build_update_idempotency_key(record_id=record_id, fields=fields)
        close_semantic = str(pending_payload.get("close_semantic") or "").strip() or self._action_service.resolve_close_semantic(
            query,
            table_name,
            default="default",
        )
        if action_name == "close_record":
            close_profile_data = self._action_service.build_pending_close_action_data(
                record_id=record_id,
                fields=fields,
                source_fields=source_fields,
                diff_items=[],
                table_id=table_id,
                table_name=table_name,
                idempotency_key=idempotency_key,
                app_token=app_token,
                created_at=time.time(),
                ttl_seconds=self._confirm_ttl_seconds,
                append_date=str(pending_payload.get("append_date") or date.today().isoformat()),
                close_semantic=close_semantic,
                intent_text=query,
            )
            close_payload = close_profile_data.get("pending_action", {}).get("payload", {})
            status_field = str((close_payload or {}).get("close_status_field") or pending_payload.get("close_status_field") or "案件状态").strip() or "案件状态"
            close_status_raw = pending_payload.get("close_status_value")
            if not close_status_raw and isinstance(close_payload, dict):
                close_status_raw = close_payload.get("close_status_value")
            close_status = str(close_status_raw).strip() if close_status_raw is not None else ""
            if not close_status:
                close_status = "已结案"
            if status_field not in fields:
                fields[status_field] = close_status
        append_date = str(pending_payload.get("append_date") or "").strip() or None
        effective_preview_fields, diff_items, normalized_append_date = self._action_service.build_update_preview(
            table_name=table_name,
            fields=fields,
            source_fields=source_fields,
            append_date=append_date,
        )
        if not diff_items:
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"clear_pending_action": True, "record_id": record_id, "table_id": table_id, "table_name": table_name},
                message="无字段变更",
                reply_text="该字段已是目标值，无需更新。",
            )

        try:
            outcome = await self._action_service.execute_update(
                action_name=action_name,
                table_id=table_id,
                table_name=table_name,
                record_id=record_id,
                fields=fields,
                source_fields=source_fields,
                idempotency_key=idempotency_key,
                append_date=normalized_append_date,
                close_semantic=close_semantic,
                app_token=app_token,
            )
            if not outcome.success:
                if self._is_record_not_found_error(outcome.message, outcome.reply_text):
                    return SkillResult(
                        success=False,
                        skill_name=self.name,
                        data={
                            "clear_pending_action": True,
                            "record_id": record_id,
                            "table_id": table_id,
                            "table_name": table_name,
                            "error_code": "update_record_deleted",
                        },
                        message="目标记录不存在",
                        reply_text=get_user_message_by_code("update_record_deleted"),
                    )
                return self._build_pending_update_result(
                    action_name=action_name,
                    record_id=record_id,
                    fields=fields,
                    preview_fields=effective_preview_fields,
                    source_fields=source_fields,
                    diff_items=diff_items,
                    table_id=table_id,
                    table_name=table_name,
                    idempotency_key=idempotency_key,
                    app_token=app_token,
                    created_at=time.time(),
                    ttl_seconds=self._confirm_ttl_seconds,
                    reply_text=outcome.reply_text,
                    append_date=normalized_append_date,
                    close_semantic=close_semantic,
                )

            return SkillResult(
                success=True,
                skill_name=self.name,
                data=outcome.data,
                message=outcome.message,
                reply_text=outcome.reply_text,
            )
        except Exception as exc:
            logger.error("UpdateSkill pending execution error: %s", exc, exc_info=True)
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"error_code": "update_record_failed"},
                message=str(exc),
                reply_text=get_user_message_by_code("update_record_failed"),
            )

    def _is_record_not_found_error(self, message: str, reply_text: str) -> bool:
        text = f"{message}\n{reply_text}".lower()
        tokens = (
            "recordidnotfound",
            "record not found",
            "notfound",
            "未找到",
            "不存在",
        )
        return any(token in text for token in tokens)

    async def _execute_pending_collect_fields(
        self,
        *,
        query: str,
        pending_payload: dict[str, Any],
        table_ctx: Any,
    ) -> SkillResult:
        if self._is_cancel(query):
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"clear_pending_action": True},
                message="已取消更新",
                reply_text="好的，已取消更新操作。",
            )

        if self._is_pending_expired(pending_payload):
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"clear_pending_action": True, "error_code": "update_collect_context_expired"},
                message="更新引导已超时",
                reply_text=get_user_message_by_code("update_collect_context_expired"),
            )

        record_id = str(pending_payload.get("record_id") or "").strip()
        if not record_id:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"clear_pending_action": True, "error_code": "update_collect_record_missing"},
                message="更新上下文缺少 record_id",
                reply_text=get_user_message_by_code("update_collect_record_missing"),
            )

        table_id = str(pending_payload.get("table_id") or table_ctx.table_id or "").strip() or None
        table_name = str(pending_payload.get("table_name") or table_ctx.table_name or "").strip() or None
        app_token = self._resolve_app_token(table_ctx=table_ctx, pending_payload=pending_payload)
        table_type = str(pending_payload.get("table_type") or self._resolve_table_type(table_name)).strip() or "case"
        source_fields_raw = pending_payload.get("source_fields")
        source_fields = dict(source_fields_raw) if isinstance(source_fields_raw, dict) else {}

        fields = self._collect_update_fields(query=query, planner_plan=None)
        if not fields:
            return self._build_update_collect_fields_result(
                record_id=record_id,
                source_fields=source_fields,
                table_id=table_id,
                table_name=table_name,
                table_type=table_type,
                app_token=app_token,
            )

        validation_error = self._validate_fields(fields)
        if validation_error:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"record_id": record_id, "invalid_fields": fields},
                message="字段值校验失败",
                reply_text=validation_error,
            )

        adapted_fields, unresolved, available = await self._table_adapter.adapt_fields_for_table(fields, table_id)
        if unresolved:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={
                    "record_id": record_id,
                    "table_id": table_id,
                    "table_name": table_name,
                    "unresolved_fields": unresolved,
                    "available_fields": available,
                },
                message="字段名与目标表不匹配",
                reply_text=self._table_adapter.build_field_not_found_message(unresolved, available, table_name),
            )

        if adapted_fields:
            fields = adapted_fields

        effective_preview_fields, diff_items, append_date = self._action_service.build_update_preview(
            table_name=table_name,
            fields=fields,
            source_fields=source_fields,
            append_date=None,
        )
        if not diff_items:
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={
                    "clear_pending_action": True,
                    "record_id": record_id,
                    "table_id": table_id,
                    "table_name": table_name,
                },
                message="无字段变更",
                reply_text="该字段已是目标值，无需更新。",
            )

        idempotency_key = str(pending_payload.get("idempotency_key") or "").strip() or None
        if not idempotency_key:
            idempotency_key = self._build_update_idempotency_key(record_id=record_id, fields=fields)

        return self._build_pending_update_result(
            action_name="update_record",
            record_id=record_id,
            fields=fields,
            preview_fields=effective_preview_fields,
            source_fields=source_fields,
            diff_items=diff_items,
            table_id=table_id,
            table_name=table_name,
            idempotency_key=idempotency_key,
            app_token=app_token,
            created_at=time.time(),
            ttl_seconds=self._confirm_ttl_seconds,
            append_date=append_date,
        )

    def _build_update_collect_fields_result(
        self,
        *,
        record_id: str,
        source_fields: dict[str, Any],
        table_id: str | None,
        table_name: str | None,
        table_type: str,
        app_token: str | None,
    ) -> SkillResult:
        case_no = str(source_fields.get("项目ID") or source_fields.get("案号") or record_id or "").strip()
        left = str(source_fields.get("委托人") or source_fields.get("委托人及联系方式") or "").strip()
        right = str(source_fields.get("对方当事人") or "").strip()
        identity = " vs ".join([item for item in [left, right] if item])
        if not identity:
            identity = str(source_fields.get("案由") or "").strip()

        reply_text = (
            "已定位到案件，请告诉我要修改什么。\n"
            "例如：开庭日改成2024-12-01、案件状态改为已结案、追加进展：今天收到法院通知、主办律师改成张三。"
        )

        ttl_seconds = 120
        payload = {
            "record_id": record_id,
            "table_id": table_id,
            "table_name": table_name,
            "table_type": table_type,
            "app_token": app_token,
            "source_fields": source_fields,
            "created_at": time.time(),
            "pending_ttl_seconds": ttl_seconds,
            "awaiting_fields": True,
        }

        return SkillResult(
            success=True,
            skill_name=self.name,
            data={
                "record_id": record_id,
                "table_id": table_id,
                "table_name": table_name,
                "table_type": table_type,
                "record_case_no": case_no,
                "record_identity": identity,
                "pending_action": {
                    "action": "update_collect_fields",
                    "ttl_seconds": ttl_seconds,
                    "payload": payload,
                },
            },
            message="等待补充修改字段",
            reply_text=reply_text,
        )

    def _resolve_table_type(self, table_name: str | None) -> str:
        normalized = str(table_name or "").replace(" ", "")
        if "合同" in normalized:
            return "contracts"
        if any(token in normalized for token in ("招投标", "投标", "台账")):
            return "bidding"
        if any(token in normalized for token in ("团队", "成员", "工作总览")):
            return "team_overview"
        return "case"

    def _resolve_app_token(
        self,
        *,
        table_ctx: Any,
        pending_payload: dict[str, Any] | None,
        extra: dict[str, Any] | None = None,
        planner_plan: dict[str, Any] | None = None,
    ) -> str | None:
        payload = pending_payload if isinstance(pending_payload, dict) else {}
        context_extra = extra if isinstance(extra, dict) else {}
        candidates: list[Any] = [
            payload.get("app_token"),
            getattr(table_ctx, "app_token", None),
            context_extra.get("app_token"),
        ]
        active_record = context_extra.get("active_record")
        if isinstance(active_record, dict):
            candidates.append(active_record.get("app_token"))
        if isinstance(planner_plan, dict):
            params = planner_plan.get("params")
            if isinstance(params, dict):
                candidates.append(params.get("app_token"))
        for key in ("BITABLE_APP_TOKEN", "FEISHU_BITABLE_APP_TOKEN", "APP_TOKEN"):
            candidates.append(os.getenv(key))

        for raw in candidates:
            token = str(raw or "").strip()
            if token:
                return token
        return None

    def _collect_update_fields(self, *, query: str, planner_plan: dict[str, Any] | None) -> dict[str, Any]:
        merged: dict[str, Any] = {}

        planner_fields = self._extract_fields_from_planner(planner_plan)
        for key, value in planner_fields.items():
            merged[str(key)] = value

        parsed_fields = self._parse_update_fields(query)
        for key, value in parsed_fields.items():
            merged[str(key)] = value

        kv_fields = self._parse_key_value_fields(query)
        for key, value in kv_fields.items():
            merged[str(key)] = value

        normalized: dict[str, Any] = {}
        for raw_key, raw_value in merged.items():
            key = str(raw_key).strip()
            if not key:
                continue
            mapped_key = self._field_aliases.get(key, key)
            normalized_value = self._normalize_field_value(mapped_key, raw_value)
            if normalized_value is None:
                continue
            if isinstance(normalized_value, str) and not normalized_value.strip():
                continue
            normalized[mapped_key] = normalized_value
        return normalized

    def _normalize_field_value(self, field_name: str, raw_value: Any) -> Any:
        if raw_value is None:
            return None
        if not isinstance(raw_value, str):
            return raw_value

        value = str(raw_value).strip().strip("\"'")
        for prefix in self._update_value_prefixes:
            if value.startswith(prefix):
                value = value[len(prefix):].strip()
                break
        value = re.sub(r"^(?:成|为)\s*", "", value).strip().strip("\"'")
        value = re.sub(r"^[：:=\s]+", "", value).strip().strip("\"'")
        if not value:
            return ""

        if field_name in self._date_field_names:
            parsed = parse_time_range(value)
            if parsed and parsed.date_from and parsed.date_to and parsed.date_from == parsed.date_to:
                return parsed.date_from

        return value

    def _is_pending_expired(self, pending_payload: dict[str, Any]) -> bool:
        created_at = pending_payload.get("created_at")
        try:
            created_at_value = float(str(created_at))
        except Exception:
            return False
        ttl_seconds = self._resolve_pending_ttl(pending_payload.get("pending_ttl_seconds"))
        return (time.time() - created_at_value) >= ttl_seconds

    def _resolve_pending_ttl(self, ttl_raw: Any) -> int:
        try:
            ttl_seconds = int(str(ttl_raw))
        except Exception:
            ttl_seconds = self._confirm_ttl_seconds
        return max(1, ttl_seconds)

    def _build_pending_update_result(
        self,
        *,
        action_name: str = "update_record",
        record_id: str,
        fields: dict[str, Any],
        preview_fields: dict[str, Any],
        source_fields: dict[str, Any],
        diff_items: list[dict[str, str]],
        table_id: str | None,
        table_name: str | None,
        idempotency_key: str | None,
        app_token: str | None,
        created_at: float,
        ttl_seconds: int,
        append_date: str,
        close_semantic: str = "default",
        reply_text: str | None = None,
    ) -> SkillResult:
        complete_preview_fields = self._ensure_preview_fields_cover_diff(
            preview_fields=preview_fields,
            fields=fields,
            source_fields=source_fields,
            diff_items=diff_items,
        )
        if action_name == "close_record":
            pending_data = self._action_service.build_pending_close_action_data(
                record_id=record_id,
                fields=complete_preview_fields,
                source_fields=source_fields,
                diff_items=diff_items,
                table_id=table_id,
                table_name=table_name,
                idempotency_key=idempotency_key,
                app_token=app_token,
                created_at=created_at,
                ttl_seconds=ttl_seconds,
                append_date=append_date,
                close_semantic=close_semantic,
            )
        else:
            pending_data = self._action_service.build_pending_update_action_data(
                action_name=action_name,
                record_id=record_id,
                fields=fields,
                preview_fields=complete_preview_fields,
                source_fields=source_fields,
                diff_items=diff_items,
                table_id=table_id,
                table_name=table_name,
                idempotency_key=idempotency_key,
                app_token=app_token,
                created_at=created_at,
                ttl_seconds=ttl_seconds,
                append_date=append_date,
            )
        return SkillResult(
            success=True,
            skill_name=self.name,
            data=pending_data,
            message="等待确认更新",
            reply_text=reply_text or self._build_update_confirm_reply(diff_items, ttl_seconds),
        )

    def _ensure_preview_fields_cover_diff(
        self,
        *,
        preview_fields: dict[str, Any],
        fields: dict[str, Any],
        source_fields: dict[str, Any],
        diff_items: list[dict[str, str]],
    ) -> dict[str, Any]:
        merged: dict[str, Any] = dict(preview_fields)
        for item in diff_items:
            if not isinstance(item, dict):
                continue
            field_name = str(item.get("field") or "").strip()
            if not field_name:
                continue
            if field_name in merged:
                continue
            if field_name in fields:
                merged[field_name] = fields.get(field_name)
                continue
            if field_name in source_fields:
                merged[field_name] = source_fields.get(field_name)
                continue
            merged[field_name] = item.get("new")
        return merged

    def _build_update_confirm_reply(self, diff_items: list[dict[str, str]], ttl_seconds: int) -> str:
        lines = ["请确认以下更新（旧值 -> 新值）："]
        for item in diff_items:
            field = str(item.get("field") or "")
            old = str(item.get("old") or "")
            new = str(item.get("new") or "")
            mode = str(item.get("mode") or "").strip().lower()
            if mode == "append":
                delta = str(item.get("delta") or "").strip()
                lines.append(f"- {field}")
                lines.append("  模式: 追加")
                lines.append(f"  旧值: {old}")
                if delta:
                    lines.append(f"  新增: {delta}")
                lines.append(f"  追加后: {new}")
            else:
                lines.append(f"- {field}")
                lines.append(f"  旧值: {old}")
                lines.append(f"  新值: {new}")
        lines.append(f"请在 {ttl_seconds} 秒内回复“确认”继续，回复“取消”终止。")
        return "\n".join(lines)

    def _extract_source_fields(self, record: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(record, dict):
            return {}
        fields_text = record.get("fields_text")
        if isinstance(fields_text, dict):
            return dict(fields_text)
        fields = record.get("fields")
        if isinstance(fields, dict):
            return dict(fields)
        return {}

    def _build_update_diff(self, source_fields: dict[str, Any], target_fields: dict[str, Any]) -> list[dict[str, str]]:
        diff_items: list[dict[str, str]] = []
        for key, new_value in target_fields.items():
            old_value = source_fields.get(key)
            if self._value_equal(old_value, new_value):
                continue
            diff_items.append(
                {
                    "field": str(key),
                    "old": self._to_text(old_value),
                    "new": self._to_text(new_value),
                }
            )
        return diff_items

    def _value_equal(self, old_value: Any, new_value: Any) -> bool:
        if isinstance(old_value, (dict, list)) or isinstance(new_value, (dict, list)):
            try:
                old_norm = json.dumps(old_value, ensure_ascii=False, sort_keys=True)
                new_norm = json.dumps(new_value, ensure_ascii=False, sort_keys=True)
                return old_norm == new_norm
            except Exception:
                return self._to_text(old_value) == self._to_text(new_value)
        return self._to_text(old_value) == self._to_text(new_value)

    def _to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, ensure_ascii=False, sort_keys=True)
            except Exception:
                return str(value)
        return str(value).strip()

    def _build_update_idempotency_key(self, *, record_id: str, fields: dict[str, Any]) -> str:
        payload = {
            "record_id": record_id,
            "fields": fields,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]
        return f"update-{digest}"

    async def _execute_pending_repair(
        self,
        *,
        query: str,
        pending_action: str,
        pending_payload: dict[str, Any],
        table_ctx: Any,
    ) -> SkillResult:
        if self._is_cancel(query):
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"clear_pending_action": True},
                message="已取消补录",
                reply_text="好的，已取消子表补录。",
            )

        table_id = str(pending_payload.get("table_id") or table_ctx.table_id or "").strip() or None
        table_name = str(pending_payload.get("table_name") or table_ctx.table_name or "").strip() or None
        raw_idempotency_key = pending_payload.get("idempotency_key")
        idempotency_key = str(raw_idempotency_key).strip() if raw_idempotency_key else None
        if not table_id:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"clear_pending_action": True, "error_code": "update_repair_subtable_missing"},
                message="补录缺少子表信息",
                reply_text=get_user_message_by_code("update_repair_subtable_missing"),
            )

        fields_raw = pending_payload.get("fields")
        fields: dict[str, Any] = {}
        if isinstance(fields_raw, dict):
            for key, value in fields_raw.items():
                field_name = str(key).strip()
                if field_name:
                    fields[field_name] = value
        parsed_fields = self._parse_update_fields(query)
        kv_fields = self._parse_key_value_fields(query)
        for key, value in parsed_fields.items():
            fields[key] = value
        for key, value in kv_fields.items():
            fields[key] = value

        required_raw = pending_payload.get("required_fields")
        required_fields = [str(item).strip() for item in required_raw if str(item).strip()] if isinstance(required_raw, list) else []
        if not required_fields:
            required_fields = [str(key) for key in fields.keys() if str(key).strip()]
        missing_fields = self._missing_required_fields(fields, required_fields)

        if missing_fields:
            return self._build_pending_repair_result(
                pending_action=pending_action,
                pending_payload={
                    **pending_payload,
                    "fields": fields,
                    "required_fields": required_fields,
                    "table_id": table_id,
                    "table_name": table_name,
                },
                reply_text=(
                    "子表补录还缺少这些字段：\n"
                    + "\n".join([f"- {name}" for name in missing_fields])
                    + "\n请继续补充。"
                ),
            )

        auto_submit = bool(pending_payload.get("auto_submit", False))
        if not auto_submit and not parsed_fields and not self._is_confirm(query):
            return self._build_pending_repair_result(
                pending_action=pending_action,
                pending_payload={
                    **pending_payload,
                    "fields": fields,
                    "required_fields": required_fields,
                    "table_id": table_id,
                    "table_name": table_name,
                },
                reply_text="已收到，请回复“确认”继续写入子表。",
            )

        if auto_submit and not parsed_fields and not self._is_confirm(query):
            error_hint = str(pending_payload.get("error") or "").strip()
            prefix = "子表补录仍需要您提供修正后的字段值。"
            if error_hint:
                prefix = f"子表写入失败：{error_hint}"
            return self._build_pending_repair_result(
                pending_action=pending_action,
                pending_payload={
                    **pending_payload,
                    "fields": fields,
                    "required_fields": required_fields,
                    "table_id": table_id,
                    "table_name": table_name,
                },
                reply_text=f"{prefix}\n请按“字段是值”的格式补充后继续。",
            )

        validation_error = self._validate_fields(fields)
        if validation_error:
            return self._build_pending_repair_result(
                pending_action=pending_action,
                pending_payload={
                    **pending_payload,
                    "fields": fields,
                    "required_fields": required_fields,
                    "table_id": table_id,
                    "table_name": table_name,
                },
                reply_text=validation_error,
            )

        adapted_fields, unresolved, available = await self._table_adapter.adapt_fields_for_table(
            fields,
            table_id,
        )
        if unresolved:
            return self._build_pending_repair_result(
                pending_action=pending_action,
                pending_payload={
                    **pending_payload,
                    "fields": fields,
                    "required_fields": required_fields,
                    "table_id": table_id,
                    "table_name": table_name,
                },
                reply_text=self._table_adapter.build_field_not_found_message(unresolved, available, table_name),
            )
        if adapted_fields:
            fields = adapted_fields

        record_ids_raw = pending_payload.get("record_ids")
        record_ids = [str(item).strip() for item in record_ids_raw if str(item).strip()] if isinstance(record_ids_raw, list) else []
        if not record_ids:
            match_field = str(pending_payload.get("match_field") or "").strip()
            match_value = pending_payload.get("match_value")
            if match_field and match_value not in (None, ""):
                try:
                    records = await self._table_adapter.search_exact_records(
                        field=match_field,
                        value=match_value,
                        table_id=table_id,
                    )
                    record_ids = [str(item.get("record_id") or "").strip() for item in records if str(item.get("record_id") or "").strip()]
                except Exception as exc:
                    logger.warning("Repair search failed: %s", exc)

        if not record_ids:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"clear_pending_action": True, "error_code": "update_repair_record_missing"},
                message="补录目标不存在",
                reply_text=get_user_message_by_code("update_repair_record_missing"),
            )

        updated_count = 0
        for record_id in record_ids:
            result = await self._data_writer.update(
                table_id,
                record_id,
                fields,
                idempotency_key=idempotency_key,
            )
            if not result.success:
                error = str(result.error or "子表更新失败")
                return self._build_pending_repair_result(
                    pending_action=pending_action,
                    pending_payload={
                        **pending_payload,
                        "fields": fields,
                        "required_fields": required_fields,
                        "table_id": table_id,
                        "table_name": table_name,
                        "record_ids": record_ids,
                    },
                    reply_text=get_user_message_by_code("update_repair_failed", detail=error),
                )
            updated_count += 1

        return SkillResult(
            success=True,
            skill_name=self.name,
            data={
                "clear_pending_action": True,
                "record_id": record_ids[0],
                "updated_fields": fields,
                "table_id": table_id,
                "table_name": table_name,
            },
            message="子表补录成功",
            reply_text=f"已完成子表补录，更新 {updated_count} 条记录。",
        )

    def _build_pending_repair_result(
        self,
        *,
        pending_action: str,
        pending_payload: dict[str, Any],
        reply_text: str,
    ) -> SkillResult:
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={
                "pending_action": {
                    "action": pending_action,
                    "payload": pending_payload,
                },
                "table_id": pending_payload.get("table_id"),
                "table_name": pending_payload.get("table_name"),
            },
            message="等待子表补录",
            reply_text=reply_text,
        )

    def _is_confirm(self, text: str) -> bool:
        normalized = str(text or "").strip().lower().strip("，。！？!?,. ")
        return normalized in self._confirm_phrases

    def _is_cancel(self, text: str) -> bool:
        normalized = str(text or "").strip().lower().strip("，。！？!?,. ")
        return normalized in self._cancel_phrases

    def _missing_required_fields(self, fields: dict[str, Any], required_fields: list[str]) -> list[str]:
        missing: list[str] = []
        for field_name in required_fields:
            value = fields.get(field_name)
            if value is None:
                missing.append(field_name)
                continue
            text = str(value).strip() if not isinstance(value, (dict, list)) else value
            if text == "" or text == [] or text == {}:
                missing.append(field_name)
        return missing

    def _normalize_field_segment(self, value: str) -> str:
        segment = str(value).strip()
        segment = re.sub(r"^(?:请|帮我|麻烦|把|将)", "", segment).strip()
        segment = re.sub(r"^(?:修改|更新|调整)", "", segment).strip()
        if " 的" in segment:
            segment = segment.split(" 的", 1)[1].strip()
        if "的" in segment and any(token in segment for token in ["案号", "项目", "记录"]):
            segment = segment.rsplit("的", 1)[-1].strip()
        segment = re.sub(r"(?:案件|案子|记录|项目)的?内容$", "", segment).strip()
        segment = segment.replace("内容", "").strip()
        mapped = self._field_aliases.get(segment, segment)
        return str(mapped).strip()

    def _validate_fields(self, fields: dict[str, Any]) -> str | None:
        for field_name, options in self._field_options.items():
            if field_name not in fields:
                continue
            value = str(fields.get(field_name) or "").strip()
            if not value:
                continue
            if value not in options:
                option_text = "、".join(options)
                return f"\"{field_name}\"的可选值为：{option_text}。请选择其中一个。"
        return None

    def _build_multi_record_reply(self, records: list[dict[str, Any]]) -> str:
        lines = [f"找到 {len(records)} 条记录，请指定要更新哪一条："]
        for index, record in enumerate(records[:5], start=1):
            fields = record.get("fields_text") or record.get("fields") or {}
            case_no = str(fields.get("案号") or fields.get("项目ID") or "未知")
            cause = str(fields.get("案由") or fields.get("案件分类") or "")
            if cause:
                lines.append(f"{index}. {case_no} - {cause}")
            else:
                lines.append(f"{index}. {case_no}")
        lines.append("可回复“第一个/第二个”后继续更新。")
        return "\n".join(lines)

    async def _search_records_by_query(self, query: str, table_id: str | None = None) -> list[dict[str, Any]]:
        import re

        exact_case = re.search(r"(?:案号|案件号)[是为:：\s]*([A-Za-z0-9\-_/（）()_\u4e00-\u9fa5]+)", query)
        exact_project = re.search(r"(?:项目ID|项目编号|项目号)[是为:：\s]*([A-Za-z0-9\-_/（）()_\u4e00-\u9fa5]+)", query)
        bare_project = re.search(r"(?<![A-Za-z0-9_-])([A-Za-z]{2,}-\d{4,}(?:-\d{2,})?)(?![A-Za-z0-9_-])", query)
        bare_case = re.search(r"([（(]\d{4}[）)][^\s，。,.！？!]{4,64})", query)

        field_name = None
        field_value = None
        if exact_case:
            field_name = "案号"
            field_value = exact_case.group(1).strip()
        elif exact_project:
            field_name = "项目ID"
            field_value = exact_project.group(1).strip()
        elif bare_project:
            field_name = "项目ID"
            field_value = bare_project.group(1).strip()
        elif bare_case:
            field_name = "案号"
            field_value = bare_case.group(1).strip()

        if not field_name or not field_value:
            return []

        try:
            return await self._table_adapter.search_exact_records(
                field=field_name,
                value=field_value,
                table_id=table_id,
            )
        except Exception as exc:
            logger.warning("UpdateSkill pre-search failed: %s", exc)
            return []

    def _has_record_identifier_hint(self, query: str) -> bool:
        text = str(query or "")
        if re.search(r"(?:案号|案件号|项目ID|项目编号|项目号)[是为:：\s]*[A-Za-z0-9\-_/（）()_\u4e00-\u9fa5]+", text):
            return True
        if re.search(r"(?<![A-Za-z0-9_-])[A-Za-z]{2,}-\d{4,}(?:-\d{2,})?(?![A-Za-z0-9_-])", text):
            return True
        if re.search(r"[（(]\d{4}[）)][^\s，。,.！？!]{4,64}", text):
            return True
        return False

    def _extract_fields_from_planner(self, planner_plan: dict[str, Any] | None) -> dict[str, Any]:
        """从 planner 输出提取更新字段。"""
        if not isinstance(planner_plan, dict):
            return {}
        if planner_plan.get("tool") != "record.update":
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

    def _resolve_pending_action_name(
        self,
        *,
        query: str,
        planner_plan: dict[str, Any] | None,
        table_name: str | None,
    ) -> tuple[str, str]:
        close_semantic = self._extract_close_semantic_from_planner(planner_plan)
        if close_semantic:
            return "close_record", close_semantic

        inferred_semantic = self._action_service.resolve_close_semantic(query, table_name, default="")
        if inferred_semantic:
            return "close_record", inferred_semantic
        return "update_record", "default"

    def _extract_close_semantic_from_planner(self, planner_plan: dict[str, Any] | None) -> str:
        if not isinstance(planner_plan, dict):
            return ""

        tool = str(planner_plan.get("tool") or "").strip()
        intent = str(planner_plan.get("intent") or "").strip().lower()
        params_raw = planner_plan.get("params")
        params = params_raw if isinstance(params_raw, dict) else {}
        explicit = str(params.get("close_semantic") or "").strip()
        if explicit:
            if explicit in {"default", "enforcement_end"}:
                return explicit
            logger.warning(
                "Planner close_semantic invalid, fallback to default",
                extra={
                    "event_code": "update.planner.close_semantic.invalid",
                    "close_semantic": explicit,
                },
            )
            return "default"

        if tool == "record.close":
            return "default"
        if intent in {"close_record", "record.close", "close"}:
            return "default"
        return ""

    def _parse_key_value_fields(self, query: str) -> dict[str, Any]:
        import re

        fields: dict[str, Any] = {}
        pattern = r"([^\s,，、]+?)(?:是|为|：|:)\s*([^\s,，、是为：:]+)"
        matches = re.findall(pattern, query)
        for alias, value in matches:
            name = self._normalize_field_segment(alias.strip())
            mapped = self._field_aliases.get(name, name)
            value_text = value.strip()
            if mapped in {"案号", "项目ID"} and "的案件" in value_text:
                continue
            if mapped and value_text:
                fields[mapped] = self._normalize_field_value(mapped, value_text)

        direct_patterns = {
            "案件状态": r"(?:案件状态|状态)\s*([^,，。；;\n]+)",
            "开庭日": r"(?:开庭日|开庭)\s*([^,，。；;\n]+)",
            "审理法院": r"(?:审理法院|法院)\s*([^,，。；;\n]+)",
            "进展": r"进展\s*([^,，。；;\n]+)",
            "金额": r"(?:金额|费用)\s*([^,，。；;\n]+)",
        }
        for field_name, rule in direct_patterns.items():
            match = re.search(rule, query)
            if not match:
                continue
            value = match.group(1).strip()
            if value:
                fields[field_name] = self._normalize_field_value(field_name, value)
        return fields
# endregion
