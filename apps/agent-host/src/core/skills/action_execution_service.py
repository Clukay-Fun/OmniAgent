from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
from typing import Any, Mapping

import yaml

from src.core.skills.action_smart_engine import ActionSmartEngine
from src.core.skills.auto_reminders import build_auto_reminder_items
from src.core.skills.data_writer import DataWriter
from src.core.skills.locator_triplet import validate_locator_triplet


@dataclass
class ActionExecutionOutcome:
    success: bool
    message: str
    reply_text: str
    data: dict[str, Any]


class ActionExecutionService:
    """Unified write execution for C1/C2/C3 actions."""

    _BUILTIN_CONFIG: dict[str, Any] = {
        "table_type_alias": {
            "case": ["案件"],
            "contracts": ["合同"],
            "bidding": ["招投标", "投标", "台账"],
            "team_overview": ["团队成员工作总览", "团队工作总览", "团队成员"],
        },
        "read_only_table_types": ["team_overview"],
        "defaults": {
            "case": {"案件状态": "未结"},
            "bidding": {"标书领取状态": "未领取"},
        },
        "dedupe_fields": {
            "case": "case_no",
            "contracts": "contract_id",
            "bidding": "bid_id",
        },
        "append_fields": {
            "case": {
                "progress": {
                    "append_mode": True,
                    "date_prefix": True,
                    "target_fields": ["进展"],
                }
            }
        },
        "close_profiles": {
            "case": {
                "default": {
                    "title": "案件结案",
                    "status_field": "案件状态",
                    "target_status": "已结案",
                    "confirm_text": "确认结案",
                    "cancel_text": "暂不结案",
                    "consequences": ["案件将从在办视角移出", "后续更新需通过结案后流程处理"],
                    "remove_from_open_list": True,
                    "reminder_policy": "close_all",
                },
                "enforcement_end": {
                    "title": "执行终本",
                    "status_field": "案件状态",
                    "target_status": "执行终本",
                    "confirm_text": "确认终本",
                    "cancel_text": "暂不终本",
                    "consequences": ["后续可恢复执行", "数据保留在未结列表或终本分类"],
                    "remove_from_open_list": False,
                    "reminder_policy": "preserve_seizure",
                }
            },
            "contracts": {
                "default": {
                    "title": "合同归档",
                    "status_field": "合同状态",
                    "target_status": "已归档",
                    "confirm_text": "确认归档",
                    "cancel_text": "暂不归档",
                    "consequences": ["合同将进入归档状态", "归档后默认不再进入日常跟进列表"],
                }
            },
            "bidding": {
                "default": {
                    "title": "投标关闭",
                    "status_field": "状态",
                    "target_status": "已关闭",
                    "confirm_text": "确认关闭",
                    "cancel_text": "暂不关闭",
                    "consequences": ["项目将标记为关闭", "关闭后默认不在进行中列表展示"],
                }
            },
        },
        "delete_profiles": {
            "default": {
                "title": "删除确认",
                "subtitle": "该操作不可撤销，请再次确认。",
                "confirm_text": "确认删除",
                "cancel_text": "取消",
                "confirm_type": "danger",
                "warnings": ["该操作将永久删除记录"],
                "suggestion": "如仅需结束流程，建议优先使用关闭/结案。",
            }
        },
        "close_intent_mapping": {
            "case": {
                "default": ["结案", "判决生效", "撤诉", "调解结案"],
                "enforcement_end": ["执行终本", "执行不了了", "终本", "终结本次执行"],
            }
        },
    }

    def __init__(
        self,
        *,
        data_writer: DataWriter,
        linker: Any,
        smart_engine: ActionSmartEngine | None = None,
    ) -> None:
        self._data_writer = data_writer
        self._linker = linker
        self._smart = smart_engine or ActionSmartEngine()
        self._cfg = self._load_action_cfg()

    def validate_write_allowed(self, table_name: str | None) -> str | None:
        return self._deny_write_reason(self.resolve_table_type(table_name))

    async def execute_create(
        self,
        *,
        table_id: str | None,
        table_name: str | None,
        fields: dict[str, Any],
        idempotency_key: str | None,
        app_token: str | None = None,
    ) -> ActionExecutionOutcome:
        if app_token and table_id:
            try:
                validate_locator_triplet(app_token=app_token, table_id=table_id)
            except ValueError as exc:
                return ActionExecutionOutcome(False, str(exc), "写入参数缺失，请重试。", {})

        table_type = self.resolve_table_type(table_name)
        denied_text = self._deny_write_reason(table_type)
        if denied_text:
            return ActionExecutionOutcome(False, "写入受限", denied_text, {})

        effective_fields = self.apply_create_defaults(table_name, fields)
        inferred = self._smart.infer_create_fields(table_type, effective_fields)
        for key, value in inferred.items():
            effective_fields.setdefault(key, value)

        write_result = await self._data_writer.create(
            table_id,
            effective_fields,
            idempotency_key=idempotency_key,
        )
        if not write_result.success:
            return ActionExecutionOutcome(
                False,
                write_result.error or "创建失败",
                "创建记录失败，请稍后重试。",
                {},
            )

        record_id = write_result.record_id or ""
        record_url = write_result.record_url or ""
        lines = ["OK 创建成功！", "", *[f"• {k}：{v}" for k, v in effective_fields.items()], ""]
        if record_url:
            lines.append(f"查看详情：{record_url}")
        reply_text = "\n".join(lines).strip()

        link_sync = await self._linker.sync_after_create(
            parent_table_id=table_id,
            parent_table_name=table_name,
            parent_fields=effective_fields,
        )
        link_summary = self._linker.summarize(link_sync)
        repair_payload = self._linker.build_repair_pending(link_sync)
        pending_action = None
        if repair_payload:
            repair_action = str(repair_payload.get("repair_action") or "repair_child_create").strip()
            pending_action = {"action": repair_action, "payload": repair_payload}
            reply_text += "\n\n子表写入失败，请补充或修正后继续。例如：金额是1000，状态是待支付。"
        else:
            reminder_items = build_auto_reminder_items(str(table_name or ""), effective_fields)
            if reminder_items:
                pending_action = {
                    "action": "create_reminder",
                    "payload": {
                        "source_action": "create_record",
                        "table_name": str(table_name or ""),
                        "record_id": record_id,
                        "reminders": reminder_items,
                    },
                }
                reply_text += "\n\n检测到日期字段，可为您自动创建提醒。请点击确认继续。"
        if link_summary:
            reply_text += f"\n\n{link_summary}"

        data = {
            "clear_pending_action": False if pending_action else True,
            "pending_action": pending_action,
            "record_id": record_id,
            "fields": effective_fields,
            "record_url": record_url,
            "table_id": table_id,
            "table_name": table_name,
            "link_sync": link_sync,
            "auto_reminders": pending_action.get("payload", {}).get("reminders", []) if pending_action else [],
            "inferred_fields": inferred,
        }
        return ActionExecutionOutcome(True, "创建成功", reply_text, data)

    async def execute_update(
        self,
        *,
        action_name: str,
        table_id: str | None,
        table_name: str | None,
        record_id: str,
        fields: dict[str, Any],
        source_fields: dict[str, Any],
        idempotency_key: str | None,
        append_date: str | None = None,
        close_semantic: str = "default",
        app_token: str | None = None,
    ) -> ActionExecutionOutcome:
        if app_token and table_id:
            try:
                validate_locator_triplet(
                    app_token=app_token, table_id=table_id,
                    record_id=record_id, require_record_id=True,
                )
            except ValueError as exc:
                return ActionExecutionOutcome(False, str(exc), "写入参数缺失，请重试。", {})

        table_type = self.resolve_table_type(table_name)
        denied_text = self._deny_write_reason(table_type)
        if denied_text:
            return ActionExecutionOutcome(False, "写入受限", denied_text, {})

        effective_fields = self.apply_update_rules(
            table_name,
            fields,
            source_fields,
            append_date=append_date,
        )
        write_result = await self._data_writer.update(
            table_id,
            record_id,
            effective_fields,
            idempotency_key=idempotency_key,
        )
        if not write_result.success:
            return ActionExecutionOutcome(
                False,
                write_result.error or "更新失败",
                f"更新失败：{write_result.error or '未知错误'}\n请回复“确认”重试，或回复“取消”终止。",
                {},
            )

        close_profile = self._resolve_close_profile(table_name=table_name, semantic=close_semantic)
        close_title = str(close_profile.get("title") or "关闭").strip() or "关闭"
        opener = f"OK {close_title}成功！" if action_name == "close_record" else "OK 更新成功！"
        record_url = write_result.record_url or ""
        field_list = "\n".join([f"  • {k}: {v}" for k, v in effective_fields.items()])
        reply_text = f"{opener}\n\n已更新字段：\n{field_list}\n\n查看详情：{record_url}"

        link_sync = await self._linker.sync_after_update(
            parent_table_id=table_id,
            parent_table_name=table_name,
            updated_fields=effective_fields,
            source_fields=source_fields,
        )
        link_summary = self._linker.summarize(link_sync)
        repair_payload = self._linker.build_repair_pending(link_sync)
        next_pending_action = None
        if repair_payload:
            repair_action = str(repair_payload.get("repair_action") or "repair_child_create").strip()
            next_pending_action = {"action": repair_action, "payload": repair_payload}
            reply_text += "\n\n子表同步失败，请补充或修正后继续。例如：金额是1000，状态是待支付。"
        else:
            if action_name == "close_record":
                reminder_policy = str(close_profile.get("reminder_policy") or "close_all").strip().lower() or "close_all"
                if reminder_policy == "preserve_seizure":
                    reminder_items = build_auto_reminder_items(
                        str(table_name or ""),
                        {"查封到期日": source_fields.get("查封到期日")},
                    )
                else:
                    reminder_items = []
            else:
                reminder_items = build_auto_reminder_items(str(table_name or ""), effective_fields)
            if reminder_items:
                next_pending_action = {
                    "action": "create_reminder",
                    "payload": {
                        "source_action": "update_record",
                        "table_name": str(table_name or ""),
                        "record_id": record_id,
                        "reminders": reminder_items,
                    },
                }
                reply_text += "\n\n检测到日期字段，可为您自动创建提醒。请点击确认继续。"
        if link_summary:
            reply_text += f"\n\n{link_summary}"

        data = {
            "clear_pending_action": False if next_pending_action else True,
            "pending_action": next_pending_action,
            "record_id": record_id,
            "updated_fields": effective_fields,
            "record_url": record_url,
            "table_id": table_id,
            "table_name": table_name,
            "source_fields": source_fields,
            "link_sync": link_sync,
            "auto_reminders": next_pending_action.get("payload", {}).get("reminders", []) if next_pending_action else [],
            "close_semantic": close_semantic if action_name == "close_record" else "",
            "close_profile": close_semantic if action_name == "close_record" else "",
            "close_remove_from_open_list": bool(close_profile.get("remove_from_open_list", True)) if action_name == "close_record" else False,
        }
        message = f"{close_title}成功" if action_name == "close_record" else "更新成功"
        return ActionExecutionOutcome(True, message, reply_text, data)

    async def execute_delete(
        self,
        *,
        table_id: str | None,
        table_name: str | None,
        record_id: str,
        case_no: str,
        idempotency_key: str | None,
        app_token: str | None = None,
    ) -> ActionExecutionOutcome:
        if app_token and table_id:
            try:
                validate_locator_triplet(
                    app_token=app_token, table_id=table_id,
                    record_id=record_id, require_record_id=True,
                )
            except ValueError as exc:
                return ActionExecutionOutcome(False, str(exc), "写入参数缺失，请重试。", {})

        table_type = self.resolve_table_type(table_name)
        denied_text = self._deny_write_reason(table_type)
        if denied_text:
            return ActionExecutionOutcome(False, "写入受限", denied_text, {})

        write_result = await self._data_writer.delete(
            table_id,
            str(record_id),
            idempotency_key=idempotency_key,
        )
        if not write_result.success:
            error = write_result.error or "未知错误"
            return ActionExecutionOutcome(False, f"删除失败: {error}", f"删除失败：{error}", {})

        link_sync = await self._linker.sync_after_delete(
            parent_table_id=table_id,
            parent_table_name=table_name,
            parent_fields={"案号": case_no},
        )
        link_summary = self._linker.summarize(link_sync)
        reply_text = f"OK 已删除\n案件：{case_no}"
        if link_summary:
            reply_text += f"\n\n{link_summary}"
        data = {
            "record_id": record_id,
            "case_no": case_no,
            "table_id": table_id,
            "record_url": write_result.record_url or "",
            "link_sync": link_sync,
            "clear_pending_action": True,
        }
        return ActionExecutionOutcome(True, "删除成功", reply_text, data)

    def apply_create_defaults(self, table_name: str | None, fields: dict[str, Any]) -> dict[str, Any]:
        output = dict(fields)
        table_type = self.resolve_table_type(table_name)
        defaults_raw = self._cfg.get("defaults") if isinstance(self._cfg.get("defaults"), Mapping) else {}
        defaults = defaults_raw.get(table_type) if isinstance(defaults_raw, Mapping) else None
        if isinstance(defaults, Mapping):
            for key, value in defaults.items():
                name = str(key).strip()
                if not name:
                    continue
                if name not in output or str(output.get(name) or "").strip() == "":
                    output[name] = value
        return output

    def apply_update_rules(
        self,
        table_name: str | None,
        fields: dict[str, Any],
        source_fields: dict[str, Any],
        *,
        append_date: str | None = None,
    ) -> dict[str, Any]:
        output = dict(fields)
        table_type = self.resolve_table_type(table_name)
        append_cfg_all = self._cfg.get("append_fields") if isinstance(self._cfg.get("append_fields"), Mapping) else {}
        append_cfg = append_cfg_all.get(table_type) if isinstance(append_cfg_all, Mapping) else None
        if not isinstance(append_cfg, Mapping):
            return output

        for _, rule_raw in append_cfg.items():
            rule = rule_raw if isinstance(rule_raw, Mapping) else {}
            if not bool(rule.get("append_mode", False)):
                continue
            targets_raw = rule.get("target_fields")
            targets = [str(item).strip() for item in targets_raw if str(item).strip()] if isinstance(targets_raw, list) else []
            if not targets:
                continue
            use_date_prefix = bool(rule.get("date_prefix", True))
            for field_name in targets:
                if field_name not in output:
                    continue
                new_value = str(output.get(field_name) or "").strip()
                if not new_value:
                    continue
                old_value = str(source_fields.get(field_name) or "").strip()
                append_text = new_value
                if use_date_prefix:
                    append_day = self._normalize_append_date(append_date)
                    append_text = f"[{append_day}] {append_text}"
                if old_value:
                    output[field_name] = f"{old_value}\n{append_text}"
                else:
                    output[field_name] = append_text
        return output

    def build_update_preview(
        self,
        *,
        table_name: str | None,
        fields: dict[str, Any],
        source_fields: dict[str, Any],
        append_date: str | None,
    ) -> tuple[dict[str, Any], list[dict[str, str]], str]:
        normalized_append_date = self._normalize_append_date(append_date)
        effective_fields = self.apply_update_rules(
            table_name,
            fields,
            source_fields,
            append_date=normalized_append_date,
        )
        diff_items = self._build_diff_with_mode(
            source_fields=source_fields,
            raw_fields=fields,
            effective_fields=effective_fields,
        )
        return effective_fields, diff_items, normalized_append_date

    def build_pending_update_action_data(
        self,
        *,
        action_name: str,
        record_id: str,
        fields: dict[str, Any],
        preview_fields: dict[str, Any],
        source_fields: dict[str, Any],
        diff_items: list[dict[str, str]],
        table_id: str | None,
        table_name: str | None,
        idempotency_key: str | None,
        created_at: float,
        ttl_seconds: int,
        append_date: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "record_id": record_id,
            "fields": fields,
            "preview_fields": preview_fields,
            "source_fields": source_fields,
            "diff": diff_items,
            "table_id": table_id,
            "table_name": table_name,
            "created_at": created_at,
            "pending_ttl_seconds": ttl_seconds,
            "append_date": append_date,
        }
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        return {
            "pending_action": {
                "action": action_name,
                "payload": payload,
                "ttl_seconds": ttl_seconds,
            },
            "record_id": record_id,
            "table_id": table_id,
            "table_name": table_name,
            "updated_fields": preview_fields,
            "source_fields": source_fields,
        }

    def build_pending_close_action_data(
        self,
        *,
        record_id: str,
        fields: dict[str, Any],
        source_fields: dict[str, Any],
        diff_items: list[dict[str, str]],
        table_id: str | None,
        table_name: str | None,
        idempotency_key: str | None,
        created_at: float,
        ttl_seconds: int,
        append_date: str,
        close_semantic: str = "",
        intent_text: str = "",
    ) -> dict[str, Any]:
        semantic = str(close_semantic or "").strip() or self.resolve_close_semantic(intent_text, table_name, default="default")
        data = self.build_pending_update_action_data(
            action_name="close_record",
            record_id=record_id,
            fields=fields,
            preview_fields=fields,
            source_fields=source_fields,
            diff_items=diff_items,
            table_id=table_id,
            table_name=table_name,
            idempotency_key=idempotency_key,
            created_at=created_at,
            ttl_seconds=ttl_seconds,
            append_date=append_date,
        )
        payload = data.get("pending_action", {}).get("payload", {})
        if isinstance(payload, dict):
            profile = self._resolve_close_profile(table_name=table_name, semantic=semantic)
            status_field = str(profile.get("status_field") or "案件状态").strip() or "案件状态"
            close_to = str(profile.get("target_status") or fields.get(status_field) or "已结案").strip() or "已结案"
            close_from = str(source_fields.get(status_field) or "").strip() or "(空)"
            payload.update(
                {
                    "close_semantic": semantic,
                    "close_profile": semantic,
                    "close_title": str(profile.get("title") or "关闭").strip(),
                    "close_status_field": status_field,
                    "close_status_from": close_from,
                    "close_status_value": close_to,
                    "close_consequences": profile.get("consequences") if isinstance(profile.get("consequences"), list) else [],
                    "close_remove_from_open_list": bool(profile.get("remove_from_open_list", True)),
                    "close_reminder_policy": str(profile.get("reminder_policy") or "close_all").strip() or "close_all",
                    "confirm_text": str(profile.get("confirm_text") or "确认执行").strip(),
                    "cancel_text": str(profile.get("cancel_text") or "取消").strip(),
                    "confirm_type": "primary",
                }
            )
            close_field_missing = status_field not in payload.get("fields", {}) if isinstance(payload.get("fields"), Mapping) else True
            if close_field_missing:
                payload.setdefault("fields", {})
                if isinstance(payload.get("fields"), dict):
                    payload["fields"][status_field] = close_to
        return data

    def build_pending_delete_action_data(
        self,
        *,
        record_id: str,
        case_no: str,
        table_id: str | None,
        table_name: str | None,
        idempotency_key: str | None,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        profile = self._resolve_delete_profile(table_name)
        payload: dict[str, Any] = {
            "record_id": record_id,
            "case_no": case_no,
            "table_id": table_id,
            "table_name": table_name,
            "delete_title": str(profile.get("title") or "删除确认").strip(),
            "delete_subtitle": str(profile.get("subtitle") or "该操作不可撤销，请再次确认。").strip(),
            "confirm_text": str(profile.get("confirm_text") or "确认删除").strip(),
            "cancel_text": str(profile.get("cancel_text") or "取消").strip(),
            "confirm_type": str(profile.get("confirm_type") or "danger").strip() or "danger",
            "warnings": profile.get("warnings") if isinstance(profile.get("warnings"), list) else [],
            "suggestion": str(profile.get("suggestion") or "").strip(),
        }
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        return {
            "pending_delete": payload,
            "pending_action": {
                "action": "delete_record",
                "payload": payload,
                "ttl_seconds": ttl_seconds,
            },
        }

    def resolve_close_semantic(self, text: str, table_name: str | None, *, default: str = "default") -> str:
        table_type = self.resolve_table_type(table_name)
        mapping_raw = self._cfg.get("close_intent_mapping") if isinstance(self._cfg.get("close_intent_mapping"), Mapping) else {}
        table_mapping_raw = mapping_raw.get(table_type) if isinstance(mapping_raw, Mapping) else {}
        table_mapping = table_mapping_raw if isinstance(table_mapping_raw, Mapping) else {}
        source = str(text or "").strip()
        if not source:
            return default
        for semantic, keywords_raw in table_mapping.items():
            keywords = [str(item).strip() for item in keywords_raw if str(item).strip()] if isinstance(keywords_raw, list) else []
            for kw in keywords:
                if kw in source:
                    return str(semantic)
        return default

    def _build_diff_with_mode(
        self,
        *,
        source_fields: Mapping[str, Any],
        raw_fields: Mapping[str, Any],
        effective_fields: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        diff_items: list[dict[str, str]] = []
        for key, new_value in effective_fields.items():
            field_name = str(key)
            old_text = str(source_fields.get(field_name) or "").strip()
            new_text = str(new_value or "").strip()
            if old_text == new_text:
                continue
            raw_new = str(raw_fields.get(field_name) or "").strip()
            is_append = bool(old_text) and bool(raw_new) and new_text.startswith(old_text + "\n")
            item: dict[str, str] = {
                "field": field_name,
                "old": old_text,
                "new": new_text,
            }
            if is_append:
                item["mode"] = "append"
                item["delta"] = new_text[len(old_text) + 1 :]
            diff_items.append(item)
        return diff_items

    def resolve_duplicate_field_name(self, table_name: str | None) -> str | None:
        table_type = self.resolve_table_type(table_name)
        dedupe_raw = self._cfg.get("dedupe_fields") if isinstance(self._cfg.get("dedupe_fields"), Mapping) else {}
        canonical = str(dedupe_raw.get(table_type) or "").strip() if isinstance(dedupe_raw, Mapping) else ""
        if not canonical:
            return None
        mapped = self._resolve_canonical_to_source_field(canonical)
        return mapped or canonical

    def resolve_table_type(self, table_name: str | None) -> str:
        text = str(table_name or "").strip()
        aliases_raw = self._cfg.get("table_type_alias") if isinstance(self._cfg.get("table_type_alias"), Mapping) else {}
        aliases = aliases_raw if isinstance(aliases_raw, Mapping) else {}
        for table_type, tokens_raw in aliases.items():
            tokens = [str(item).strip() for item in tokens_raw if str(item).strip()] if isinstance(tokens_raw, list) else []
            if any(token and token in text for token in tokens):
                return str(table_type)
        if "合同" in text:
            return "contracts"
        if any(token in text for token in ("招投标", "投标", "台账")):
            return "bidding"
        if any(token in text for token in ("团队", "成员", "总览")):
            return "team_overview"
        return "case"

    def _deny_write_reason(self, table_type: str) -> str | None:
        read_only_raw = self._cfg.get("read_only_table_types")
        read_only = [str(item).strip() for item in read_only_raw if str(item).strip()] if isinstance(read_only_raw, list) else []
        if table_type in read_only:
            return "该表为只读视图，禁止执行新增、修改、关闭或删除操作。"
        return None

    def _resolve_close_profile(self, *, table_name: str | None, semantic: str) -> dict[str, Any]:
        table_type = self.resolve_table_type(table_name)
        close_profiles_raw = self._cfg.get("close_profiles") if isinstance(self._cfg.get("close_profiles"), Mapping) else {}
        close_profiles = close_profiles_raw if isinstance(close_profiles_raw, Mapping) else {}
        table_profiles_raw = close_profiles.get(table_type)
        table_profiles = table_profiles_raw if isinstance(table_profiles_raw, Mapping) else {}
        semantic_key = str(semantic or "default").strip() or "default"
        profile_raw = table_profiles.get(semantic_key) or table_profiles.get("default")
        profile = profile_raw if isinstance(profile_raw, Mapping) else {}
        return dict(profile)

    def _resolve_delete_profile(self, table_name: str | None) -> dict[str, Any]:
        table_type = self.resolve_table_type(table_name)
        delete_profiles_raw = self._cfg.get("delete_profiles") if isinstance(self._cfg.get("delete_profiles"), Mapping) else {}
        delete_profiles = delete_profiles_raw if isinstance(delete_profiles_raw, Mapping) else {}
        profile_raw = delete_profiles.get(table_type) or delete_profiles.get("default")
        profile = profile_raw if isinstance(profile_raw, Mapping) else {}
        return dict(profile)

    def _normalize_append_date(self, append_date: str | None) -> str:
        text = str(append_date or "").strip()
        if text:
            return text
        return date.today().isoformat()

    def _resolve_canonical_to_source_field(self, canonical_key: str) -> str | None:
        query_cfg = self._load_query_list_cfg()
        raw_mapping = query_cfg.get("field_mapping")
        mapping_raw: dict[str, Any] = dict(raw_mapping) if isinstance(raw_mapping, Mapping) else {}
        for _, domain_mapping_raw in mapping_raw.items():
            domain_mapping = domain_mapping_raw if isinstance(domain_mapping_raw, Mapping) else {}
            for source_name, mapped_key in domain_mapping.items():
                if str(mapped_key).strip() == canonical_key:
                    source_text = str(source_name).strip()
                    if source_text:
                        return source_text
        return None

    def _load_query_list_cfg(self) -> dict[str, Any]:
        render = self._load_render_templates()
        query_cfg = render.get("query_list_v2")
        return query_cfg if isinstance(query_cfg, dict) else {}

    def _load_action_cfg(self) -> dict[str, Any]:
        render = self._load_render_templates()
        action_cfg = render.get("action_execution")
        if not isinstance(action_cfg, dict):
            action_cfg = {}
        merged = self._deep_merge(dict(self._BUILTIN_CONFIG), action_cfg)
        return merged

    def _load_render_templates(self) -> dict[str, Any]:
        path = self._config_path()
        if not path.exists():
            return {}
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        render = payload.get("render_templates")
        if not isinstance(render, dict):
            return {}
        return render

    def _config_path(self) -> Path:
        custom_path = os.getenv("CARD_TEMPLATE_CONFIG_PATH", "").strip()
        if custom_path:
            return Path(custom_path)
        return Path(__file__).resolve().parents[3] / "config" / "card_templates.yaml"

    def _deep_merge(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
