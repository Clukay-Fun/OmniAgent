"""
描述: 案件记录创建技能
主要功能:
    - 解析用户输入中的字段信息
    - 调用 MCP 接口创建多维表格记录
    - 返回创建结果及记录链接
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from src.core.skills.base import BaseSkill
from src.core.skills.action_execution_service import ActionExecutionService
from src.core.skills.data_writer import DataWriter
from src.core.skills.multi_table_linker import MultiTableLinker
from src.core.skills.response_pool import pool
from src.core.skills.table_adapter import TableAdapter
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
        *,
        data_writer: DataWriter,
    ) -> None:
        """
        初始化创建技能

        参数:
            mcp_client: MCP 客户端实例
            settings: 配置信息
        """
        self._mcp = mcp_client
        self._settings = settings
        self._skills_config = skills_config or {}
        if data_writer is None:
            raise ValueError("CreateSkill requires an injected data_writer")
        self._data_writer = data_writer
        self._table_adapter = TableAdapter(mcp_client, skills_config=skills_config)
        self._linker = MultiTableLinker(
            mcp_client,
            skills_config=skills_config,
            data_writer=self._data_writer,
        )
        self._action_service = ActionExecutionService(data_writer=self._data_writer, linker=self._linker)
        
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

        create_cfg = self._skills_config.get("create", {}) if isinstance(self._skills_config, dict) else {}
        required = create_cfg.get("required_fields", ["案号", "委托人", "案由"])
        self._required_fields = [str(item).strip() for item in required if str(item).strip()]
        if not self._required_fields:
            self._required_fields = ["案号", "委托人", "案由"]

        confirm_phrases = create_cfg.get("confirm_phrases", ["确认", "确认创建", "是", "是的", "ok", "yes"])
        self._confirm_phrases = {str(item).strip().lower() for item in confirm_phrases if str(item).strip()}
        cancel_phrases = create_cfg.get("cancel_phrases", ["取消", "算了", "不了", "不创建", "不建了", "不用了"])
        self._cancel_phrases = {str(item).strip().lower() for item in cancel_phrases if str(item).strip()}

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        执行创建逻辑

        参数:
            context: 技能上下文

        返回:
            技能执行结果
        """
        query = context.query.strip()
        extra = context.extra or {}
        planner_plan = extra.get("planner_plan") if isinstance(extra.get("planner_plan"), dict) else None
        table_ctx = await self._table_adapter.resolve_table_context(query, extra, context.last_result)

        pending_payload = self._extract_pending_create(extra)
        has_pending_flow = bool(pending_payload)
        pending_action_name = str(pending_payload.get("repair_action") or "create_record").strip() if has_pending_flow else "create_record"
        raw_idempotency_key = pending_payload.get("idempotency_key") or extra.get("idempotency_key")
        idempotency_key = str(raw_idempotency_key).strip() if raw_idempotency_key else None
        if pending_payload.get("table_id") and not table_ctx.table_id:
            table_ctx.table_id = str(pending_payload.get("table_id"))
        if pending_payload.get("table_name") and not table_ctx.table_name:
            table_ctx.table_name = str(pending_payload.get("table_name"))
        app_token = self._resolve_app_token(
            extra=extra,
            planner_plan=planner_plan,
            pending_payload=pending_payload,
            table_ctx=table_ctx,
        )

        if self._is_cancel(query):
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"clear_pending_action": True},
                message="已取消创建",
                reply_text="好的，已取消创建操作。",
            )

        fields: dict[str, Any] = {}
        pending_fields = pending_payload.get("fields")
        if isinstance(pending_fields, dict):
            for key, value in pending_fields.items():
                field_name = str(key).strip()
                if field_name:
                    fields[field_name] = value

        planner_fields = self._extract_fields_from_planner(planner_plan)
        for k, v in planner_fields.items():
            fields.setdefault(k, v)

        parsed_fields = self._parse_fields(query)
        for k, v in parsed_fields.items():
            fields[k] = v

        required_fields = pending_payload.get("required_fields")
        if not isinstance(required_fields, list) or not required_fields:
            required_fields = self._required_fields
        required_fields = [str(item).strip() for item in required_fields if str(item).strip()]

        awaiting_confirm = bool(pending_payload.get("awaiting_confirm"))
        awaiting_duplicate_confirm = bool(pending_payload.get("awaiting_duplicate_confirm"))
        duplicate_checked = bool(pending_payload.get("duplicate_checked"))
        skip_duplicate_check = bool(pending_payload.get("skip_duplicate_check"))
        auto_submit = bool(pending_payload.get("auto_submit", False))
        has_new_input = bool(parsed_fields)

        missing_fields = self._missing_required_fields(fields, required_fields)

        if awaiting_duplicate_confirm and not self._is_confirm(query):
            duplicate_field = str(pending_payload.get("duplicate_field") or self._action_service.resolve_duplicate_field_name(table_ctx.table_name) or "案号").strip()
            duplicate_value = str(pending_payload.get("duplicate_value") or fields.get(duplicate_field) or "").strip()
            duplicate_count = int(pending_payload.get("duplicate_count") or 1)
            return self._build_pending_result(
                action_name=pending_action_name,
                fields=fields,
                required_fields=required_fields,
                table_id=table_ctx.table_id,
                table_name=table_ctx.table_name,
                app_token=app_token,
                message="案号重复待确认",
                reply_text=(
                    f"字段“{duplicate_field}”值“{duplicate_value}”已存在（命中 {duplicate_count} 条记录）。\n"
                    "如果仍需创建，请回复“确认”。"
                ),
                awaiting_duplicate_confirm=True,
                duplicate_count=duplicate_count,
                duplicate_field=duplicate_field,
                duplicate_value=duplicate_value,
                duplicate_checked=True,
                idempotency_key=idempotency_key,
            )

        if not missing_fields and not skip_duplicate_check:
            duplicate_field = self._action_service.resolve_duplicate_field_name(table_ctx.table_name)
            duplicate_value = str(fields.get(duplicate_field or "") or "").strip() if duplicate_field else ""
            if duplicate_field and duplicate_value and not duplicate_checked and not awaiting_duplicate_confirm:
                duplicate_count = await self._count_duplicates(duplicate_field, duplicate_value, table_ctx.table_id)
                if duplicate_count > 0:
                    return self._build_pending_result(
                        action_name=pending_action_name,
                        fields=fields,
                        required_fields=required_fields,
                        table_id=table_ctx.table_id,
                        table_name=table_ctx.table_name,
                        app_token=app_token,
                        message="案号重复待确认",
                        reply_text=(
                            f"字段“{duplicate_field}”值“{duplicate_value}”已存在（命中 {duplicate_count} 条记录）。\n"
                            "如果仍需创建，请回复“确认”；若取消请回复“取消”。"
                        ),
                        awaiting_duplicate_confirm=True,
                        duplicate_count=duplicate_count,
                        duplicate_field=duplicate_field,
                        duplicate_value=duplicate_value,
                        duplicate_checked=True,
                        idempotency_key=idempotency_key,
                    )
                duplicate_checked = True

        if missing_fields:
            return self._build_pending_result(
                action_name=pending_action_name,
                fields=fields,
                required_fields=required_fields,
                table_id=table_ctx.table_id,
                table_name=table_ctx.table_name,
                app_token=app_token,
                message="缺少必填字段",
                reply_text=self._build_missing_fields_reply(missing_fields),
                duplicate_checked=duplicate_checked,
                idempotency_key=idempotency_key,
            )

        if has_pending_flow and auto_submit and not has_new_input and not self._is_confirm(query):
            return self._build_pending_result(
                action_name=pending_action_name,
                fields=fields,
                required_fields=required_fields,
                table_id=table_ctx.table_id,
                table_name=table_ctx.table_name,
                app_token=app_token,
                message="等待补录字段",
                reply_text="请按“字段是值”的格式继续补录子表数据。",
                duplicate_checked=duplicate_checked,
                skip_duplicate_check=skip_duplicate_check,
                idempotency_key=idempotency_key,
            )

        if has_pending_flow and not auto_submit and awaiting_confirm and not self._is_confirm(query):
            return self._build_pending_result(
                action_name=pending_action_name,
                fields=fields,
                required_fields=required_fields,
                table_id=table_ctx.table_id,
                table_name=table_ctx.table_name,
                app_token=app_token,
                message="等待确认创建",
                reply_text=self._build_confirm_reply(fields),
                awaiting_confirm=True,
                duplicate_checked=duplicate_checked,
                idempotency_key=idempotency_key,
            )

        if has_pending_flow and not auto_submit and not awaiting_confirm and not self._is_confirm(query):
            return self._build_pending_result(
                action_name=pending_action_name,
                fields=fields,
                required_fields=required_fields,
                table_id=table_ctx.table_id,
                table_name=table_ctx.table_name,
                app_token=app_token,
                message="等待确认创建",
                reply_text=self._build_confirm_reply(fields),
                awaiting_confirm=True,
                duplicate_checked=duplicate_checked,
                idempotency_key=idempotency_key,
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
            outcome = await self._action_service.execute_create(
                table_id=table_ctx.table_id,
                table_name=table_ctx.table_name,
                fields=fields,
                idempotency_key=idempotency_key,
                app_token=app_token,
            )
            if not outcome.success:
                return SkillResult(
                    success=False,
                    skill_name=self.name,
                    message=outcome.message,
                    reply_text=outcome.reply_text or pool.pick("error", "创建记录失败，请稍后重试。"),
                )

            return SkillResult(
                success=True,
                skill_name=self.name,
                data=outcome.data,
                message=outcome.message,
                reply_text=outcome.reply_text,
            )
                
        except Exception as e:
            logger.error(f"CreateSkill execution error: {e}")
            return SkillResult(
                success=False,
                skill_name=self.name,
                message=str(e),
                reply_text=pool.pick("error", "创建记录失败，请稍后重试。"),
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

        # 模式2：字段+值（无连接词）
        direct_patterns = {
            "案号": r"案号\s*([A-Za-z0-9\-_/（）()\u4e00-\u9fa5]+)",
            "委托人": r"委托人\s*([^,，。；;\n]+)",
            "案由": r"案由\s*([^,，。；;\n]+)",
            "主办律师": r"主办律师\s*([^,，。；;\n]+)",
            "协办律师": r"协办律师\s*([^,，。；;\n]+)",
            "审理法院": r"(?:审理法院|法院)\s*([^,，。；;\n]+)",
            "开庭日": r"(?:开庭日|开庭)\s*([^,，。；;\n]+)",
        }
        for field_name, pattern in direct_patterns.items():
            match = re.search(pattern, query)
            if not match:
                continue
            value = match.group(1).strip()
            if value:
                fields[field_name] = value
        
        return fields

    def _extract_pending_create(self, extra: dict[str, Any]) -> dict[str, Any]:
        pending = extra.get("pending_action")
        if not isinstance(pending, dict):
            return {}
        action = str(pending.get("action") or "").strip()
        if action not in {"create_record", "repair_child_write", "repair_child_create"}:
            return {}
        payload = pending.get("payload")
        if not isinstance(payload, dict):
            return {}
        result = dict(payload)
        if action != "create_record":
            result.setdefault("repair_action", action)
        return result

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

    def _build_missing_fields_reply(self, missing_fields: list[str]) -> str:
        lines = ["好的，还需要以下必填信息："]
        for index, name in enumerate(missing_fields, start=1):
            lines.append(f"{index}. {name}")
        lines.append("您可以一次性提供，也可以逐项告诉我。")
        return "\n".join(lines)

    def _build_confirm_reply(self, fields: dict[str, Any]) -> str:
        lines = ["请确认以下信息："]
        for key in self._required_fields:
            if key in fields:
                lines.append(f"- {key}：{fields.get(key)}")
        for key, value in fields.items():
            if key in self._required_fields:
                continue
            lines.append(f"- {key}：{value}")
        lines.append("确认创建吗？回复“确认”继续，回复“取消”终止。")
        return "\n".join(lines)

    def _build_pending_result(
        self,
        *,
        action_name: str = "create_record",
        fields: dict[str, Any],
        required_fields: list[str],
        table_id: str | None,
        table_name: str | None,
        app_token: str | None,
        message: str,
        reply_text: str,
        awaiting_confirm: bool = False,
        awaiting_duplicate_confirm: bool = False,
        duplicate_count: int = 0,
        duplicate_field: str = "",
        duplicate_value: str = "",
        duplicate_checked: bool = False,
        skip_duplicate_check: bool = False,
        idempotency_key: str | None = None,
    ) -> SkillResult:
        payload: dict[str, Any] = {
            "fields": fields,
            "required_fields": required_fields,
            "awaiting_confirm": awaiting_confirm,
            "awaiting_duplicate_confirm": awaiting_duplicate_confirm,
            "duplicate_count": duplicate_count,
            "duplicate_field": duplicate_field,
            "duplicate_value": duplicate_value,
            "duplicate_checked": duplicate_checked,
            "skip_duplicate_check": skip_duplicate_check,
            "table_id": table_id,
            "table_name": table_name,
            "app_token": app_token,
        }
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        return SkillResult(
            success=True,
            skill_name=self.name,
            data={
                "pending_action": {
                    "action": action_name,
                    "payload": payload,
                },
                "table_id": table_id,
                "table_name": table_name,
            },
            message=message,
            reply_text=reply_text,
        )

    def _resolve_app_token(
        self,
        *,
        extra: dict[str, Any],
        planner_plan: dict[str, Any] | None,
        pending_payload: dict[str, Any],
        table_ctx: Any,
    ) -> str | None:
        candidates: list[Any] = [
            pending_payload.get("app_token"),
            getattr(table_ctx, "app_token", None),
            extra.get("app_token"),
        ]
        active_record = extra.get("active_record")
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

    async def _count_duplicates(self, field_name: str, value: str, table_id: str | None) -> int:
        if not field_name or not value:
            return 0
        try:
            records = await self._table_adapter.search_exact_records(
                field=field_name,
                value=value,
                table_id=table_id,
            )
            return len(records)
        except Exception as exc:
            logger.warning("CreateSkill duplicate pre-check failed: %s", exc)
            return 0

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
