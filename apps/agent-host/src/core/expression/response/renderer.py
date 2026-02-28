"""
描述: 响应渲染器
主要功能:
    - 针对不同技能的执行结果，组装输出文本和卡片参数
    - 各种查询场景与状态类的消息映射
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import re
from typing import Any, Dict, Mapping, cast

import yaml

from src.core.foundation.common.errors import get_user_message_by_code
from src.core.expression.response.models import Block, CardTemplateSpec, RenderedResponse


DEFAULT_TEMPLATES: Dict[str, str] = {
    "success": "已完成 {skill_name}",
    "failure": "处理失败：{skill_name}",
}


# region 响应渲染引擎
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

        data = payload.get("data")
        if skill_name == "QuerySkill" and success and isinstance(data, Mapping):
            text_fallback = self._build_query_text_fallback(
                data=data,
                default_text=str(text_fallback),
            )

        blocks = [Block(type="paragraph", content={"text": str(text_fallback)})]

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
                query_meta_raw = data.get("query_meta")
                query_meta = query_meta_raw if isinstance(query_meta_raw, Mapping) else {}
                query_text = str(query_meta.get("query_text") or text_fallback)
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
        actions: dict[str, Any] = {
            "confirm": {
                "callback_action": f"{callback_prefix}_confirm",
                "intent": "confirm",
            },
            "cancel": {
                "callback_action": f"{callback_prefix}_cancel",
                "intent": "cancel",
            },
        }
        if str(action_name or "").startswith("batch_"):
            actions["retry"] = {
                "callback_action": f"{callback_prefix}_retry",
                "intent": "retry",
            }
        return actions

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
        query_meta_raw = data.get("query_meta")
        query_meta = query_meta_raw if isinstance(query_meta_raw, Mapping) else {}
        tool = str(query_meta.get("tool") or "").strip().lower()
        normalized_query = self._normalize_query_text(str(query_text or query_meta.get("query_text") or ""))

        variant_hint = str(
            query_meta.get("style_variant")
            or query_meta.get("variant")
            or data.get("style_variant")
            or ""
        ).strip().upper()
        if self._is_style_allowed_for_domain(domain, variant_hint):
            return variant_hint

        if domain == "case":
            if record_count <= 1:
                if any(token in normalized_query for token in ("开庭", "截止", "到期", "管辖权", "举证", "查封", "反诉", "上诉")):
                    return "T3C"
                if any(token in normalized_query for token in ("进展", "时间线", "最新情况", "进度")):
                    return "T5B"
                if any(token in normalized_query for token in ("法官", "法院", "案号", "程序", "一审", "二审")):
                    return "T6"

            if tool == "data.bitable.search_date_range":
                if any(token in normalized_query for token in ("截止", "到期", "管辖权", "举证", "查封", "反诉", "上诉")):
                    return "T3B"
                return "T3A"

            if any(token in normalized_query for token in ("待办", "待做", "还没做")):
                return "T5A"
            if any(token in normalized_query for token in ("进展", "时间线", "最新情况", "进度")):
                return "T5B"
            if any(token in normalized_query for token in ("状态", "未结", "重要紧急", "紧急")):
                return "T5C"
            if any(token in normalized_query for token in ("联系人", "当事人", "委托人", "对方当事人")):
                return "T4B"
            if any(token in normalized_query for token in ("我的案件", "我的案子", "主办", "协办", "律师")):
                return "T4A"
            if any(token in normalized_query for token in ("法官", "法院", "案号", "程序", "一审", "二审")):
                return "T6"

        if domain == "contracts":
            if record_count > 1 and any(token in normalized_query for token in ("未付款", "未开票", "待盖章", "到期", "快到期")):
                return "HT-T3"

        if domain == "bidding":
            if record_count > 1 and any(token in normalized_query for token in ("中标", "结果", "中标率")):
                return "ZB-T4"
            if record_count > 1 and any(token in normalized_query for token in ("最近", "截标", "标书", "保证金", "时间线", "本周", "下周")):
                return "ZB-T3"

        if domain == "team_overview":
            if record_count > 1 and any(token in normalized_query for token in ("看板", "过期", "重要紧急", "待办")):
                return "RW-T3"
            if record_count > 1 and any(token in normalized_query for token in ("总览", "任务总览", "完成情况")):
                return "RW-T4"

        return style

    def _normalize_query_text(self, query_text: str) -> str:
        return re.sub(r"\s+", "", str(query_text or "")).lower()

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

    def _build_query_text_fallback(self, data: Mapping[str, Any], default_text: str) -> str:
        records_raw = data.get("records")
        records = records_raw if isinstance(records_raw, list) else []
        if not records:
            return default_text

        query_meta_raw = data.get("query_meta")
        query_meta = query_meta_raw if isinstance(query_meta_raw, Mapping) else {}
        query_text = str(query_meta.get("query_text") or default_text)
        domain = self._detect_query_domain(data)
        style = self._select_query_style(domain=domain, query_text=query_text, data=data, record_count=len(records))
        variant = self._select_query_style_variant(
            domain=domain,
            style=style,
            query_text=query_text,
            data=data,
            record_count=len(records),
        )
        active_style = variant or style
        total = int(data.get("total") or len(records))

        if domain == "case":
            if len(records) == 1:
                return self._render_case_detail_text(records[0], style=active_style)
            return self._render_case_list_text(records, total=total, style=active_style)

        if domain == "contracts":
            if len(records) == 1:
                return self._render_contract_detail_text(records[0], style=active_style)
            return self._render_contract_list_text(records, total=total, style=active_style)

        if domain == "bidding":
            if len(records) == 1:
                return self._render_bidding_detail_text(records[0], style=active_style)
            return self._render_bidding_list_text(records, total=total, style=active_style)

        if domain == "team_overview":
            if len(records) == 1:
                return self._render_team_detail_text(records[0], style=active_style)
            return self._render_team_list_text(records, total=total, style=active_style)

        return default_text

    def _record_fields(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        fields_text = record.get("fields_text")
        if isinstance(fields_text, Mapping):
            return fields_text
        fields = record.get("fields")
        if isinstance(fields, Mapping):
            return fields
        return {}

    def _pick_field(self, fields: Mapping[str, Any], keys: list[str]) -> str:
        for key in keys:
            value = str(fields.get(key) or "").strip()
            if value:
                return value
        return ""

    def _short_date(self, raw: str) -> str:
        text = str(raw or "").strip()
        if not text:
            return "—"
        normalized = text.replace("/", "-").replace(".", "-")
        if "T" in normalized:
            normalized = normalized.replace("T", " ")
        try:
            dt = datetime.fromisoformat(normalized)
            return dt.strftime("%m-%d %H:%M")
        except ValueError:
            pass
        try:
            d = date.fromisoformat(normalized.split(" ", 1)[0])
            return d.strftime("%m-%d")
        except ValueError:
            return text

    def _deadline_badge(self, raw: str) -> str:
        text = str(raw or "").strip()
        if not text or text == "—":
            return "➖ 未设置"
        normalized = text.replace("/", "-").replace(".", "-")
        if "T" in normalized:
            normalized = normalized.replace("T", " ")
        try:
            due = date.fromisoformat(normalized.split(" ", 1)[0])
        except ValueError:
            return "➖ 未设置"
        today = date.today()
        delta = (due - today).days
        if delta < 0:
            return f"❌ 已过期{abs(delta)}天"
        if delta == 0:
            return "⏰ 今日到期"
        if delta <= 3:
            return f"⏰ 还有{delta}天"
        if delta <= 7:
            return f"🟡 {delta}天后"
        return f"🟢 {delta}天后"

    def _urgency_badge(self, raw: str) -> str:
        text = str(raw or "").strip()
        if not text:
            return "⚪ 未标注"
        if "重要紧急" in text:
            return f"🔴 {text}"
        if "重要" in text or "紧急" in text:
            return f"🟡 {text}"
        return f"🔵 {text}"

    def _render_case_detail_text(self, record: Mapping[str, Any], style: str) -> str:
        fields = self._record_fields(record)
        project_id = self._pick_field(fields, ["项目 ID", "项目ID", "项目号", "record_id"]) or "—"
        project_type = self._pick_field(fields, ["项目类型", "案件分类"]) or "—"
        category = self._pick_field(fields, ["案件分类", "案由"]) or "—"
        client = self._pick_field(fields, ["委托人", "客户名称", "甲方"]) or "—"
        opponent = self._pick_field(fields, ["对方当事人", "乙方"]) or "—"
        contact_person = self._pick_field(fields, ["联系人", "联系人姓名"]) or "—"
        contact_info = self._pick_field(fields, ["联系方式", "手机号", "联系电话"]) or "—"
        case_no = self._pick_field(fields, ["案号", "案件号"]) or "—"
        court = self._pick_field(fields, ["审理法院", "法院"]) or "—"
        stage = self._pick_field(fields, ["审理程序", "程序阶段"]) or "—"
        judge = self._pick_field(fields, ["承办法官", "法官"]) or "—"
        owner = self._pick_field(fields, ["主办律师", "负责人"]) or "—"
        co_owner = self._pick_field(fields, ["协办律师"]) or "—"
        hearing = self._pick_field(fields, ["开庭日", "开庭时间"]) or "—"
        jurisdiction = self._pick_field(fields, ["管辖权异议截止日"]) or "—"
        evidence = self._pick_field(fields, ["举证截止日"]) or "—"
        seizure = self._pick_field(fields, ["查封到期日", "查封到期"]) or "—"
        counterclaim = self._pick_field(fields, ["反诉截止日"]) or "—"
        appeal = self._pick_field(fields, ["上诉截止日"]) or "—"
        status = self._pick_field(fields, ["案件状态", "状态"]) or "未标注"
        urgency = self._urgency_badge(self._pick_field(fields, ["重要紧急程度", "紧急程度"]))
        todo = self._pick_field(fields, ["待做事项", "待办事项", "待办"]) or "—"
        progress = self._pick_field(fields, ["进展", "最新进展"]) or "—"
        remark = self._pick_field(fields, ["备注"]) or "—"
        link = str(record.get("record_url") or "").strip()

        header = "📅 重要日期总览" if style == "T3C" else "📌 案件详情"
        lines = [
            header,
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🔖 {project_id} | {project_type}",
            f"📂 案件分类：{category}",
            "━━ 当事人信息 ━━",
            f"🏢 委托人：{client}",
            f"🆚 对方：{opponent}",
            f"📞 联系人：{contact_person} | {contact_info}",
            "━━ 案件信息 ━━",
            f"📄 案号：{case_no}",
            f"⚖️ 审理法院：{court}",
            f"📌 审理程序：{stage}",
            f"👨‍⚖️ 承办法官：{judge}",
            "━━ 承办律师 ━━",
            f"👤 主办：{owner} | 协办：{co_owner}",
            "━━ 重要日期 ━━",
            f"📅 开庭日：{hearing} {self._deadline_badge(hearing)}",
            f"⚠️ 管辖权异议截止：{jurisdiction} {self._deadline_badge(jurisdiction)}",
            f"⚠️ 举证截止：{evidence} {self._deadline_badge(evidence)}",
            f"📎 查封到期：{seizure} {self._deadline_badge(seizure)}",
            f"📎 反诉截止：{counterclaim} {self._deadline_badge(counterclaim)}",
            f"📎 上诉截止：{appeal} {self._deadline_badge(appeal)}",
            "━━ 案件动态 ━━",
            f"{urgency} | {status}",
            f"📝 待办事项：{todo}",
            f"💬 最新进展：{progress}",
            f"💡 备注：{remark}",
        ]
        if link:
            lines.append(f"🔗 查看详情：{link}")
        return "\n".join(lines)

    def _render_case_list_text(self, records: list[Mapping[str, Any]], total: int, style: str) -> str:
        shown = len(records)
        title = f"🔍 找到 {total} 个相关案件（显示前{shown}条）"
        if style == "T3A":
            title = "📅 近期开庭安排"
        elif style == "T3B":
            title = "⚠️ 重要截止日提醒"
        elif style == "T4A":
            title = "👤 律师案件总览"
        elif style == "T4B":
            title = "🔍 当事人/联系人查找结果"
        elif style == "T5A":
            title = "📝 待办事项看板"
        elif style == "T5B":
            title = "💬 案件进展查询"
        elif style == "T5C":
            title = "📌 状态筛选结果"
        elif style == "T6":
            title = "⚖️ 法院/程序/案号查询结果"

        lines: list[str] = [title, "━━━━━━━━━━━━━━━━━━━━━━━━"]
        for index, record in enumerate(records, start=1):
            fields = self._record_fields(record)
            project_id = self._pick_field(fields, ["项目 ID", "项目ID", "项目号", "record_id"]) or "—"
            client = self._pick_field(fields, ["委托人", "客户名称", "甲方"]) or "—"
            opponent = self._pick_field(fields, ["对方当事人", "乙方"]) or "—"
            category = self._pick_field(fields, ["案件分类", "案由"]) or "—"
            hearing = self._pick_field(fields, ["开庭日", "开庭时间"]) or "—"
            court = self._pick_field(fields, ["审理法院", "法院"]) or "—"
            owner = self._pick_field(fields, ["主办律师", "负责人"]) or "—"
            status = self._pick_field(fields, ["案件状态", "状态"]) or "未标注"
            urgency = self._urgency_badge(self._pick_field(fields, ["重要紧急程度", "紧急程度"]))
            case_no = self._pick_field(fields, ["案号", "案件号"]) or "—"
            progress = self._pick_field(fields, ["进展", "最新进展"]) or "—"
            todo = self._pick_field(fields, ["待做事项", "待办事项", "待办"]) or "—"
            link = str(record.get("record_url") or "").strip()

            lines.append(f"{index}️⃣ {project_id}")
            lines.append(f"🏢 {client} vs {opponent}")
            if style in {"T3A", "T3B", "T3C"}:
                lines.append(f"📅 关键日期：{hearing} | {self._deadline_badge(hearing)}")
            elif style in {"T5A", "T5B", "T5C"}:
                lines.append(f"📋 {category} | {urgency} | {status}")
                lines.append(f"📝 待办：{todo}")
                if style == "T5B":
                    lines.append(f"💬 进展：{progress}")
            elif style == "T6":
                lines.append(f"📄 案号：{case_no}")
                lines.append(f"⚖️ {court} | 👤 {owner} | {status}")
            else:
                lines.append(f"📋 {category} | 📅 开庭：{self._short_date(hearing)} ({self._deadline_badge(hearing)})")
                lines.append(f"⚖️ {court} | 👤 {owner} | {urgency} | {status}")

            if link:
                lines.append(f"🔗 查看详情：{link}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")

        return "\n".join(lines)

    def _render_contract_detail_text(self, record: Mapping[str, Any], style: str) -> str:
        fields = self._record_fields(record)
        contract_id = self._pick_field(fields, ["合同编号", "合同号", "项目ID"]) or "—"
        contract_type = self._pick_field(fields, ["合同类型", "类型"]) or "—"
        contract_name = self._pick_field(fields, ["合同名称", "标题"]) or "—"
        client = self._pick_field(fields, ["客户名称", "甲方", "委托人"]) or "—"
        owner = self._pick_field(fields, ["主办律师", "负责人"]) or "—"
        amount = self._pick_field(fields, ["合同金额", "金额"]) or "—"
        status = self._pick_field(fields, ["合同状态", "状态"]) or "—"
        payment_status = self._pick_field(fields, ["开票付款状态", "付款状态"]) or "—"
        sign_date = self._pick_field(fields, ["签约日期"]) or "—"
        start_date = self._pick_field(fields, ["开始日期"]) or "—"
        end_date = self._pick_field(fields, ["结束日期", "到期日期"]) or "—"
        seal_status = self._pick_field(fields, ["盖章状态"]) or "—"
        linked_project = self._pick_field(fields, ["关联项目", "项目ID"]) or "—"
        link = str(record.get("record_url") or "").strip()

        lines = [
            "📋 合同详情",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📋 合同号：{contract_id}",
            f"📂 合同类型：{contract_type}",
            f"📄 合同名称：{contract_name}",
            f"🏢 客户：{client}",
            f"👤 主办律师：{owner}",
            f"💰 合同金额：{amount}",
            f"📌 合同状态：{status}",
            f"💳 开票付款状态：{payment_status}",
            f"📅 签约日期：{sign_date}",
            f"📅 开始日期：{start_date}",
            f"📅 结束日期：{end_date} {self._deadline_badge(end_date)}",
            f"📎 盖章状态：{seal_status}",
            f"🔗 关联项目：{linked_project}",
        ]
        if link:
            lines.append(f"🔗 查看详情：{link}")
        return "\n".join(lines)

    def _render_contract_list_text(self, records: list[Mapping[str, Any]], total: int, style: str) -> str:
        title = f"🔍 找到 {total} 份合同（显示前{len(records)}条）"
        if style == "HT-T3":
            title = "💳 合同状态聚焦"
        lines = [title, "━━━━━━━━━━━━━━━━━━━━━━━━"]
        for index, record in enumerate(records, start=1):
            fields = self._record_fields(record)
            contract_id = self._pick_field(fields, ["合同编号", "合同号", "项目ID"]) or "—"
            contract_name = self._pick_field(fields, ["合同名称", "标题"]) or "—"
            client = self._pick_field(fields, ["客户名称", "甲方", "委托人"]) or "—"
            amount = self._pick_field(fields, ["合同金额", "金额"]) or "—"
            payment_status = self._pick_field(fields, ["开票付款状态", "付款状态"]) or "—"
            end_date = self._pick_field(fields, ["结束日期", "到期日期"]) or "—"
            seal_status = self._pick_field(fields, ["盖章状态"]) or "—"
            link = str(record.get("record_url") or "").strip()
            lines.append(f"{index}️⃣ {contract_id} | {contract_name}")
            lines.append(f"🏢 {client}")
            lines.append(f"💰 {amount} | {payment_status}")
            lines.append(f"📅 到期：{end_date} {self._deadline_badge(end_date)} | {seal_status}")
            if link:
                lines.append(f"🔗 查看详情：{link}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def _render_bidding_detail_text(self, record: Mapping[str, Any], style: str) -> str:
        fields = self._record_fields(record)
        bid_id = self._pick_field(fields, ["项目号", "编号", "项目ID"]) or "—"
        project_name = self._pick_field(fields, ["投标项目名称", "项目名称", "标题"]) or "—"
        owner_org = self._pick_field(fields, ["招标方", "业主单位"]) or "—"
        owner = self._pick_field(fields, ["承办律师", "负责人"]) or "—"
        phase = self._pick_field(fields, ["阶段", "进度", "状态"]) or "—"
        close_date = self._pick_field(fields, ["截标时间", "投标截止日", "截止日"]) or "—"
        book_status = self._pick_field(fields, ["标书领取状态", "标书状态"]) or "—"
        deposit_status = self._pick_field(fields, ["保证金缴纳状态", "保证金状态"]) or "—"
        bid_result = self._pick_field(fields, ["是否中标", "中标状态"]) or "—"
        bid_amount = self._pick_field(fields, ["中标金额", "金额"]) or "—"
        link = str(record.get("record_url") or "").strip()
        lines = [
            "🏁 招投标详情",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🔖 {bid_id}",
            f"📋 投标项目：{project_name}",
            f"🏢 招标方：{owner_org}",
            f"👤 承办律师：{owner}",
            f"📌 当前阶段：{phase}",
            f"📅 截标时间：{close_date} {self._deadline_badge(close_date)}",
            f"📄 标书状态：{book_status}",
            f"💰 保证金：{deposit_status}",
            f"🏆 中标结果：{bid_result}",
            f"💰 中标金额：{bid_amount}",
        ]
        if link:
            lines.append(f"🔗 查看详情：{link}")
        return "\n".join(lines)

    def _render_bidding_list_text(self, records: list[Mapping[str, Any]], total: int, style: str) -> str:
        title = f"🔍 进行中的招投标项目（共{total}个）"
        if style == "ZB-T3":
            title = "📅 招投标时间线"
        elif style == "ZB-T4":
            title = "🏆 招投标结果"
        lines = [title, "━━━━━━━━━━━━━━━━━━━━━━━━"]
        for index, record in enumerate(records, start=1):
            fields = self._record_fields(record)
            bid_id = self._pick_field(fields, ["项目号", "编号", "项目ID"]) or "—"
            project_name = self._pick_field(fields, ["投标项目名称", "项目名称", "标题"]) or "—"
            owner_org = self._pick_field(fields, ["招标方", "业主单位"]) or "—"
            owner = self._pick_field(fields, ["承办律师", "负责人"]) or "—"
            close_date = self._pick_field(fields, ["截标时间", "投标截止日", "截止日"]) or "—"
            phase = self._pick_field(fields, ["阶段", "进度", "状态"]) or "—"
            amount = self._pick_field(fields, ["中标金额", "金额"]) or "—"
            link = str(record.get("record_url") or "").strip()
            lines.append(f"{index}️⃣ {bid_id}")
            lines.append(f"📋 {project_name}")
            lines.append(f"🏢 {owner_org}")
            lines.append(f"👤 {owner} | 💰 {amount}")
            lines.append(f"📅 截标：{self._short_date(close_date)} ({self._deadline_badge(close_date)})")
            lines.append(f"📝 当前阶段：{phase}")
            if link:
                lines.append(f"🔗 查看详情：{link}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def _render_team_detail_text(self, record: Mapping[str, Any], style: str) -> str:
        fields = self._record_fields(record)
        record_id = self._pick_field(fields, ["record_id", "记录 ID"]) or "—"
        desc = self._pick_field(fields, ["任务描述", "描述"]) or "—"
        task_type = self._pick_field(fields, ["任务类型", "类型"]) or "—"
        status = self._pick_field(fields, ["状态", "进展"]) or "—"
        creator = self._pick_field(fields, ["发起人"]) or "—"
        helper = self._pick_field(fields, ["请求协助人", "协助人"]) or "—"
        deadline = self._pick_field(fields, ["截止时间", "截止日"]) or "—"
        urgency = self._urgency_badge(self._pick_field(fields, ["重要紧急程度", "紧急程度"]))
        link = str(record.get("record_url") or "").strip()
        lines = [
            "📋 任务详情",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🔖 {record_id}",
            f"📋 任务描述：{desc}",
            f"📂 任务类型：{task_type}",
            f"📌 状态：{status}",
            f"{urgency}",
            f"👤 发起人：{creator}",
            f"🤝 请求协助人：{helper}",
            f"📅 截止：{deadline} {self._deadline_badge(deadline)}",
            "⚠️ 只读数据",
        ]
        if link:
            lines.append(f"🔗 查看详情：{link}")
        return "\n".join(lines)

    def _render_team_list_text(self, records: list[Mapping[str, Any]], total: int, style: str) -> str:
        title = f"📋 任务列表（共 {total} 条）"
        if style == "RW-T3":
            title = "📋 任务看板"
        elif style == "RW-T4":
            title = "👤 成员任务总览"
        lines = [title, "━━━━━━━━━━━━━━━━━━━━━━━━"]
        for index, record in enumerate(records, start=1):
            fields = self._record_fields(record)
            member = self._pick_field(fields, ["成员", "负责人", "发起人"]) or "—"
            desc = self._pick_field(fields, ["任务描述", "描述"]) or "—"
            status = self._pick_field(fields, ["状态", "进展"]) or "—"
            due = self._pick_field(fields, ["截止时间", "截止日"]) or "—"
            urgency = self._urgency_badge(self._pick_field(fields, ["重要紧急程度", "紧急程度"]))
            link = str(record.get("record_url") or "").strip()
            lines.append(f"{index}️⃣ {member} | {desc}")
            lines.append(f"📌 {status} | {urgency}")
            lines.append(f"📅 截止：{self._short_date(due)} ({self._deadline_badge(due)})")
            if link:
                lines.append(f"🔗 查看详情：{link}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⚠️ 只读数据")
        return "\n".join(lines)

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
        config_root = Path(__file__).resolve().parents[4] / "config"
        new_path = config_root / "messages" / "zh-CN" / "responses.yaml"
        if new_path.exists():
            return new_path
        return config_root / "responses.yaml"

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
