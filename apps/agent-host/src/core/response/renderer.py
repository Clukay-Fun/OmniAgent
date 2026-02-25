from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, cast

import yaml

from src.core.errors import get_user_message_by_code
from src.core.response.models import Block, CardTemplateSpec, RenderedResponse


DEFAULT_TEMPLATES: Dict[str, str] = {
    "success": "已完成 {skill_name}",
    "failure": "处理失败：{skill_name}",
}


class ResponseRenderer:
    def __init__(
        self,
        templates: Mapping[str, str] | None = None,
        templates_path: str | Path | None = None,
        assistant_name: str = "assistant",
        query_card_v2_enabled: bool = False,
    ) -> None:
        if templates is not None:
            self._templates = dict(templates)
        else:
            self._templates = self._load_templates(templates_path)
        self._assistant_name = assistant_name
        self._query_card_v2_enabled = bool(query_card_v2_enabled)

    def render(self, skill_result: Any) -> RenderedResponse:
        payload = self._to_mapping(skill_result)
        success = bool(payload.get("success", False))
        skill_name = str(payload.get("skill_name") or "unknown")

        reply_text = payload.get("reply_text")
        message = payload.get("message")
        chosen_text = reply_text if self._is_non_blank(reply_text) else message
        error_code = self._extract_error_code(payload)
        if not success and error_code:
            chosen_text = get_user_message_by_code(error_code, fallback=str(chosen_text or ""))

        template_key = "success" if success else "failure"
        template_text = self._templates.get(template_key) or DEFAULT_TEMPLATES[template_key]
        rendered_template = template_text.format(skill_name=skill_name)
        text_fallback = chosen_text if self._is_non_blank(chosen_text) else rendered_template

        blocks = [Block(type="paragraph", content={"text": str(text_fallback)})]

        data = payload.get("data")
        if isinstance(data, Mapping) and data and skill_name not in ["QuerySkill", "ChitchatSkill"]:
            items = self._build_safe_kv_items(data)
            if items:
                blocks.append(Block(type="kv_list", content={"items": items}))

        card_template = self._select_card_template(
            skill_name=skill_name,
            success=success,
            text_fallback=str(text_fallback),
            data=data if isinstance(data, Mapping) else {},
            error_code=error_code,
        )

        return RenderedResponse(
            text_fallback=str(text_fallback),
            blocks=blocks,
            meta={"assistant_name": self._assistant_name, "skill_name": skill_name},
            card_template=card_template,
        )

    def _select_card_template(
        self,
        skill_name: str,
        success: bool,
        text_fallback: str,
        data: Mapping[str, Any],
        error_code: str = "",
    ) -> CardTemplateSpec | None:
        if not success:
            error_class = self._classify_error(text_fallback)
            return CardTemplateSpec(
                template_id="error.notice",
                version="v1",
                params={
                    "title": "处理失败",
                    "message": text_fallback,
                    "skill_name": skill_name,
                    "error_class": error_class,
                    "error_code": error_code,
                },
            )

        if skill_name == "DeleteSkill":
            pending_delete = data.get("pending_delete")
            if isinstance(pending_delete, Mapping):
                table_type = str(data.get("table_type") or pending_delete.get("table_type") or "")
                return CardTemplateSpec(
                    template_id="delete.confirm",
                    version="v1",
                    params={
                        "title": str(pending_delete.get("delete_title") or ""),
                        "subtitle": str(pending_delete.get("delete_subtitle") or ""),
                        "summary": self._build_delete_summary(pending_delete, data),
                        "actions": self._build_delete_actions(pending_delete),
                        "table_type": table_type,
                        "record_id": str(pending_delete.get("record_id") or ""),
                        "warnings": pending_delete.get("warnings") if isinstance(pending_delete.get("warnings"), list) else [],
                        "suggestion": str(pending_delete.get("suggestion") or ""),
                        "confirm_text": str(pending_delete.get("confirm_text") or ""),
                        "cancel_text": str(pending_delete.get("cancel_text") or ""),
                        "confirm_type": str(pending_delete.get("confirm_type") or ""),
                    },
                )

            if self._is_delete_cancelled(text_fallback):
                return CardTemplateSpec(
                    template_id="delete.cancelled",
                    version="v1",
                    params={
                        "title": "删除已取消",
                        "message": text_fallback,
                    },
                )

            return CardTemplateSpec(
                template_id="delete.success",
                version="v1",
                params={
                    "title": "删除成功",
                    "message": text_fallback,
                },
            )

        pending_action = data.get("pending_action")
        if isinstance(pending_action, Mapping) and skill_name != "QuerySkill":
            action_name = str(pending_action.get("action") or "")
            pending_payload_raw = pending_action.get("payload")
            pending_payload = pending_payload_raw if isinstance(pending_payload_raw, Mapping) else {}
            table_name = str(data.get("table_name") or pending_payload.get("table_name") or "")
            table_type = str(data.get("table_type") or pending_payload.get("table_type") or "")
            record_id = str(data.get("record_id") or pending_payload.get("record_id") or "")

            if action_name == "update_collect_fields":
                return CardTemplateSpec(
                    template_id="update.guide",
                    version="v1",
                    params={
                        "title": "修改案件",
                        "record_id": record_id,
                        "table_name": table_name,
                        "table_type": table_type,
                        "record_case_no": str(data.get("record_case_no") or pending_payload.get("record_case_no") or ""),
                        "record_identity": str(data.get("record_identity") or pending_payload.get("record_identity") or ""),
                        "cancel_action": {
                            "callback_action": "update_collect_fields_cancel",
                            "table_type": table_type,
                            "record_id": record_id,
                            "extra_data": {},
                        },
                    },
                )

            return CardTemplateSpec(
                template_id="action.confirm",
                version="v1",
                params={
                    "title": "请确认操作",
                    "message": text_fallback,
                    "action": action_name,
                    "payload": dict(cast(Mapping[str, Any], pending_payload)),
                    "table_name": table_name,
                    "table_type": table_type,
                    "record_id": record_id,
                    "actions": self._build_generic_actions(action_name),
                    "confirm_text": str(pending_payload.get("confirm_text") or ""),
                    "cancel_text": str(pending_payload.get("cancel_text") or ""),
                    "confirm_type": str(pending_payload.get("confirm_type") or ""),
                },
            )

        if skill_name == "QuerySkill":
            records = data.get("records")
            if isinstance(records, list):
                query_text = text_fallback
                domain = self._detect_query_domain(data)
                style = self._select_query_style(
                    domain=domain,
                    query_text=query_text,
                    data=data,
                    record_count=len(records),
                )
                style_variant = self._select_query_style_variant(
                    domain=domain,
                    style=style,
                    query_text=query_text,
                    data=data,
                    record_count=len(records),
                )
                title = self._query_title_by_domain(domain)
                if self._query_card_v2_enabled:
                    actions = self._build_query_list_actions(data)
                    query_meta_raw = data.get("query_meta")
                    query_meta = query_meta_raw if isinstance(query_meta_raw, Mapping) else {}
                    return CardTemplateSpec(
                        template_id="query.list",
                        version="v2",
                        params={
                            "title": title,
                            "total": int(data.get("total") or len(records)),
                            "records": records,
                            "actions": actions,
                            "style": style,
                            "style_variant": style_variant,
                            "domain": domain,
                            "table_name": str(query_meta.get("table_name") or data.get("table_name") or ""),
                            "table_id": str(query_meta.get("table_id") or data.get("table_id") or ""),
                        },
                    )
                if len(records) > 1:
                    return CardTemplateSpec(
                        template_id="query.list",
                        version="v1",
                        params={
                            "title": "查询结果",
                            "total": int(data.get("total") or len(records)),
                            "records": records,
                        },
                    )
            if isinstance(records, list) and len(records) == 1 and isinstance(records[0], Mapping):
                return CardTemplateSpec(
                    template_id="query.detail",
                    version="v1",
                    params={
                        "title": "记录详情",
                        "record": dict(records[0]),
                    },
                )

        if skill_name == "CreateSkill":
            fields_raw = data.get("fields")
            fields = fields_raw if isinstance(fields_raw, Mapping) else {}
            fields_text = {str(key): value for key, value in fields.items()}
            table_name = str(data.get("table_name") or "")
            return CardTemplateSpec(
                template_id="create.success",
                version="v1",
                params={
                    "title": "创建成功",
                        "record": {
                            "record_id": str(data.get("record_id") or ""),
                            "record_url": str(data.get("record_url") or ""),
                            "fields_text": fields_text,
                        },
                        "record_url": str(data.get("record_url") or ""),
                        "table_name": table_name,
                    },
                )

        if skill_name == "UpdateSkill":
            changes = self._build_update_changes(data)
            return CardTemplateSpec(
                template_id="update.success",
                version="v1",
                params={
                    "title": "更新成功",
                    "changes": changes,
                    "record_url": str(data.get("record_url") or ""),
                    "record_id": str(data.get("record_id") or ""),
                    "progress_append": self._extract_progress_append(data),
                },
            )

        if skill_name == "ReminderSkill":
            return CardTemplateSpec(
                template_id="todo.reminder",
                version="v1",
                params={
                    "title": "提醒结果",
                    "message": text_fallback,
                    "content": str(data.get("content") or ""),
                    "remind_time": str(data.get("remind_time") or ""),
                },
            )

        return None

    def _classify_error(self, message: str) -> str:
        normalized = str(message or "").lower()
        if any(token in normalized for token in ["权限", "无权", "forbidden", "permission denied", "access denied"]):
            return "permission_denied"
        if any(token in normalized for token in ["未找到", "不存在", "没有找到", "not found", "recordidnotfound", "notfound"]):
            return "record_not_found"
        if any(token in normalized for token in ["缺少", "必填", "参数", "未提供", "无法解析更新字段"]):
            return "missing_params"
        return "general"

    def _extract_error_code(self, payload: Mapping[str, Any]) -> str:
        top_level = str(payload.get("error_code") or "").strip()
        if top_level:
            return top_level

        data_raw = payload.get("data")
        data = data_raw if isinstance(data_raw, Mapping) else {}
        from_data = str(data.get("error_code") or "").strip()
        if from_data:
            return from_data

        return ""

    def _build_update_changes(self, data: Mapping[str, Any]) -> list[dict[str, str]]:
        updated_fields_raw = data.get("updated_fields")
        source_fields_raw = data.get("source_fields")
        updated_fields = updated_fields_raw if isinstance(updated_fields_raw, Mapping) else {}
        source_fields = source_fields_raw if isinstance(source_fields_raw, Mapping) else {}

        changes: list[dict[str, str]] = []
        for key, new_value in updated_fields.items():
            old_value = source_fields.get(key, "")
            changes.append(
                {
                    "field": str(key),
                    "old": str(old_value),
                    "new": str(new_value),
                }
            )
        return changes

    def _build_delete_summary(self, pending_delete: Mapping[str, Any], data: Mapping[str, Any]) -> dict[str, str]:
        records_raw = data.get("records")
        records = records_raw if isinstance(records_raw, list) else []
        first_record = records[0] if records and isinstance(records[0], Mapping) else {}
        fields_text = first_record.get("fields_text") if isinstance(first_record, Mapping) else {}
        if not isinstance(fields_text, Mapping):
            fields_text = first_record.get("fields") if isinstance(first_record, Mapping) else {}
        if not isinstance(fields_text, Mapping):
            fields_text = {}

        case_no = str(
            pending_delete.get("case_no")
            or pending_delete.get("record_summary")
            or fields_text.get("案号")
            or ""
        ).strip()
        record_id = str(pending_delete.get("record_id") or "").strip()

        summary: dict[str, str] = {}
        if case_no:
            summary["案号"] = case_no
        if record_id:
            summary["记录 ID"] = record_id
        cause = str(fields_text.get("案由") or "").strip()
        if cause:
            summary["案由"] = cause
        return summary

    def _build_delete_actions(self, pending_delete: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            "record_id": str(pending_delete.get("record_id") or ""),
            "case_no": str(pending_delete.get("case_no") or pending_delete.get("record_summary") or ""),
            "table_id": str(pending_delete.get("table_id") or ""),
        }
        return {
            "confirm": {
                "callback_action": "delete_record_confirm",
                "intent": "confirm",
                "pending_delete": payload,
            },
            "cancel": {
                "callback_action": "delete_record_cancel",
                "intent": "cancel",
                "pending_delete": payload,
            },
        }

    def _is_delete_cancelled(self, text: str) -> bool:
        normalized = str(text or "").lower()
        return "取消" in normalized and "删除" in normalized

    def _build_generic_actions(self, action_name: str) -> dict[str, Any]:
        callback_prefix = {
            "create_record": "create_record",
            "update_record": "update_record",
            "close_record": "close_record",
            "delete_record": "delete_record",
        }.get(action_name, action_name or "pending_action")
        return {
            "confirm": {
                "callback_action": f"{callback_prefix}_confirm",
                "intent": "confirm",
            },
            "cancel": {
                "callback_action": f"{callback_prefix}_cancel",
                "intent": "cancel",
            },
        }

    def _build_query_list_actions(self, data: Mapping[str, Any]) -> dict[str, Any]:
        pending_action = data.get("pending_action") if isinstance(data.get("pending_action"), Mapping) else {}
        payload = pending_action.get("payload") if isinstance(pending_action, Mapping) else {}
        callbacks = payload.get("callbacks") if isinstance(payload, Mapping) else {}
        callback_map = callbacks if isinstance(callbacks, Mapping) else {}
        table_type = str(data.get("table_type") or self._detect_query_domain(data))

        def _pick(name: str, fallback_action: str) -> dict[str, Any]:
            raw = callback_map.get(name)
            picked = dict(raw) if isinstance(raw, Mapping) else {}
            picked.setdefault("callback_action", fallback_action)
            picked.setdefault("table_type", table_type)
            picked.setdefault("record_id", "")
            picked.setdefault("extra_data", {})
            return picked

        return {
            "next_page": _pick("query_list_next_page", "query_list_next_page"),
            "today_hearing": _pick("query_list_today_hearing", "query_list_today_hearing"),
            "week_hearing": _pick("query_list_week_hearing", "query_list_week_hearing"),
        }

    def _extract_progress_append(self, data: Mapping[str, Any]) -> str:
        updated_fields_raw = data.get("updated_fields")
        if not isinstance(updated_fields_raw, Mapping):
            return ""
        for key, value in updated_fields_raw.items():
            field_name = str(key)
            if any(token in field_name for token in ("进展", "备注", "跟进", "状态")):
                text = str(value or "").strip()
                if text:
                    return text
        return ""

    def _query_title_by_domain(self, domain: str) -> str:
        return {
            "case": "案件项目总库查询结果",
            "contracts": "合同管理表查询结果",
            "bidding": "招投标台账查询结果",
            "team_overview": "团队成员工作总览（只读）",
        }.get(domain, "查询结果")

    def _detect_query_domain(self, data: Mapping[str, Any]) -> str:
        query_meta_raw = data.get("query_meta")
        query_meta = query_meta_raw if isinstance(query_meta_raw, Mapping) else {}
        table_name = str(query_meta.get("table_name") or data.get("table_name") or "")
        combined = table_name.replace(" ", "")
        if "合同" in combined:
            return "contracts"
        if any(token in combined for token in ("招投标", "投标", "台账")):
            return "bidding"
        if any(token in combined for token in ("团队", "成员", "工作总览")):
            return "team_overview"
        return "case"

    def _select_query_style(self, domain: str, query_text: str, data: Mapping[str, Any], record_count: int) -> str:
        _ = query_text
        query_meta_raw = data.get("query_meta")
        query_meta = query_meta_raw if isinstance(query_meta_raw, Mapping) else {}

        style_hint = str(
            query_meta.get("style_hint")
            or query_meta.get("style")
            or data.get("style_hint")
            or ""
        ).strip().upper()
        if self._is_style_allowed_for_domain(domain, style_hint):
            return style_hint

        if record_count == 1:
            return self._default_detail_style(domain)
        return self._default_list_style(domain)

    def _select_query_style_variant(
        self,
        domain: str,
        style: str,
        query_text: str,
        data: Mapping[str, Any],
        record_count: int,
    ) -> str:
        _ = query_text
        _ = record_count
        query_meta_raw = data.get("query_meta")
        query_meta = query_meta_raw if isinstance(query_meta_raw, Mapping) else {}
        variant_hint = str(
            query_meta.get("style_variant")
            or query_meta.get("variant")
            or data.get("style_variant")
            or ""
        ).strip().upper()
        if self._is_style_allowed_for_domain(domain, variant_hint):
            return variant_hint
        return style

    def _default_detail_style(self, domain: str) -> str:
        return {
            "case": "T1",
            "contracts": "HT-T1",
            "bidding": "ZB-T1",
            "team_overview": "RW-T1",
        }.get(domain, "T1")

    def _default_list_style(self, domain: str) -> str:
        return {
            "case": "T2",
            "contracts": "HT-T2",
            "bidding": "ZB-T2",
            "team_overview": "RW-T2",
        }.get(domain, "T2")

    def _is_style_allowed_for_domain(self, domain: str, style: str) -> bool:
        normalized = str(style or "").strip().upper()
        if not normalized:
            return False
        if domain == "contracts":
            return normalized.startswith("HT-")
        if domain == "bidding":
            return normalized.startswith("ZB-")
        if domain == "team_overview":
            return normalized.startswith("RW-")
        return normalized.startswith("T")

    def _load_templates(self, templates_path: str | Path | None) -> Dict[str, str]:
        path = Path(templates_path) if templates_path else self._default_template_path()
        if not path.exists():
            return dict(DEFAULT_TEMPLATES)

        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return dict(DEFAULT_TEMPLATES)

        if not isinstance(parsed, Mapping):
            return dict(DEFAULT_TEMPLATES)

        merged = dict(DEFAULT_TEMPLATES)
        for key in ("success", "failure"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                merged[key] = value
        return merged

    def _default_template_path(self) -> Path:
        return Path(__file__).resolve().parents[3] / "config" / "responses.yaml"

    def _to_mapping(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "__dict__") and isinstance(value.__dict__, dict):
            return dict(value.__dict__)
        if hasattr(value, "dict") and callable(value.dict):
            maybe_mapping = value.dict()
            if isinstance(maybe_mapping, Mapping):
                return dict(cast(Mapping[str, Any], maybe_mapping))
        if hasattr(value, "model_dump") and callable(value.model_dump):
            maybe_mapping = value.model_dump()
            if isinstance(maybe_mapping, Mapping):
                return dict(cast(Mapping[str, Any], maybe_mapping))
        return {}

    def _is_non_blank(self, value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip())

    def _build_safe_kv_items(self, data: Mapping[str, Any]) -> list[dict[str, str]]:
        hidden_keys = {
            "total",
            "records",
            "raw",
            "schema",
            "query_meta",
            "pagination",
            "fields",
            "updated_fields",
            "source_fields",
            "pending_action",
            "pending_delete",
        }
        items: list[dict[str, str]] = []
        for raw_key, raw_value in data.items():
            key = str(raw_key)
            if key in hidden_keys:
                continue
            if isinstance(raw_value, (dict, list, tuple, set)):
                continue
            value = str(raw_value or "").strip()
            if not value:
                continue
            if len(value) > 200:
                value = value[:200].rstrip() + "..."
            items.append({"key": key, "value": value})
        return items
