"""
描述: 提供用于渲染C1/C2/C3确认卡片块的帮助类
主要功能:
    - 根据不同的操作类型构建确认信息
    - 解析和处理日期字段以生成自动提醒
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping

from src.adapters.channels.feishu.card_template_config import get_render_templates
from src.adapters.channels.feishu.smart_engine import SmartEngine


class ActionEngine:
    """
    Render helper for C1/C2/C3 confirmation card blocks.

    功能:
        - 初始化智能引擎实例
        - 根据操作类型构建确认信息
        - 解析和处理日期字段以生成自动提醒
    """

    def __init__(self, smart_engine: SmartEngine | None = None) -> None:
        """
        初始化ActionEngine实例

        功能:
            - 如果未提供智能引擎实例，则创建一个新的SmartEngine实例
        """
        self._smart = smart_engine or SmartEngine()

    def build_confirm_lines(
        self,
        *,
        action: str,
        message: str,
        table_name: str,
        payload: Mapping[str, Any],
    ) -> tuple[str | None, list[str]]:
        """
        根据操作类型构建确认信息行

        功能:
            - 根据操作类型（如create_record, update_record等）构建确认信息
            - 返回确认标题和详细信息行
        """
        normalized = str(action or "").strip()
        lines: list[str] = [str(message or "").strip()]
        if table_name:
            lines.append(f"- 数据表: {table_name}")

        if normalized == "create_record":
            return "C1 新增确认", lines + self._build_create_lines(payload)
        if normalized == "update_record":
            return "C2 修改确认", lines + self._build_update_lines(payload)
        if normalized == "create_reminder":
            return "自动提醒创建确认", lines + self._build_reminder_lines(payload)
        if normalized == "delete_record":
            return "C3 删除确认", lines + self._build_delete_lines(payload)
        if normalized == "close_record":
            title = str(payload.get("close_title") or "C3 关闭确认").strip() or "C3 关闭确认"
            return title, lines + self._build_close_lines(payload)
        if normalized:
            lines.append(f"- 操作类型: {normalized}")
        return None, lines

    def _build_create_lines(self, payload: Mapping[str, Any]) -> list[str]:
        """
        构建新增记录的详细信息行

        功能:
            - 从payload中提取字段信息
            - 根据DSL模板构建详细信息行
            - 检查并添加缺少的字段信息
        """
        lines: list[str] = []
        fields_raw = payload.get("fields")
        fields = fields_raw if isinstance(fields_raw, Mapping) else {}
        table_name = str(payload.get("table_name") or "").strip()

        dsl_lines = self._build_create_lines_from_detail_dsl(table_name=table_name, fields=fields)
        if dsl_lines:
            lines.extend(dsl_lines)
        elif fields:
            lines.append("- 待新增字段:")
            for key, value in list(fields.items())[:16]:
                text = str(value or "").strip() or "—"
                lines.append(f"  - {key}: {text}")

        required_raw = payload.get("required_fields")
        required = [str(item).strip() for item in required_raw if str(item).strip()] if isinstance(required_raw, list) else []
        missing = [name for name in required if not str(fields.get(name) or "").strip()]
        if missing:
            lines.append("- 缺少字段: " + "、".join(missing))
        return lines

    def _build_create_lines_from_detail_dsl(self, *, table_name: str, fields: Mapping[str, Any]) -> list[str]:
        """
        根据DSL模板构建新增记录的详细信息行

        功能:
            - 解析DSL模板并提取相关字段信息
            - 构建详细信息行
        """
        domain = self._resolve_domain(table_name)
        style = {"case": "T1", "contracts": "HT-T1", "bidding": "ZB-T1"}.get(domain, "")
        if not style:
            return []

        render_templates = get_render_templates()
        query_cfg_raw = render_templates.get("query_list_v2") if isinstance(render_templates, Mapping) else {}
        query_cfg = query_cfg_raw if isinstance(query_cfg_raw, Mapping) else {}
        style_dsl_raw = (
            query_cfg.get("template_dsl", {})
            .get(domain, {})
            .get("styles", {})
            .get(style, {})
        )
        style_dsl = style_dsl_raw if isinstance(style_dsl_raw, Mapping) else {}
        specs_raw = style_dsl.get("detail_fields")
        specs = specs_raw if isinstance(specs_raw, list) else []
        if not specs:
            return []

        canonical_values: dict[str, str] = {}
        field_mapping_raw = query_cfg.get("field_mapping", {}).get(domain, {})
        field_mapping = field_mapping_raw if isinstance(field_mapping_raw, Mapping) else {}
        for source_name, mapped_key in field_mapping.items():
            source_text = str(source_name).strip()
            mapped_text = str(mapped_key).strip()
            if not source_text or not mapped_text:
                continue
            value = str(fields.get(source_text) or "").strip()
            if value:
                canonical_values[mapped_text] = value
        for key, value in fields.items():
            k = str(key).strip()
            v = str(value or "").strip()
            if k and v and k not in canonical_values:
                canonical_values[k] = v

        lines = ["- 待新增字段:"]
        for spec in specs:
            if not isinstance(spec, Mapping):
                continue
            key = str(spec.get("name") or spec.get("key") or "").strip()
            if not key:
                continue
            value = canonical_values.get(key, "")
            if not value:
                value = self._resolve_value_by_field_keys(query_cfg=query_cfg, domain=domain, key=key, fields=fields)
            if not value:
                continue
            label = str(spec.get("label") or "").strip() or key
            if label == "":
                lines.append(f"  - {value}")
            else:
                lines.append(f"  - {label}: {value}")
        return lines if len(lines) > 1 else []

    def _resolve_value_by_field_keys(
        self,
        *,
        query_cfg: Mapping[str, Any],
        domain: str,
        key: str,
        fields: Mapping[str, Any],
    ) -> str:
        """
        根据字段键解析值

        功能:
            - 从query_cfg中提取字段键并解析值
        """
        field_keys_raw = query_cfg.get("field_keys", {}).get(domain, {})
        field_keys = field_keys_raw if isinstance(field_keys_raw, Mapping) else {}
        candidates_raw = field_keys.get(key)
        candidates = [str(item).strip() for item in candidates_raw if str(item).strip()] if isinstance(candidates_raw, list) else []
        if not candidates:
            candidates = [key]
        for name in candidates:
            value = str(fields.get(name) or "").strip()
            if value:
                return value
        return ""

    def _resolve_domain(self, table_name: str) -> str:
        """
        解析数据表名称以确定领域

        功能:
            - 根据数据表名称中的关键词确定领域
        """
        combined = str(table_name or "").replace(" ", "")
        if "合同" in combined:
            return "contracts"
        if any(token in combined for token in ("招投标", "投标", "台账")):
            return "bidding"
        if any(token in combined for token in ("团队", "成员", "工作总览")):
            return "team_overview"
        return "case"

    def _build_update_lines(self, payload: Mapping[str, Any]) -> list[str]:
        """
        构建更新记录的详细信息行

        功能:
            - 从payload中提取变更明细
            - 构建详细信息行
        """
        lines: list[str] = []
        diff_raw = payload.get("diff")
        diff = diff_raw if isinstance(diff_raw, list) else []
        if not diff:
            return lines

        lines.append("- 变更明细:")
        for item in diff:
            if not isinstance(item, Mapping):
                continue
            field = str(item.get("field") or "字段").strip()
            old = str(item.get("old") or "").strip() or "(空)"
            new = str(item.get("new") or "").strip() or "(空)"
            mode = str(item.get("mode") or "").strip().lower()
            if mode == "append":
                delta = str(item.get("delta") or "").strip()
                lines.append(f"  - {field}")
                lines.append("    模式: 追加")
                lines.append(f"    旧值: {old}")
                if delta:
                    lines.append(f"    新增: {delta}")
                lines.append(f"    追加后: {new}")
            else:
                lines.append(f"  - {field}")
                lines.append(f"    旧值: {old}")
                lines.append(f"    新值: {new}")
            if "进展" in field:
                suggestions = self._smart.analyze_progress_for_suggestions(new, table_type="case")
                for suggestion in suggestions:
                    field_label = str(suggestion.get("field_label") or suggestion.get("field") or "")
                    reason = str(suggestion.get("reason") or "").strip()
                    tip = f"建议同步确认字段：{field_label}"
                    if reason:
                        tip = f"{tip}（{reason}）"
                    lines.append(f"  - ⚠️ {tip}")
        return lines

    def _build_delete_lines(self, payload: Mapping[str, Any]) -> list[str]:
        """
        构建删除记录的详细信息行

        功能:
            - 从payload中提取记录ID
            - 构建详细信息行并添加警告信息
        """
        lines: list[str] = []
        record_id = str(payload.get("record_id") or "").strip()
        if record_id:
            lines.append(f"- 目标记录: {record_id}")
        lines.append("- 警告: 该操作可能不可撤销")
        return lines

    def _build_close_lines(self, payload: Mapping[str, Any]) -> list[str]:
        """
        构建关闭记录的详细信息行

        功能:
            - 从payload中提取状态变更信息
            - 构建详细信息行并添加操作后影响
        """
        lines: list[str] = []
        status_field = str(payload.get("close_status_field") or "状态").strip() or "状态"
        before = str(payload.get("close_status_from") or "").strip() or "(空)"
        after = str(payload.get("close_status_value") or "").strip() or "(空)"
        lines.append(f"- 状态变更: {status_field}: {before} -> {after}")

        consequences_raw = payload.get("close_consequences")
        consequences = [str(item).strip() for item in consequences_raw if str(item).strip()] if isinstance(consequences_raw, list) else []
        if consequences:
            lines.append("- 操作后影响:")
            for item in consequences[:6]:
                lines.append(f"  - {item}")
        return lines

    def _build_reminder_lines(self, payload: Mapping[str, Any]) -> list[str]:
        """
        构建提醒的详细信息行

        功能:
            - 从payload中提取提醒信息
            - 构建详细信息行
        """
        lines: list[str] = []
        reminders_raw = payload.get("reminders")
        reminders = reminders_raw if isinstance(reminders_raw, list) else []
        if reminders:
            lines.append("- 待创建提醒:")
            for item in reminders[:20]:
                if not isinstance(item, Mapping):
                    continue
                content = str(item.get("content") or "提醒事项").strip()
                remind_time = str(item.get("remind_time") or "").strip()
                lines.append(f"  - {content} @ {remind_time}")
        else:
            lines.append("- 未检测到可创建的提醒")
        return lines

    def build_auto_reminders(self, table_name: str, fields: Mapping[str, Any]) -> list[str]:
        """
        构建自动提醒

        功能:
            - 根据数据表名称和字段信息构建自动提醒
        """
        table = str(table_name or "")
        reminder_defs = {
            "案件": {
                "开庭日": 3,
                "管辖权异议截止日": 3,
                "举证截止日": 3,
                "查封到期日": 30,
                "反诉截止日": 3,
                "上诉截止日": 3,
            },
            "合同": {
                "合同结束日期": 30,
            },
            "招投标": {
                "标书购买截止时间": 2,
                "截标时间": 3,
                "保证金截止日期": 2,
            },
        }
        picked: dict[str, int] = {}
        for key, defs in reminder_defs.items():
            if key in table:
                picked = defs
                break
        if not picked:
            return []

        reminders: list[str] = []
        for field_name, days_before in picked.items():
            raw = fields.get(field_name)
            target = self._parse_date(raw)
            if target is None:
                continue
            remind_date = target - timedelta(days=days_before)
            if remind_date < date.today():
                continue
            reminders.append(f"{field_name}: {remind_date.isoformat()}（提前{days_before}天）")
        return reminders

    def build_auto_reminder_items(self, table_name: str, fields: Mapping[str, Any]) -> list[dict[str, str]]:
        """
        构建自动提醒项

        功能:
            - 根据数据表名称和字段信息构建自动提醒项
        """
        table = str(table_name or "")
        reminder_defs = {
            "案件": {
                "开庭日": (3, "开庭提醒"),
                "管辖权异议截止日": (3, "管辖权异议截止提醒"),
                "举证截止日": (3, "举证截止提醒"),
                "查封到期日": (30, "查封到期提醒"),
                "反诉截止日": (3, "反诉截止提醒"),
                "上诉截止日": (3, "上诉截止提醒"),
            },
            "合同": {
                "合同结束日期": (30, "合同到期提醒"),
            },
            "招投标": {
                "标书购买截止时间": (2, "标书购买截止提醒"),
                "截标时间": (3, "截标提醒"),
                "保证金截止日期": (2, "保证金截止提醒"),
            },
        }
        picked: dict[str, tuple[int, str]] = {}
        for key, defs in reminder_defs.items():
            if key in table:
                picked = defs
                break
        if not picked:
            return []

        items: list[dict[str, str]] = []
        for field_name, config in picked.items():
            days_before, label = config
            target = self._parse_date(fields.get(field_name))
            if target is None:
                continue
            remind_date = target - timedelta(days=days_before)
            if remind_date < date.today():
                continue
            items.append(
                {
                    "field": field_name,
                    "content": f"{label}（{field_name}）",
                    "remind_time": f"{remind_date.isoformat()} 09:00",
                    "priority": "medium",
                }
            )
        return items

    def _parse_date(self, value: Any) -> date | None:
        """
        解析日期字符串为date对象

        功能:
            - 尝试将输入字符串解析为date对象
        """
        text = str(value or "").strip().lstrip("：:")
        if not text:
            return None
        normalized = text.replace("年", "-").replace("月", "-").replace("日", "")
        normalized = normalized.replace("/", "-").replace(".", "-")
        if "T" in normalized:
            normalized = normalized.split("T", 1)[0]
        if " " in normalized:
            normalized = normalized.split(" ", 1)[0]
        try:
            return date.fromisoformat(normalized)
        except ValueError:
            return None
