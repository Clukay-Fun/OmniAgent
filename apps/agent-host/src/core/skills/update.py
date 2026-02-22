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
from src.core.skills.data_writer import DataWriter
from src.core.skills.multi_table_linker import MultiTableLinker
from src.core.skills.response_pool import pool
from src.core.skills.table_adapter import TableAdapter
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

        update_cfg = self._skills_config.get("update", {}) if isinstance(self._skills_config, dict) else {}
        if not isinstance(update_cfg, dict):
            update_cfg = {}
        default_options = {
            "案件状态": ["进行中", "已结案", "暂停"],
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
        planner_plan = extra.get("planner_plan") if isinstance(extra.get("planner_plan"), dict) else None
        last_result = context.last_result or {}
        table_ctx = await self._table_adapter.resolve_table_context(query, extra, last_result)

        pending_action, pending_payload = self._extract_pending_update(extra)
        if pending_action and pending_payload:
            return await self._execute_pending_repair(
                query=query,
                pending_action=pending_action,
                pending_payload=pending_payload,
                table_ctx=table_ctx,
            )

        planner_params = planner_plan.get("params") if isinstance(planner_plan, dict) else None
        planner_record_id = None
        if isinstance(planner_params, dict):
            rid = planner_params.get("record_id")
            planner_record_id = str(rid).strip() if rid else None

        records = []
        if not planner_record_id:
            exact_records = await self._search_records_by_query(query, table_ctx.table_id)
            if exact_records:
                records = exact_records

        if not records and not planner_record_id:
            active_record = extra.get("active_record")
            if isinstance(active_record, dict) and active_record.get("record_id"):
                records = [active_record]

        if not records and not planner_record_id:
            last_records = last_result.get("records", [])
            if isinstance(last_records, list):
                records = last_records

        if not records and not planner_record_id:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="需要先定位要更新的记录",
                reply_text="请先提供案号/项目ID，或先查询后再更新。",
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
        else:
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
                reply_text="无法获取记录 ID，更新失败。",
            )

        # 解析更新字段（简化版：从查询中提取）
        fields = self._extract_fields_from_planner(planner_plan)
        parsed_fields = self._parse_update_fields(query)
        kv_fields = self._parse_key_value_fields(query)
        for k, v in parsed_fields.items():
            fields[k] = v
        for k, v in kv_fields.items():
            fields[k] = v
        if not fields:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="无法解析更新字段",
                reply_text="请明确要更新的字段和值，例如：把开庭日改成2024-12-01",
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
        
        # 调用 MCP 更新工具
        try:
            write_result = await self._data_writer.update(
                table_ctx.table_id,
                record_id,
                fields,
            )

            if not write_result.success:
                error = write_result.error or "未知错误"
                return SkillResult(
                    success=False,
                    skill_name=self.name,
                    message=f"更新失败: {error}",
                    reply_text=f"更新失败：{error}",
                )
            
            record_url = write_result.record_url or ""
            updated_fields = write_result.fields if isinstance(write_result.fields, dict) else {}
            
            # 构建回复
            opener = pool.pick("update_success", "✅ 更新成功！")
            field_list = "\n".join([f"  • {k}: {v}" for k, v in fields.items()])
            reply_text = (
                f"{opener}\n\n"
                f"已更新字段：\n{field_list}\n\n"
                f"🔗 查看详情：{record_url}"
            )

            source_fields = record.get("fields_text") if isinstance(record, dict) else None
            if not isinstance(source_fields, dict):
                source_fields = record.get("fields") if isinstance(record, dict) else {}
            link_sync = await self._linker.sync_after_update(
                parent_table_id=table_ctx.table_id,
                parent_table_name=table_ctx.table_name,
                updated_fields=fields,
                source_fields=source_fields if isinstance(source_fields, dict) else {},
            )
            link_summary = self._linker.summarize(link_sync)
            repair_payload = self._linker.build_repair_pending(link_sync)
            pending_action = None
            if repair_payload:
                repair_action = str(repair_payload.get("repair_action") or "repair_child_create").strip()
                pending_action = {
                    "action": repair_action,
                    "payload": repair_payload,
                }
                reply_text += (
                    "\n\n"
                    "子表同步失败，请补充或修正后继续。"
                    "例如：金额是1000，状态是待支付。"
                )
            if link_summary:
                reply_text += f"\n\n{link_summary}"
            
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={
                    "clear_pending_action": False if pending_action else True,
                    "pending_action": pending_action,
                    "record_id": record_id,
                    "updated_fields": fields,
                    "record_url": record_url,
                    "table_id": table_ctx.table_id,
                    "table_name": table_ctx.table_name,
                    "source_fields": source_fields if isinstance(source_fields, dict) else {},
                    "link_sync": link_sync,
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
                reply_text=pool.pick("error", "更新失败，请稍后重试。"),
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
            field_value = match.group(2).strip()
            fields[field_name] = field_value
            return fields
        
        # 模式2: 修改X为Y / 更新X为Y
        pattern2 = re.compile(r"(?:修改|更新)(.+?)为(.+)")
        match = pattern2.search(query)
        if match:
            field_name = self._normalize_field_segment(match.group(1).strip())
            field_value = match.group(2).strip()
            fields[field_name] = field_value
            return fields
        
        # 模式3: 更新X=Y
        pattern3 = re.compile(r"更新(.+?)[=为](.+)")
        match = pattern3.search(query)
        if match:
            field_name = self._normalize_field_segment(match.group(1).strip())
            field_value = match.group(2).strip()
            fields[field_name] = field_value
            return fields
        
        return fields

    def _extract_pending_update(self, extra: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        pending = extra.get("pending_action")
        if not isinstance(pending, dict):
            return None, {}
        action = str(pending.get("action") or "").strip()
        if action not in {"repair_child_update"}:
            return None, {}
        payload = pending.get("payload")
        if not isinstance(payload, dict):
            return None, {}
        return action, payload

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
        if not table_id:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"clear_pending_action": True},
                message="补录缺少子表信息",
                reply_text="补录失败：未找到目标子表，请重新发起操作。",
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
                data={"clear_pending_action": True},
                message="补录目标不存在",
                reply_text="未找到可补录的子表记录，请重新发起操作。",
            )

        updated_count = 0
        for record_id in record_ids:
            result = await self._data_writer.update(
                table_id,
                record_id,
                fields,
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
                    reply_text=f"子表补录失败：{error}\n请修正后继续。",
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
        if " 的" in segment:
            segment = segment.split(" 的", 1)[1].strip()
        if "的" in segment and any(token in segment for token in ["案号", "项目", "记录"]):
            segment = segment.rsplit("的", 1)[-1].strip()
        return segment

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

        field_name = None
        field_value = None
        if exact_case:
            field_name = "案号"
            field_value = exact_case.group(1).strip()
        elif exact_project:
            field_name = "项目ID"
            field_value = exact_project.group(1).strip()

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

    def _parse_key_value_fields(self, query: str) -> dict[str, Any]:
        import re

        fields: dict[str, Any] = {}
        pattern = r"([^\s,，、]+?)(?:是|为|：|:)\s*([^\s,，、是为：:]+)"
        matches = re.findall(pattern, query)
        for alias, value in matches:
            name = self._normalize_field_segment(alias.strip())
            mapped = self._field_aliases.get(name, name)
            if mapped and value.strip():
                fields[mapped] = value.strip()

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
                fields[field_name] = value
        return fields
# endregion
