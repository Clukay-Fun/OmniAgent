"""
描述: Discord 响应格式化器
主要功能:
    - 将 RenderedResponse 转换为 Discord text/embed/components
    - 实现 Embed 优先策略（查询/摘要/状态）
    - 提供确认/取消按钮的轻量组件模型
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.response.models import Block, RenderedResponse


@dataclass
class DiscordEmbedFieldPayload:
    name: str
    value: str
    inline: bool = False


@dataclass
class DiscordEmbedPayload:
    title: str
    description: str
    fields: list[DiscordEmbedFieldPayload] = field(default_factory=list)


@dataclass
class DiscordComponentButtonPayload:
    label: str
    custom_id: str
    style: str = "primary"


@dataclass
class DiscordResponsePayload:
    text: str
    embed: DiscordEmbedPayload | None = None
    components: list[DiscordComponentButtonPayload] = field(default_factory=list)


class DiscordFormatter:
    """Discord 渲染格式化器。"""

    _TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*[:\-\s|]+\|?\s*$")

    def __init__(self, *, embed_enabled: bool = True, components_enabled: bool = True) -> None:
        self._embed_enabled = bool(embed_enabled)
        self._components_enabled = bool(components_enabled)

    def format(self, rendered: RenderedResponse) -> DiscordResponsePayload:
        text = self._build_text_payload(rendered)
        embed = self._build_embed(rendered) if self._embed_enabled and self._should_use_embed(rendered) else None
        components = self._build_components(rendered) if self._components_enabled else []
        return DiscordResponsePayload(text=text, embed=embed, components=components)

    def _build_text_payload(self, rendered: RenderedResponse) -> str:
        template = rendered.card_template
        if template is not None and str(template.template_id or "").strip() == "query.list":
            compact = self._build_query_list_text(template.params if isinstance(template.params, Mapping) else {})
            if compact:
                return compact
        return str(rendered.text_fallback or "").strip() or "请稍后重试。"

    def _should_use_embed(self, rendered: RenderedResponse) -> bool:
        skill_name = str(rendered.meta.get("skill_name") or "").strip()
        template_id = str(getattr(rendered.card_template, "template_id", "") or "").strip()
        if template_id == "query.list":
            return False
        if template_id in {"query.detail", "error.notice", "delete.success", "delete.cancelled"}:
            return True
        if any(block.type in {"heading", "kv_list", "bullet_list"} for block in rendered.blocks):
            return True
        if skill_name == "QuerySkill":
            return False
        return False

    def _build_embed(self, rendered: RenderedResponse) -> DiscordEmbedPayload | None:
        title = self._pick_embed_title(rendered)
        description_parts: list[str] = []
        fields: list[DiscordEmbedFieldPayload] = []

        for block in rendered.blocks:
            if block.type == "heading":
                continue
            if block.type == "paragraph":
                text = self._normalize_text_block(str(block.content.get("text") or "").strip())
                if text:
                    description_parts.append(text)
                continue
            if block.type == "callout":
                text = str(block.content.get("text") or "").strip()
                if text:
                    description_parts.append(f"> {text}")
                continue
            if block.type == "bullet_list":
                items = block.content.get("items")
                if isinstance(items, list):
                    bullet_lines = [f"- {str(item).strip()}" for item in items if str(item).strip()]
                    if bullet_lines:
                        description_parts.append("\n".join(bullet_lines))
                continue
            if block.type == "kv_list":
                fields.extend(self._kv_fields(block))

        description = "\n\n".join(part for part in description_parts if part).strip()
        if not description:
            description = self._normalize_text_block(rendered.text_fallback)

        if not title and not description and not fields:
            return None
        return DiscordEmbedPayload(title=title or "结果", description=description or "-", fields=fields[:25])

    def _pick_embed_title(self, rendered: RenderedResponse) -> str:
        for block in rendered.blocks:
            if block.type != "heading":
                continue
            title = str(block.content.get("text") or "").strip()
            if title:
                return title
        skill_name = str(rendered.meta.get("skill_name") or "").strip()
        title_map = {
            "QuerySkill": "查询结果",
            "CreateSkill": "操作结果",
            "UpdateSkill": "操作结果",
            "DeleteSkill": "操作结果",
            "ReminderSkill": "提醒结果",
        }
        return title_map.get(skill_name, "")

    def _kv_fields(self, block: Block) -> list[DiscordEmbedFieldPayload]:
        items = block.content.get("items")
        if not isinstance(items, list):
            return []
        fields: list[DiscordEmbedFieldPayload] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            key = str(item.get("key") or "").strip() or "字段"
            value = str(item.get("value") or "").strip() or "-"
            fields.append(DiscordEmbedFieldPayload(name=key[:256], value=value[:1024], inline=False))
        return fields

    def _normalize_text_block(self, text: str) -> str:
        lines = [line.rstrip() for line in str(text or "").splitlines()]
        if self._looks_like_markdown_table(lines):
            visible_lines = [line.strip() for line in lines if line.strip() and not self._TABLE_SEPARATOR.match(line)]
            table_text = "\n".join(visible_lines)
            return f"```text\n{table_text}\n```"
        return str(text or "").strip()

    def _looks_like_markdown_table(self, lines: list[str]) -> bool:
        if len(lines) < 2:
            return False
        has_pipe = any("|" in line for line in lines)
        has_separator = any(self._TABLE_SEPARATOR.match(line) for line in lines)
        return has_pipe and has_separator

    def _build_components(self, rendered: RenderedResponse) -> list[DiscordComponentButtonPayload]:
        template = rendered.card_template
        if template is None:
            return []
        params = template.params if isinstance(template.params, dict) else {}

        action = str(params.get("action") or "").strip()
        actions_raw = params.get("actions")
        actions = actions_raw if isinstance(actions_raw, Mapping) else {}
        cancel_action = params.get("cancel_action") if isinstance(params.get("cancel_action"), Mapping) else {}

        confirm_callback = self._extract_callback(actions.get("confirm"), f"{action}_confirm" if action else "")
        cancel_callback = self._extract_callback(actions.get("cancel"), f"{action}_cancel" if action else "")
        if not cancel_callback:
            cancel_callback = self._extract_callback(cancel_action, "")

        if not confirm_callback and not cancel_callback and template.template_id == "delete.confirm":
            confirm_callback = "delete_record_confirm"
            cancel_callback = "delete_record_cancel"
        if not cancel_callback and template.template_id == "update.guide":
            cancel_callback = "update_collect_fields_cancel"

        buttons: list[DiscordComponentButtonPayload] = []
        confirm_text = str(params.get("confirm_text") or "").strip() or "确认"
        cancel_text = str(params.get("cancel_text") or "").strip() or "取消"

        if confirm_callback:
            buttons.append(
                DiscordComponentButtonPayload(
                    label=confirm_text,
                    custom_id=f"omni:action:{confirm_callback}",
                    style="success",
                )
            )
        if cancel_callback:
            buttons.append(
                DiscordComponentButtonPayload(
                    label=cancel_text,
                    custom_id=f"omni:action:{cancel_callback}",
                    style="secondary",
                )
            )
        return buttons

    def _extract_callback(self, action_payload: Any, fallback: str) -> str:
        if isinstance(action_payload, Mapping):
            callback = str(action_payload.get("callback_action") or "").strip()
            if callback:
                return callback
        return str(fallback or "").strip()

    def _build_query_list_text(self, params: Mapping[str, Any]) -> str:
        records_raw = params.get("records")
        records = records_raw if isinstance(records_raw, list) else []
        if not records:
            return ""

        total = int(params.get("total") or len(records))
        display_limit = 5
        shown_records = records[:display_limit]
        lines: list[str] = [f"🔎 **查询结果**（共 {total} 条，本次展示 {len(shown_records)} 条）", ""]
        current_length = sum(len(item) for item in lines)

        for index, record in enumerate(shown_records, start=1):
            if not isinstance(record, Mapping):
                continue
            fields = self._record_fields(record)
            record_title = self._truncate(
                self._pick_field(fields, ["案号", "项目 ID", "项目ID", "项目号", "合同编号", "record_id"]) or f"记录{index}",
                28,
            )
            party_left = self._truncate(
                self._pick_field(fields, ["委托人", "客户名称", "投标项目名称", "任务描述", "标题"]) or "-",
                24,
            )
            party_right = self._truncate(
                self._pick_field(fields, ["对方当事人", "乙方", "招标方", "请求协助人"]),
                24,
            )
            event_time = self._truncate(
                self._pick_field(fields, ["开庭日", "开庭时间", "截止时间", "结束日期", "投标截止日", "提醒时间"]) or "-",
                20,
            )
            status = self._truncate(
                self._pick_field(fields, ["案件状态", "状态", "进度", "合同状态", "阶段"]) or "-",
                16,
            )
            court = self._truncate(
                self._pick_field(fields, ["审理法院", "法院", "承办单位"]),
                18,
            )

            line1 = f"**{index}. {record_title}**"
            line2 = f"　👥 {party_left}"
            if party_right:
                line2 = f"{line2} vs {party_right}"
            line3 = f"　📅 {event_time}"
            line4 = f"　📌 {status}"
            if court:
                line4 = f"{line4} ｜ ⚖ {court}"

            candidate_add = len(line1) + len(line2) + len(line3) + len(line4) + 4
            if current_length + candidate_add > 1700:
                remaining = max(len(shown_records) - index + 1, 0)
                if remaining > 0:
                    lines.append(f"…其余 {remaining} 条可回复“第N个详情”查看")
                break

            lines.append(line1)
            lines.append(line2)
            lines.append(line3)
            lines.append(line4)
            lines.append("")
            current_length += candidate_add

        hidden_in_page = max(len(records) - len(shown_records), 0)
        if hidden_in_page > 0:
            lines.append(f"本页其余 {hidden_in_page} 条可回复“第N个详情”查看（如：第6个详情）。")
        if total > len(shown_records):
            lines.append("➡ 回复“下一页”继续查看后续结果。")
        lines.append("ℹ 也可回复“第N个详情”查看单条详情。")

        return "\n".join(line for line in lines if line is not None).strip()

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

    def _truncate(self, text: str, max_chars: int) -> str:
        normalized = str(text or "").strip()
        if max_chars <= 0 or len(normalized) <= max_chars:
            return normalized
        return f"{normalized[: max_chars - 1]}…"
