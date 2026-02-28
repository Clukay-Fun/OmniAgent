"""
描述: 卡片模板渲染引擎
主要功能:
    - 提供结构化卡片渲染所需的数据拼接逻辑
    - 提供各类展现状态和日期倒计时的文本格式化
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from src.adapters.channels.feishu.actions.action_engine import ActionEngine
from src.adapters.channels.feishu.ui_cards.card_template_config import get_render_templates
from src.adapters.channels.feishu.utils.record_links import build_record_link_line
from src.adapters.channels.feishu.ui_cards.template_runtime import (
    FilterEngine,
    GroupEngine,
    SectionEngine,
    SummaryEngine,
)


_ACTION_ENGINE = ActionEngine()
_OK_MARKER = "✅"
_CASE_T3_STYLES = {"T3", "T3A", "T3B", "T3C"}
_CASE_T5_STYLES = {"T5", "T5A", "T5B", "T5C"}


# region Markdown 辅助方法
def _markdown(content: str) -> dict[str, Any]:
    return {"tag": "markdown", "content": content}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _render_templates() -> dict[str, Any]:
    raw = get_render_templates()
    if isinstance(raw, dict):
        return raw
    return {}


def _render_value(path: str, default: Any) -> Any:
    current: Any = _render_templates()
    for key in path.split("."):
        if not isinstance(current, Mapping):
            return default
        current = current.get(key)
    return default if current is None else current


def _field_keys(domain: str, key: str, fallback: list[str]) -> list[str]:
    raw = _render_value(f"query_list_v2.field_keys.{domain}.{key}", fallback)
    if not isinstance(raw, list):
        return fallback
    output = [str(item).strip() for item in raw if str(item).strip()]
    return output or fallback


def _field_mapping_sources(domain: str, field_key: str) -> list[str]:
    mapping_raw = _render_value(f"query_list_v2.field_mapping.{domain}", {})
    mapping = mapping_raw if isinstance(mapping_raw, Mapping) else {}
    sources: list[str] = []
    for source_name, mapped_key in mapping.items():
        if str(mapped_key).strip() == field_key:
            source_text = str(source_name).strip()
            if source_text:
                sources.append(source_text)
    return sources


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _template_root() -> Path:
    custom = _safe_text(_render_value("query_list_v2.template_files.root", ""))
    if custom:
        path = Path(custom)
        if not path.is_absolute():
            path = (Path(__file__).resolve().parents[5] / custom).resolve()
        return path
    config_root = Path(__file__).resolve().parents[5] / "config"
    new_root = config_root / "ui_templates" / "feishu" / "templates"
    if new_root.exists():
        return new_root
    return config_root / "templates"


def _resolve_template_file(template_path: str) -> Path | None:
    path_text = _safe_text(template_path)
    if not path_text:
        return None
    raw_path = Path(path_text)
    if raw_path.is_absolute():
        return raw_path
    return (_template_root() / raw_path).resolve()


@lru_cache(maxsize=128)
def _read_template_file(template_path: str) -> str:
    file_path = _resolve_template_file(template_path)
    if file_path is None or not file_path.exists():
        return ""
    try:
        return file_path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _render_placeholders(template: str, values: Mapping[str, Any]) -> str:
    if not template:
        return ""

    def _if_replace(match: re.Match[str]) -> str:
        key = _safe_text(match.group(1))
        body = str(match.group(2) or "")
        value = _safe_text(values.get(key))
        if value and value != "—":
            return body
        return ""

    rendered = re.sub(r"\{\{#if\s+([a-zA-Z0-9_]+)\s*\}\}(.*?)\{\{/if\}\}", _if_replace, template, flags=re.S)

    def _value_replace(match: re.Match[str]) -> str:
        key = _safe_text(match.group(1))
        value = values.get(key)
        return "" if value is None else str(value)

    rendered = re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", _value_replace, rendered)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return rendered.strip()


def _load_wrapper_template(wrapper_file: str, values: Mapping[str, Any]) -> dict[str, Any]:
    template = _read_template_file(wrapper_file)
    if not template:
        return {}
    rendered = _render_placeholders(template, values)
    if not rendered:
        return {}
    try:
        payload = json.loads(rendered)
    except Exception:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    return dict(payload)


def _load_layout_template_elements(layout_file: str, values: Mapping[str, Any]) -> list[dict[str, Any]]:
    template = _read_template_file(layout_file)
    if not template:
        return []

    escaped_values: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            escaped_values[str(key)] = ""
            continue
        text = str(value)
        text = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        escaped_values[str(key)] = text

    rendered = _render_placeholders(template, escaped_values)
    if not rendered:
        return []
    try:
        payload = json.loads(rendered)
    except Exception:
        return []

    if isinstance(payload, Mapping):
        elements_raw = payload.get("elements")
    elif isinstance(payload, list):
        elements_raw = payload
    else:
        elements_raw = []

    if not isinstance(elements_raw, list):
        return []
    return [dict(item) for item in elements_raw if isinstance(item, Mapping)]


def _render_text_template(
    config_path: str,
    default_template_file: str,
    values: Mapping[str, Any],
    fallback: str,
) -> str:
    template_file = _safe_text(_render_value(config_path, default_template_file))
    template_text = _read_template_file(template_file)
    if template_text:
        rendered = _render_placeholders(template_text, values)
        if rendered:
            return rendered
    return fallback


def _render_layout_template(
    config_path: str,
    default_layout_file: str,
    values: Mapping[str, Any],
) -> list[dict[str, Any]]:
    layout_file = _safe_text(_render_value(config_path, default_layout_file))
    if not layout_file:
        return []
    return _load_layout_template_elements(layout_file, values)


def _load_wrapper_from_config(
    config_path: str,
    default_wrapper_file: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    wrapper_file = _safe_text(_render_value(config_path, default_wrapper_file))
    if not wrapper_file:
        return {}
    return _load_wrapper_template(wrapper_file, values)


def _style_dsl(domain: str, style: str) -> Mapping[str, Any]:
    raw = _render_value(f"query_list_v2.template_dsl.{domain}.styles.{style}", {})
    return raw if isinstance(raw, Mapping) else {}


def _domain_table_label(domain: str) -> str:
    label = _safe_text(_render_value(f"query_list_v2.template_dsl.{domain}.table_label", ""))
    if label:
        return label
    return {
        "case": "案件项目总库",
        "contracts": "合同管理表",
        "bidding": "招投标台账",
        "team_overview": "团队成员工作总览（只读）",
    }.get(domain, "")


def _resolve_field_value(fields: Mapping[str, Any], domain: str, field_name: str) -> str:
    if field_name == "title":
        left = _pick_first(fields, _field_keys("case", "title_left", ["委托人及联系方式", "委托人"]))
        right = _pick_first(fields, _field_keys("case", "title_right", ["对方当事人"]))
        cause = _pick_first(fields, _field_keys("case", "cause", ["案由"]))
        case_no = _pick_first(fields, _field_keys("case", "case_no", ["案号", "项目ID"]))
        title = " vs ".join([part for part in [left, right] if part])
        if cause:
            title = f"{title} | {cause}" if title else cause
        return title or case_no
    keys = _field_keys(domain, field_name, _field_mapping_sources(domain, field_name) + [field_name])
    return _pick_first(fields, keys)


def _resolve_field_value_by_spec(fields: Mapping[str, Any], domain: str, spec: Mapping[str, Any]) -> str:
    literal = spec.get("literal")
    literal_text = _safe_text(literal)
    if literal_text:
        return literal_text

    source_keys_raw = spec.get("source_keys")
    source_keys: list[str] = []
    if isinstance(source_keys_raw, list):
        source_keys = [str(item).strip() for item in source_keys_raw if str(item).strip()]
    source_key = _safe_text(spec.get("source_key"))
    if source_key:
        source_keys.insert(0, source_key)
    if source_keys:
        return _pick_first(fields, source_keys)

    name = _safe_text(spec.get("name") or spec.get("key"))
    if not name:
        return ""
    return _resolve_field_value(fields, domain, name)


def _render_fields_by_dsl(
    fields: Mapping[str, Any],
    domain: str,
    specs: list[Mapping[str, Any]],
    detail_mode: bool,
) -> list[str]:
    lines: list[str] = []
    if not specs:
        return lines
    for spec in specs:
        if not isinstance(spec, Mapping):
            continue
        name = _safe_text(spec.get("name") or spec.get("key"))
        if not name:
            continue
        label = _safe_text(spec.get("label"))
        fmt = _safe_text(spec.get("format") or "plain").lower()
        show_empty = _safe_bool(spec.get("show_empty"), detail_mode)

        raw_value = _resolve_field_value_by_spec(fields, domain, spec)
        value = _safe_text(raw_value)

        if fmt in {"date_status", "date_status_badge"}:
            if value:
                status_name = _safe_text(spec.get("status_field") or "status")
                status_text = _resolve_field_value(fields, domain, status_name)
                symbol = _date_status_symbol(value, status_text)
                text = f"{symbol} {value}".strip()
            else:
                text = ""
        elif fmt in {"urgency", "urgency_badge"}:
            if value:
                symbol = _urgency_symbol(value)
                text = f"{symbol} {value}".strip()
            else:
                text = ""
        elif fmt in {"date_countdown", "date_countdown_short", "date_expiry_check"}:
            text = _format_date_countdown(value)
        elif fmt in {"multiline", "multi_line", "case_no_multiline"}:
            text = _format_multiline_text(value)
        elif fmt in {"progress_timeline", "timeline"}:
            text = _format_progress_timeline(value)
        elif fmt in {"person_struct", "judge_struct"}:
            text = _format_person_struct_text(value)
        elif fmt == "currency":
            text = _format_currency(value)
        elif fmt == "composite":
            text = _render_composite_template(spec=spec, fields=fields, domain=domain)
        else:
            text = value

        if not text:
            if not show_empty:
                continue
            text = "—"

        if label:
            lines.append(f"- {label}: {text}")
        else:
            lines.append(f"- {text}")
    return lines


def _render_composite_template(spec: Mapping[str, Any], fields: Mapping[str, Any], domain: str) -> str:
    template = _safe_text(spec.get("template"))
    fallback = _safe_text(spec.get("fallback"))
    if not template:
        return ""

    vars_found = re.findall(r"\{([^{}]+)\}", template)
    rendered = template
    has_non_empty = False
    for var_name in vars_found:
        value = _resolve_field_value(fields, domain, var_name)
        if value:
            has_non_empty = True
        rendered = rendered.replace("{" + var_name + "}", value)
    rendered = re.sub(r"\s+", " ", rendered).strip(" |，,")
    if rendered and has_non_empty:
        return rendered

    if not fallback:
        return ""
    fallback_rendered = fallback
    vars_found = re.findall(r"\{([^{}]+)\}", fallback)
    for var_name in vars_found:
        fallback_rendered = fallback_rendered.replace("{" + var_name + "}", _resolve_field_value(fields, domain, var_name))
    return re.sub(r"\s+", " ", fallback_rendered).strip(" |，,")


def _format_currency(value: str) -> str:
    text = _safe_text(value)
    if not text:
        return "—"
    normalized = text.replace("¥", "").replace(",", "").strip()
    try:
        return f"¥{float(normalized):,.2f}"
    except ValueError:
        return text


def _format_multiline_text(value: str) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    parts = [item.strip() for item in re.split(r"[\n；;|]", text) if item.strip()]
    if len(parts) <= 1:
        return text
    return " / ".join(parts)


def _format_progress_timeline(value: str) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    chunks = [item.strip() for item in re.split(r"[\n；;]", text) if item.strip()]
    if len(chunks) <= 1:
        return text
    return " -> ".join(chunks)


def _format_person_struct_text(value: str) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    text = re.sub(r"[，,]", " / ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _format_date_countdown(value: str) -> str:
    text = _safe_text(value)
    if not text:
        return "—"
    base = text
    for separator in ("T", " "):
        if separator in text:
            base = text.split(separator, 1)[0]
            break
    try:
        target = date.fromisoformat(base)
    except ValueError:
        return text

    today = date.today()
    delta = (target - today).days
    if delta < 0:
        return f"{_OK_MARKER} {text}（已过期{abs(delta)}天）"
    if delta == 0:
        return f"{_OK_MARKER} {text}（今日）"
    if delta <= 3:
        return f"{_OK_MARKER} {text}（还有{delta}天）"
    if delta <= 7:
        return f"{_OK_MARKER} {text}（还有{delta}天）"
    return f"{_OK_MARKER} {text}（还有{delta}天）"


def _normalize_inline_text(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ，,、")


def _split_lines(value: Any, separators: str = r"[\n；;]+") -> list[str]:
    text = _safe_text(value)
    if not text:
        return []
    parts = [item.strip(" ，,、") for item in re.split(separators, text) if item.strip(" ，,、")]
    return [part for part in parts if part]


def _split_case_no_lines(value: Any) -> list[str]:
    text = _safe_text(value)
    if not text:
        return []
    normalized = text.replace(" / ,", ",").replace("/,", ",").replace(" / ", "，")
    if "\n" in normalized:
        parts = [item.strip(" ，,") for item in normalized.splitlines() if item.strip(" ，,")]
    else:
        parts = [item.strip(" ，,") for item in re.split(r"[；;，,]+", normalized) if item.strip(" ，,")]
    if not parts:
        return []
    return parts


def _split_judge_lines(value: Any) -> list[str]:
    text = _safe_text(value)
    if not text:
        return []
    normalized = text.replace(" / ", "，")
    if "\n" in normalized:
        parts = [item.strip(" ，,") for item in normalized.splitlines() if item.strip(" ，,")]
    else:
        parts = [item.strip(" ，,") for item in re.split(r"[；;，,]+", normalized) if item.strip(" ，,")]
    return parts


def _format_multiline_block(lines: list[str], fallback: str = "—") -> str:
    if not lines:
        return fallback
    return "\n".join([f"  {line}" for line in lines])


def _parse_date_from_text(value: Any) -> date | None:
    text = _safe_text(value)
    if not text:
        return None
    matched = re.search(r"(\d{4})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})", text)
    if matched:
        try:
            return date(int(matched.group(1)), int(matched.group(2)), int(matched.group(3)))
        except ValueError:
            return None

    matched = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if matched:
        try:
            return date(int(matched.group(1)), int(matched.group(2)), int(matched.group(3)))
        except ValueError:
            return None
    return None


def _normalize_datetime_text(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return "—"
    matched = re.search(r"(\d{4})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})(?:[日号])?(?:\s*(\d{1,2})[:：](\d{1,2}))?", text)
    if not matched:
        return _normalize_inline_text(text) or "—"
    year, month, day = int(matched.group(1)), int(matched.group(2)), int(matched.group(3))
    hour = matched.group(4)
    minute = matched.group(5)
    date_part = f"{year:04d}-{month:02d}-{day:02d}"
    if hour is None or minute is None:
        return date_part
    return f"{date_part} {int(hour):02d}:{int(minute):02d}"


def _countdown_suffix(value: Any) -> str:
    target = _parse_date_from_text(value)
    if target is None:
        return ""
    delta = (target - date.today()).days
    if delta < 0:
        return f"（已过{abs(delta)}天）"
    if delta == 0:
        return "（今天）"
    return f"（还有{delta}天）"


def _split_progress_entries(value: Any) -> list[str]:
    text = _safe_text(value)
    if not text:
        return []
    normalized = text.replace(" -> ", "；").replace("\r", "\n")
    entries = _split_lines(normalized, separators=r"[\n；;]+")
    if len(entries) <= 1:
        entries = _split_lines(normalized, separators=r"(?=\d{4}[年\-])")
    cleaned = [item.lstrip("，,、 ") for item in entries if item.lstrip("，,、 ")]
    return cleaned


def _format_todo_list(value: Any) -> str:
    entries = _split_lines(value)
    if not entries:
        text = _normalize_inline_text(value)
        if not text:
            return "• —"
        entries = [text]
    return "\n".join([f"• {item}" for item in entries])


def _format_progress_timeline_block(value: Any, limit: int = 3) -> str:
    entries = _split_progress_entries(value)
    if not entries:
        return "—"
    latest = list(reversed(entries))[:max(1, limit)]
    return "\n".join(latest)


def _format_urgency_badge(value: Any) -> str:
    text = _normalize_inline_text(value)
    if not text or text == "—":
        return "—"
    lowered = text.lower()
    if any(token in lowered for token in ("重要紧急", "紧急", "p0", "p1", "high", "critical")):
        return "🔴 重要紧急"
    if any(token in lowered for token in ("中", "一般", "p2", "medium")):
        return "🟡 一般"
    return f"🔵 {text}"


def _format_deadline_status(value: Any) -> str:
    normalized = _normalize_datetime_text(value)
    if normalized == "—":
        return "—"

    target = _parse_date_from_text(value)
    if target is None:
        return normalized
    delta_days = (target - date.today()).days
    if delta_days < 0:
        return f"{normalized} ❌已过期"
    if delta_days == 0:
        return f"{normalized} ⏰今天"
    return f"{normalized} ⏰还有{delta_days}天"


def _format_progress_preview(value: Any, limit: int = 3) -> tuple[str, str]:
    entries = _split_progress_entries(value)
    if not entries:
        return "• —", ""
    preview = entries[: max(1, limit)]
    more_hint = "... 展开查看全部" if len(entries) > len(preview) else ""
    return "\n".join([f"• {item}" for item in preview]), more_hint


def _render_case_t1_template_values(record: Mapping[str, Any]) -> dict[str, str]:
    project_id = _normalize_inline_text(record.get("project_id")) or "—"
    project_type = _normalize_inline_text(record.get("project_type")) or "—"
    case_category = _normalize_inline_text(record.get("case_category")) or "—"
    cause = _normalize_inline_text(record.get("cause")) or "—"
    client = _normalize_inline_text(record.get("client")) or "—"
    opponent = _normalize_inline_text(record.get("opponent")) or "—"
    contact_person = _normalize_inline_text(record.get("contact_person"))
    contact_info = _normalize_inline_text(record.get("contact_info"))
    if contact_person and contact_info:
        contact_line = f"{contact_person} | {contact_info}"
    else:
        contact_line = contact_person or contact_info or "—"

    case_no_lines = _split_case_no_lines(record.get("case_no"))
    judge_lines = _split_judge_lines(record.get("judge"))
    stage = _normalize_inline_text(record.get("stage")) or "—"
    court = _normalize_inline_text(record.get("court")) or "—"
    courtroom = _normalize_inline_text(record.get("courtroom")) or ""
    if court != "—" and courtroom:
        court = f"{court}{courtroom}"
    owner = _normalize_inline_text(record.get("owner")) or "—"
    co_owner = _normalize_inline_text(record.get("co_owner")) or "—"
    urgency_badge = _format_urgency_badge(record.get("urgency"))
    status = _normalize_inline_text(record.get("case_status") or record.get("status")) or "—"

    hearing_date = _normalize_datetime_text(record.get("hearing_date"))
    hearing_suffix = _countdown_suffix(record.get("hearing_date"))
    jurisdiction_deadline = _normalize_datetime_text(record.get("jurisdiction_deadline"))
    evidence_deadline = _normalize_datetime_text(record.get("evidence_deadline"))
    seizure_expiry = _normalize_datetime_text(record.get("seizure_expiry"))
    counterclaim_deadline = _normalize_datetime_text(record.get("counterclaim_deadline"))
    appeal_deadline = _normalize_datetime_text(record.get("appeal_deadline"))

    progress_preview, progress_more_hint = _format_progress_preview(record.get("progress"))
    progress_preview_block = progress_preview if not progress_more_hint else f"{progress_preview}\n{progress_more_hint}"

    project_type_text = "" if project_type == "—" else project_type
    cause_text = "" if cause == "—" else cause
    case_category_text = "" if case_category == "—" else case_category
    if cause_text and case_category_text:
        project_summary = f"【{project_type_text}】{cause_text} | {case_category_text}" if project_type_text else f"{cause_text} | {case_category_text}"
    elif cause_text:
        project_summary = f"【{project_type_text}】{cause_text}" if project_type_text else cause_text
    elif case_category_text:
        project_summary = f"【{project_type_text}】{case_category_text}" if project_type_text else case_category_text
    elif project_type_text:
        project_summary = f"【{project_type_text}】—"
    else:
        project_summary = "—"

    origin_raw = record.get("_origin_record")
    origin = origin_raw if isinstance(origin_raw, Mapping) else {}
    record_url = _safe_text(origin.get("record_url"))
    record_id = _safe_text(origin.get("record_id"))
    linked_contract = _normalize_inline_text(record.get("linked_contract")) or "—"
    linked_contract_url = _normalize_inline_text(record.get("linked_contract_url"))
    if not linked_contract_url and linked_contract.startswith(("http://", "https://")):
        linked_contract_url = linked_contract
    btn_contract_url = linked_contract_url or record_url or "https://open.feishu.cn"

    return {
        "project_id": project_id,
        "project_type": project_type,
        "case_category": case_category,
        "cause": cause,
        "project_summary": project_summary,
        "client": client,
        "opponent": opponent,
        "contact_line": contact_line,
        "case_no_block": _format_multiline_block(case_no_lines),
        "case_no_display": "\n".join(case_no_lines) if case_no_lines else "—",
        "court": court,
        "stage": stage,
        "judge_block": _format_multiline_block(judge_lines),
        "judge_display": "\n".join(judge_lines) if judge_lines else "—",
        "owner": owner,
        "co_owner": co_owner,
        "hearing_date": hearing_date,
        "hearing_date_countdown": hearing_suffix,
        "hearing_date_status": _format_deadline_status(record.get("hearing_date")),
        "jurisdiction_deadline": jurisdiction_deadline,
        "jurisdiction_deadline_status": _format_deadline_status(record.get("jurisdiction_deadline")),
        "evidence_deadline": evidence_deadline,
        "evidence_deadline_status": _format_deadline_status(record.get("evidence_deadline")),
        "seizure_expiry": seizure_expiry,
        "seizure_expiry_status": _format_deadline_status(record.get("seizure_expiry")),
        "counterclaim_deadline": counterclaim_deadline,
        "counterclaim_deadline_status": _format_deadline_status(record.get("counterclaim_deadline")),
        "appeal_deadline": appeal_deadline,
        "appeal_deadline_status": _format_deadline_status(record.get("appeal_deadline")),
        "urgency_badge": urgency_badge,
        "status": status,
        "todo_list": _format_todo_list(record.get("todo")),
        "progress_timeline": _format_progress_timeline_block(record.get("progress")),
        "progress_preview": progress_preview,
        "progress_preview_block": progress_preview_block,
        "progress_more_hint": progress_more_hint,
        "remark": _normalize_inline_text(record.get("remark")) or "—",
        "linked_contract": linked_contract,
        "linked_task": _normalize_inline_text(record.get("linked_task")) or "—",
        "btn_contract_url": btn_contract_url,
        "record_id": record_id,
        "table_type": "case",
    }


def _with_countdown_text(value: Any) -> str:
    normalized = _normalize_datetime_text(value)
    if normalized == "—":
        return normalized
    suffix = _countdown_suffix(value)
    return f"{normalized} {suffix}".strip()


def _contract_payment_status_markdown(value: str) -> str:
    text = _normalize_inline_text(value) or "—"
    lowered = text.lower()
    if text == "—":
        return "💳 开票付款状态：—"
    if "未" in text or "none" in lowered:
        return f"<font color='red'>💳 开票付款状态：❌ {text}</font>"
    if "部分" in text or "待" in text or "partial" in lowered:
        return f"<font color='orange'>💳 开票付款状态：⏳ {text}</font>"
    return f"<font color='green'>💳 开票付款状态：✅ {text}</font>"


def _contract_end_date_markdown(value: Any) -> str:
    normalized = _normalize_datetime_text(value)
    if normalized == "—":
        return "📅 结束日期：—"
    target = _parse_date_from_text(value)
    if target is None:
        return f"📅 结束日期：{normalized}"
    delta = (target - date.today()).days
    if delta < 0:
        return f"<font color='red'>📅 结束日期：{normalized} ⚠️已到期</font>"
    return f"📅 结束日期：{normalized}"


def _contract_seal_status_markdown(value: str) -> str:
    text = _normalize_inline_text(value) or "—"
    lowered = text.lower()
    if text == "—":
        return "📎 盖章状态：—"
    if "待" in text or "pending" in lowered:
        return f"<font color='orange'>📎 盖章状态：⏳ {text}</font>"
    if "已" in text or "done" in lowered:
        return f"<font color='green'>📎 盖章状态：✅ {text}</font>"
    return f"📎 盖章状态：{text}"


def _render_contract_t1_template_values(record: Mapping[str, Any]) -> dict[str, str]:
    contract_id = _normalize_inline_text(_resolve_field_value(record, "contracts", "id")) or "—"
    contract_name = _normalize_inline_text(_resolve_field_value(record, "contracts", "name")) or "—"
    party_a = _normalize_inline_text(_resolve_field_value(record, "contracts", "party_a")) or "—"
    party_b = _normalize_inline_text(_resolve_field_value(record, "contracts", "party_b")) or "—"
    amount_raw = _normalize_inline_text(_resolve_field_value(record, "contracts", "amount"))
    amount = _format_currency(amount_raw) if amount_raw else "—"
    status = _normalize_inline_text(_resolve_field_value(record, "contracts", "status")) or "—"
    origin_raw = record.get("_origin_record")
    origin = origin_raw if isinstance(origin_raw, Mapping) else {}
    record_url = _safe_text(origin.get("record_url"))

    payment_status = _normalize_inline_text(record.get("payment_status")) or "—"
    seal_status = _normalize_inline_text(record.get("seal_status")) or "—"
    linked_project = _normalize_inline_text(record.get("linked_project")) or "—"

    linked_case_url = _normalize_inline_text(record.get("linked_case_url")) or record_url or "https://open.feishu.cn"
    edit_contract_url = _normalize_inline_text(record.get("edit_contract_url")) or record_url or "https://open.feishu.cn"

    return {
        "contract_id": contract_id,
        "contract_type": _normalize_inline_text(record.get("contract_type")) or "—",
        "contract_name": contract_name,
        "client_name": _normalize_inline_text(record.get("client_name")) or "—",
        "party_a": party_a,
        "party_b": party_b,
        "owner": _normalize_inline_text(record.get("owner") or record.get("主办律师")) or "—",
        "amount": amount,
        "status": status,
        "payment_milestone": _normalize_inline_text(record.get("payment_milestone")) or "—",
        "payment_status": payment_status,
        "payment_status_markdown": _contract_payment_status_markdown(payment_status),
        "sign_date_with_countdown": _with_countdown_text(record.get("sign_date") or record.get("签约日期")),
        "start_date_with_countdown": _with_countdown_text(record.get("start_date") or record.get("合同开始日期")),
        "end_date_with_countdown": _with_countdown_text(record.get("end_date") or record.get("合同结束日期")),
        "end_date_markdown": _contract_end_date_markdown(record.get("end_date") or record.get("合同结束日期")),
        "seal_date_with_countdown": _with_countdown_text(record.get("seal_date") or record.get("盖章日期")),
        "seal_status": seal_status,
        "seal_status_markdown": _contract_seal_status_markdown(seal_status),
        "archive_location": _normalize_inline_text(record.get("archive_location")) or "—",
        "invoice": _normalize_inline_text(record.get("invoice")) or "—",
        "scan_copy": _normalize_inline_text(record.get("scan_copy")) or "—",
        "linked_project": linked_project,
        "btn_case_url": linked_case_url,
        "btn_edit_contract_url": edit_contract_url,
    }


def _render_bidding_t1_template_values(record: Mapping[str, Any]) -> dict[str, str]:
    project_name = _normalize_inline_text(record.get("project_name") or _resolve_field_value(record, "bidding", "name")) or "—"
    phase = _normalize_inline_text(record.get("phase") or _resolve_field_value(record, "bidding", "phase")) or "—"
    owner = _normalize_inline_text(record.get("owner") or _resolve_field_value(record, "bidding", "owner")) or "—"

    bid_amount_raw = _normalize_inline_text(record.get("bid_amount") or record.get("中标金额"))
    bid_amount = _format_currency(bid_amount_raw) if bid_amount_raw else "—"
    bidder_name = _normalize_inline_text(record.get("bidder_name") or record.get("招标方名称")) or "—"
    bid_result = _normalize_inline_text(record.get("bid_result") or record.get("是否中标")) or "待定"

    origin_raw = record.get("_origin_record")
    origin = origin_raw if isinstance(origin_raw, Mapping) else {}
    record_url = _safe_text(origin.get("record_url"))
    btn_project_url = _normalize_inline_text(record.get("project_url")) or record_url or "https://open.feishu.cn"
    btn_edit_bid_url = _normalize_inline_text(record.get("edit_bid_url")) or record_url or "https://open.feishu.cn"

    return {
        "bid_id": _normalize_inline_text(record.get("bid_id") or record.get("项目号")) or "—",
        "project_name": project_name,
        "bidder_name": bidder_name,
        "phase": phase,
        "owner": owner,
        "book_deadline_with_countdown": _with_countdown_text(
            record.get("book_deadline") or record.get("标书购买截止时间")
        ),
        "close_date_with_countdown": _with_countdown_text(
            record.get("close_date") or _resolve_field_value(record, "bidding", "due")
        ),
        "open_date_with_countdown": _with_countdown_text(record.get("open_date") or record.get("开标时间")),
        "deposit_deadline_with_countdown": _with_countdown_text(
            record.get("deposit_deadline") or record.get("保证金截止日期")
        ),
        "book_status": _normalize_inline_text(record.get("book_status") or record.get("标书领取状态")) or "—",
        "deposit_status": _normalize_inline_text(record.get("deposit_status") or record.get("保证金缴纳状态")) or "—",
        "doc_progress": _normalize_inline_text(record.get("doc_progress") or record.get("文件编制进度")) or "—",
        "book_type": _normalize_inline_text(record.get("book_type") or record.get("标书类型")) or "—",
        "bid_result": bid_result,
        "bid_amount": bid_amount,
        "remark": _normalize_inline_text(record.get("remark") or record.get("备注")) or "—",
        "btn_project_url": btn_project_url,
        "btn_edit_bid_url": btn_edit_bid_url,
    }


def _build_table_badge_text(table_name: str, table_id: str, style: str) -> str:
    if not table_name:
        return ""
    badge_template = _safe_text(
        _render_value("query_list_v2.texts.table_badge", "数据表: {table_name}{table_suffix} | 模板: {style}")
    )
    if not badge_template:
        return ""
    table_suffix = f" (ID: {table_id})" if table_id else ""
    try:
        return badge_template.format(
            table_name=table_name,
            table_id=table_id,
            table_suffix=table_suffix,
            style=style,
        )
    except Exception:
        return f"数据表: {table_name}{table_suffix} | 模板: {style}"


def _render_case_t2_template_values(record: Mapping[str, Any], index: int) -> dict[str, str]:
    case_no = _normalize_inline_text(record.get("case_no") or record.get("project_id")) or "—"
    client = _normalize_inline_text(record.get("client"))
    opponent = _normalize_inline_text(record.get("opponent"))
    cause = _normalize_inline_text(record.get("cause"))

    title_line = " vs ".join([part for part in [client, opponent] if part])
    if cause:
        title_line = f"{title_line} | {cause}" if title_line else cause
    if not title_line:
        title_line = case_no

    status = _normalize_inline_text(record.get("case_status") or record.get("status")) or "—"

    date_raw = ""
    for key in (
        "hearing_date",
        "date",
        "jurisdiction_deadline",
        "evidence_deadline",
        "seizure_expiry",
        "counterclaim_deadline",
        "appeal_deadline",
    ):
        candidate = _normalize_inline_text(record.get(key))
        if candidate:
            date_raw = candidate
            break
    date_text = _normalize_datetime_text(date_raw) if date_raw else "—"
    if date_text != "—":
        date_status = f"{_date_status_symbol(date_text, status)} {date_text}".strip()
    else:
        date_status = "—"

    owner = _normalize_inline_text(record.get("owner")) or "—"
    urgency_raw = _normalize_inline_text(record.get("urgency"))
    urgency = f"{_urgency_symbol(urgency_raw)} {urgency_raw}".strip() if urgency_raw else "—"

    return {
        "index": str(index),
        "title_line": title_line,
        "case_no": case_no,
        "status": status,
        "date_status": date_status,
        "owner": owner,
        "urgency": urgency,
    }


def _index_emoji(index: int) -> str:
    mapping = {
        1: "1️⃣",
        2: "2️⃣",
        3: "3️⃣",
        4: "4️⃣",
        5: "5️⃣",
        6: "6️⃣",
        7: "7️⃣",
        8: "8️⃣",
        9: "9️⃣",
        10: "🔟",
    }
    return mapping.get(index, f"{index}.")


def _render_t2_hearing_text(value: Any) -> str:
    parsed = _parse_date_from_text(value)
    if parsed is None:
        return "📅 无开庭安排"
    mmdd = parsed.strftime("%m-%d")
    delta_days = (parsed - date.today()).days
    if delta_days < 0:
        suffix = "❌已过期"
    elif delta_days == 0:
        suffix = "⏰今天"
    else:
        suffix = f"⏰{delta_days}天后"
    return f"📅 开庭：{mmdd} {suffix}"


def _render_t2_urgency_tag(value: Any) -> str:
    text = _normalize_inline_text(value)
    if not text or text == "—":
        text = "一般"
    lowered = text.lower()
    if "重要紧急" in text or ("重要" in text and "紧急" in text):
        return "<text_tag color='red'>重要紧急</text_tag>"
    if "重要不紧急" in text or "important" in lowered:
        return "<text_tag color='yellow'>重要不紧急</text_tag>"
    if "一般" in text or "medium" in lowered:
        return "<text_tag color='yellow'>一般</text_tag>"
    return f"<text_tag color='blue'>{text}</text_tag>"


def _render_case_t2_cardkit_values(record: Mapping[str, Any], index: int) -> dict[str, str]:
    client = _normalize_inline_text(record.get("client"))
    opponent = _normalize_inline_text(record.get("opponent"))
    title = " vs ".join([part for part in [client, opponent] if part])
    if not title:
        title = _normalize_inline_text(record.get("title")) or _normalize_inline_text(record.get("case_no")) or f"记录{index}"

    project_id = _normalize_inline_text(record.get("project_id")) or _normalize_inline_text(record.get("case_no")) or "—"
    category = _normalize_inline_text(record.get("case_category") or record.get("cause")) or "未分类"
    hearing_text = _render_t2_hearing_text(record.get("hearing_date"))
    owner = _normalize_inline_text(record.get("owner")) or "—"
    urgency_tag = _render_t2_urgency_tag(record.get("urgency"))
    status = _normalize_inline_text(record.get("case_status") or record.get("status")) or "—"

    origin_raw = record.get("_origin_record")
    origin = origin_raw if isinstance(origin_raw, Mapping) else {}
    detail_url = _safe_text(origin.get("record_url")) or "https://open.feishu.cn"

    return {
        "index_emoji": _index_emoji(index),
        "title_line": title,
        "project_id": project_id,
        "case_line": f"📋 {category} | {hearing_text}",
        "owner_status_line": f"👤 {owner} | {urgency_tag} | {status}",
        "detail_url": detail_url,
    }


def _render_case_t2_cardkit_layout(
    *,
    records: list[Mapping[str, Any]],
    style_cfg: Mapping[str, Any],
    title: str,
    count: int,
    shown_count: int,
    remaining: int,
    actions: Mapping[str, Any],
    table_name: str,
    table_id: str,
) -> dict[str, Any] | None:
    header_layout_file = _safe_text(style_cfg.get("list_header_layout_file"))
    item_layout_file = _safe_text(style_cfg.get("list_item_layout_file"))
    if not header_layout_file or not item_layout_file:
        return None

    values: dict[str, Any] = {
        "title": title,
        "total_count": str(count),
        "shown_count": str(shown_count),
        "table_name": table_name,
        "table_id": table_id,
    }
    elements = _load_layout_template_elements(header_layout_file, values)

    for index, record in enumerate(records, start=1):
        dsl_record = _build_dsl_record(record, "case")
        item_values = _render_case_t2_cardkit_values(dsl_record, index)
        item_elements = _load_layout_template_elements(item_layout_file, item_values)
        if item_elements:
            elements.extend(item_elements)

    next_page_raw = actions.get("next_page")
    next_page_value = _normalize_callback_value(
        next_page_raw if isinstance(next_page_raw, Mapping) else None,
        callback_action="query_list_next_page",
        table_type="case",
    )
    next_extra_raw = next_page_value.get("extra_data")
    next_extra: dict[str, Any] = dict(next_extra_raw) if isinstance(next_extra_raw, Mapping) else {}
    next_kind = _safe_text(next_page_value.get("kind") or next_extra.get("kind"))
    if remaining > 0 or next_kind == "no_more":
        next_text = _safe_text(_render_value("query_list_v2.actions.next_page", "下一页")) or "下一页"
        if remaining > 0:
            template = _safe_text(
                _render_value("query_list_v2.actions.next_page_with_remaining", "下一页（剩余 {remaining} 条）")
            )
            next_text = template.format(remaining=remaining)
        elements.append(
            {
                "tag": "button",
                "type": "primary_filled",
                "width": "fill",
                "margin": "8px 0px 0px 0px",
                "text": {
                    "tag": "plain_text",
                    "content": next_text,
                },
                "value": next_page_value,
            }
        )

    wrapper_file = _safe_text(style_cfg.get("wrapper_file"))
    wrapper_values = {
        "header_title": _safe_text(style_cfg.get("header_title")) or title,
        "table_name": table_name,
        "table_id": table_id,
        "style": "T2",
    }
    wrapper = _load_wrapper_template(wrapper_file, wrapper_values) if wrapper_file else {}

    return {
        "elements": elements,
        "wrapper": wrapper,
    }


def _render_contract_t2_template_values(record: Mapping[str, Any], index: int) -> dict[str, str]:
    contract_name = _normalize_inline_text(_resolve_field_value(record, "contracts", "name")) or "—"
    contract_id = _normalize_inline_text(_resolve_field_value(record, "contracts", "id")) or "—"
    status = _normalize_inline_text(_resolve_field_value(record, "contracts", "status")) or "—"
    amount_raw = _normalize_inline_text(_resolve_field_value(record, "contracts", "amount"))
    amount = _format_currency(amount_raw) if amount_raw else "—"
    owner = _normalize_inline_text(record.get("owner") or record.get("主办律师")) or "—"

    return {
        "index": str(index),
        "contract_name": contract_name,
        "contract_id": contract_id,
        "status": status,
        "amount": amount,
        "owner": owner,
    }


def _contract_payment_badge_text(value: Any) -> str:
    text = _normalize_inline_text(value) or "未开票未付款"
    lowered = text.lower()
    if "未" in text or "none" in lowered:
        return f"❌ {text}"
    if "部分" in text or "待" in text or "partial" in lowered:
        return f"⏳ {text}"
    return f"✅ {text}"


def _contract_seal_badge_text(value: Any) -> str:
    text = _normalize_inline_text(value) or "待盖章"
    lowered = text.lower()
    if "待" in text or "pending" in lowered:
        return f"⏳ {text}"
    if "已" in text or "done" in lowered:
        return f"✅ {text}"
    return text


def _render_contract_t2_cardkit_values(record: Mapping[str, Any], index: int) -> dict[str, str]:
    contract_id = _normalize_inline_text(_resolve_field_value(record, "contracts", "id")) or "—"
    contract_name = _normalize_inline_text(_resolve_field_value(record, "contracts", "name")) or "—"
    client_name = _normalize_inline_text(record.get("client_name") or _resolve_field_value(record, "contracts", "party_a")) or "—"

    amount_raw = _normalize_inline_text(_resolve_field_value(record, "contracts", "amount"))
    amount = _format_currency(amount_raw) if amount_raw else "—"
    payment_badge = _contract_payment_badge_text(record.get("payment_status"))

    start_date = _normalize_datetime_text(record.get("start_date") or record.get("sign_date"))
    end_date = _normalize_datetime_text(record.get("end_date"))
    date_line = f"{start_date} 至 {end_date}" if start_date != "—" or end_date != "—" else "—"
    seal_badge = _contract_seal_badge_text(record.get("seal_status"))
    linked_project = _normalize_inline_text(record.get("linked_project")) or "—"

    origin_raw = record.get("_origin_record")
    origin = origin_raw if isinstance(origin_raw, Mapping) else {}
    detail_url = _safe_text(origin.get("record_url")) or "https://open.feishu.cn"

    item_content = (
        f"**{_index_emoji(index)} {contract_id} | {contract_name}**\n"
        f"🏢 {client_name}\n"
        f"💰 {amount} | {payment_badge}\n"
        f"📅 {date_line} | {seal_badge}\n"
        f"🔗 {linked_project}"
    )

    return {
        "item_content": item_content,
        "detail_url": detail_url,
        "record_id": _safe_text(origin.get("record_id")),
        "table_type": "contracts",
    }


def _render_contract_t2_cardkit_layout(
    *,
    records: list[Mapping[str, Any]],
    style_cfg: Mapping[str, Any],
    count: int,
    shown_count: int,
    remaining: int,
    actions: Mapping[str, Any],
) -> dict[str, Any] | None:
    header_layout_file = _safe_text(style_cfg.get("list_header_layout_file"))
    item_layout_file = _safe_text(style_cfg.get("list_item_layout_file"))
    if not header_layout_file or not item_layout_file:
        return None

    elements = _load_layout_template_elements(
        header_layout_file,
        {
            "total_count": str(count),
            "shown_count": str(shown_count),
        },
    )

    for index, record in enumerate(records, start=1):
        dsl_record = _build_dsl_record(record, "contracts")
        values = _render_contract_t2_cardkit_values(dsl_record, index)
        item_elements = _load_layout_template_elements(item_layout_file, values)
        if item_elements:
            elements.extend(item_elements)

    next_page_raw = actions.get("next_page")
    next_page_value = _normalize_callback_value(
        next_page_raw if isinstance(next_page_raw, Mapping) else None,
        callback_action="query_list_next_page",
        table_type="contracts",
    )
    next_extra_raw = next_page_value.get("extra_data")
    next_extra: dict[str, Any] = dict(next_extra_raw) if isinstance(next_extra_raw, Mapping) else {}
    next_kind = _safe_text(next_page_value.get("kind") or next_extra.get("kind"))
    if remaining > 0 or next_kind == "no_more":
        next_text = _safe_text(_render_value("query_list_v2.actions.next_page", "下一页")) or "下一页"
        if remaining > 0:
            template = _safe_text(
                _render_value("query_list_v2.actions.next_page_with_remaining", "下一页（剩余 {remaining} 条）")
            )
            next_text = template.format(remaining=remaining)
        elements.append(
            {
                "tag": "button",
                "type": "primary_filled",
                "width": "fill",
                "margin": "8px 0px 0px 0px",
                "text": {
                    "tag": "plain_text",
                    "content": next_text,
                },
                "value": next_page_value,
            }
        )

    wrapper_file = _safe_text(style_cfg.get("wrapper_file"))
    wrapper_values = {
        "header_title": _safe_text(style_cfg.get("header_title")) or "合同查询结果",
        "style": "HT-T2",
    }
    wrapper = _load_wrapper_template(wrapper_file, wrapper_values) if wrapper_file else {}
    return {
        "elements": elements,
        "wrapper": wrapper,
    }


def _render_bidding_t2_template_values(record: Mapping[str, Any], index: int) -> dict[str, str]:
    project_name = _normalize_inline_text(record.get("project_name") or _resolve_field_value(record, "bidding", "name")) or "—"
    phase = _normalize_inline_text(record.get("phase") or _resolve_field_value(record, "bidding", "phase")) or "—"
    due_raw = _normalize_inline_text(_resolve_field_value(record, "bidding", "due"))
    due_text = _normalize_datetime_text(due_raw) if due_raw else "—"
    if due_text != "—":
        due_status = f"{_date_status_symbol(due_text, phase)} {due_text}".strip()
    else:
        due_status = "—"
    owner = _normalize_inline_text(record.get("owner") or _resolve_field_value(record, "bidding", "owner")) or "—"

    return {
        "index": str(index),
        "project_name": project_name,
        "phase": phase,
        "due_status": due_status,
        "owner": owner,
    }


def _render_bidding_due_with_countdown(value: Any) -> str:
    normalized = _normalize_datetime_text(value)
    if normalized == "—":
        return "无关键节点"
    target = _parse_date_from_text(value)
    if target is None:
        return normalized
    mmdd = target.strftime("%m-%d")
    delta = (target - date.today()).days
    if delta < 0:
        return f"{mmdd} ❌已过期"
    if delta == 0:
        return f"{mmdd} ⏰今天"
    return f"{mmdd} ⏰{delta}天后"


def _bidding_result_badge(value: Any) -> str:
    text = _normalize_inline_text(value) or "待定"
    lowered = text.lower()
    if "中标" in text and "未" not in text:
        return f"✅ {text}"
    if "未" in text or "lost" in lowered:
        return f"❌ {text}"
    return f"⏳ {text}"


def _render_bidding_t2_cardkit_values(record: Mapping[str, Any], index: int) -> dict[str, str]:
    bid_id = _normalize_inline_text(record.get("bid_id") or record.get("项目号")) or "—"
    project_name = _normalize_inline_text(record.get("project_name") or _resolve_field_value(record, "bidding", "name")) or "—"
    phase = _normalize_inline_text(record.get("phase") or _resolve_field_value(record, "bidding", "phase")) or "—"
    bidder_name = _normalize_inline_text(record.get("bidder_name") or record.get("招标方名称")) or "—"
    due_raw = _normalize_inline_text(_resolve_field_value(record, "bidding", "due"))
    due_line = _render_bidding_due_with_countdown(due_raw)
    owner = _normalize_inline_text(record.get("owner") or _resolve_field_value(record, "bidding", "owner")) or "—"
    result_badge = _bidding_result_badge(record.get("bid_result"))

    origin_raw = record.get("_origin_record")
    origin = origin_raw if isinstance(origin_raw, Mapping) else {}
    detail_url = _safe_text(origin.get("record_url")) or "https://open.feishu.cn"

    item_content = (
        f"**{_index_emoji(index)} {project_name} | {phase}**\n"
        f"🔖 {bid_id}\n"
        f"🏢 {bidder_name} | 📅 {due_line}\n"
        f"👤 {owner} | {result_badge}"
    )
    return {
        "item_content": item_content,
        "detail_url": detail_url,
    }


def _render_bidding_t2_cardkit_layout(
    *,
    records: list[Mapping[str, Any]],
    style_cfg: Mapping[str, Any],
    count: int,
    shown_count: int,
    remaining: int,
    actions: Mapping[str, Any],
) -> dict[str, Any] | None:
    header_layout_file = _safe_text(style_cfg.get("list_header_layout_file"))
    item_layout_file = _safe_text(style_cfg.get("list_item_layout_file"))
    if not header_layout_file or not item_layout_file:
        return None

    elements = _load_layout_template_elements(
        header_layout_file,
        {
            "total_count": str(count),
            "shown_count": str(shown_count),
        },
    )

    for index, record in enumerate(records, start=1):
        dsl_record = _build_dsl_record(record, "bidding")
        values = _render_bidding_t2_cardkit_values(dsl_record, index)
        item_elements = _load_layout_template_elements(item_layout_file, values)
        if item_elements:
            elements.extend(item_elements)

    next_page_raw = actions.get("next_page")
    next_page_value = _normalize_callback_value(
        next_page_raw if isinstance(next_page_raw, Mapping) else None,
        callback_action="query_list_next_page",
        table_type="bidding",
    )
    next_extra_raw = next_page_value.get("extra_data")
    next_extra: dict[str, Any] = dict(next_extra_raw) if isinstance(next_extra_raw, Mapping) else {}
    next_kind = _safe_text(next_page_value.get("kind") or next_extra.get("kind"))
    if remaining > 0 or next_kind == "no_more":
        next_text = _safe_text(_render_value("query_list_v2.actions.next_page", "下一页")) or "下一页"
        if remaining > 0:
            template = _safe_text(
                _render_value("query_list_v2.actions.next_page_with_remaining", "下一页（剩余 {remaining} 条）")
            )
            next_text = template.format(remaining=remaining)
        elements.append(
            {
                "tag": "button",
                "type": "primary_filled",
                "width": "fill",
                "margin": "8px 0px 0px 0px",
                "text": {
                    "tag": "plain_text",
                    "content": next_text,
                },
                "value": next_page_value,
            }
        )

    wrapper_file = _safe_text(style_cfg.get("wrapper_file"))
    wrapper_values = {
        "header_title": _safe_text(style_cfg.get("header_title")) or "招投标查询结果",
        "style": "ZB-T2",
    }
    wrapper = _load_wrapper_template(wrapper_file, wrapper_values) if wrapper_file else {}
    return {
        "elements": elements,
        "wrapper": wrapper,
    }


def _case_focus_template_family(style: str) -> str:
    normalized = style.upper()
    if normalized in _CASE_T3_STYLES:
        return "T3"
    if normalized in _CASE_T5_STYLES:
        return "T5"
    return ""


def _resolve_case_focus_variant(style: str) -> str:
    normalized = style.upper()
    if normalized in _CASE_T3_STYLES:
        if normalized == "T3B":
            return "t3b"
        if normalized == "T3C":
            return "t3c"
        return "t3a"
    if normalized in _CASE_T5_STYLES:
        if normalized == "T5B":
            return "t5b"
        if normalized == "T5C":
            return "t5c"
        return "t5a"
    return ""


def _case_brief_title_line(record: Mapping[str, Any], index: int) -> str:
    case_no = _normalize_inline_text(record.get("case_no") or record.get("project_id"))
    client = _normalize_inline_text(record.get("client"))
    opponent = _normalize_inline_text(record.get("opponent"))
    cause = _normalize_inline_text(record.get("cause"))
    party = " vs ".join([part for part in [client, opponent] if part])
    title = f"{party} | {cause}" if party and cause else (party or cause)
    if case_no and title:
        return f"{case_no} | {title}"
    return case_no or title or f"记录{index}"


def _pick_case_t3_date_value(record: Mapping[str, Any], variant: str) -> tuple[str, str]:
    if variant == "t3b":
        candidates = [
            ("jurisdiction_deadline", "管辖权异议截止"),
            ("evidence_deadline", "举证截止"),
            ("seizure_expiry", "查封到期"),
            ("counterclaim_deadline", "反诉截止"),
            ("appeal_deadline", "上诉截止"),
            ("date", "关键日期"),
            ("hearing_date", "开庭日"),
        ]
    else:
        candidates = [
            ("hearing_date", "开庭日"),
            ("date", "关键日期"),
            ("jurisdiction_deadline", "管辖权异议截止"),
            ("evidence_deadline", "举证截止"),
            ("seizure_expiry", "查封到期"),
            ("counterclaim_deadline", "反诉截止"),
            ("appeal_deadline", "上诉截止"),
        ]
    for key, label in candidates:
        raw = _normalize_inline_text(record.get(key))
        if raw:
            return raw, label
    return "", ""


def _date_bucket_label(value: str) -> str:
    target = _parse_date_from_text(value)
    if target is None:
        return "未标注日期"
    delta_days = (target - date.today()).days
    if delta_days < 0:
        return "已过期"
    if delta_days == 0:
        return "今天"
    if delta_days <= 3:
        return "3天内"
    if delta_days <= 7:
        return "7天内"
    return "7天后"


def _t3_variant_date_specs(variant: str) -> list[tuple[str, str]]:
    if variant == "t3b":
        return [
            ("管辖权异议截止", "jurisdiction_deadline"),
            ("举证截止", "evidence_deadline"),
            ("查封到期", "seizure_expiry"),
            ("反诉截止", "counterclaim_deadline"),
            ("上诉截止", "appeal_deadline"),
        ]
    if variant == "t3c":
        return [
            ("开庭", "hearing_date"),
            ("管辖权异议截止", "jurisdiction_deadline"),
            ("举证截止", "evidence_deadline"),
            ("查封到期", "seizure_expiry"),
            ("反诉截止", "counterclaim_deadline"),
            ("上诉截止", "appeal_deadline"),
        ]
    return [("开庭", "hearing_date")]


def _t3_header_summary(variant: str) -> str:
    if variant == "t3b":
        return "以下为截止日期提醒，请优先处理临近与已过期节点。"
    if variant == "t3c":
        return "以下为特定案件关键日期提醒。"
    return "以下为开庭日期提醒。"


def _build_t3_entry_line(record: Mapping[str, Any], label: str, key: str) -> dict[str, Any] | None:
    raw_value = record.get(key)
    normalized = _normalize_datetime_text(raw_value)
    if normalized == "—":
        return None

    target = _parse_date_from_text(raw_value)
    if target is None:
        return None
    delta_days = (target - date.today()).days

    if delta_days < 0:
        headline = f"❌ {label}：{normalized}（已过期{abs(delta_days)}天）"
    elif delta_days == 0:
        headline = f"🚨 {label}：{normalized}（今日到期）"
    else:
        headline = f"📅 {label}：{normalized}（还有{delta_days}天）"

    project_id = _normalize_inline_text(record.get("project_id") or record.get("case_no")) or "—"
    client = _normalize_inline_text(record.get("client"))
    opponent = _normalize_inline_text(record.get("opponent"))
    party = " vs ".join([part for part in [client, opponent] if part])
    line2 = f"{project_id} | {party}" if party else project_id

    owner = _normalize_inline_text(record.get("owner"))
    court = _normalize_inline_text(record.get("court"))
    if owner and court:
        line3 = f"👤 {owner} | ⚖️ {court}"
    elif owner:
        line3 = f"👤 {owner}"
    elif court:
        line3 = f"⚖️ {court}"
    else:
        line3 = ""

    if delta_days <= 0:
        bucket = "overdue"
    elif delta_days <= 7:
        bucket = "within_7"
    elif delta_days <= 30:
        bucket = "within_30"
    else:
        bucket = "later"

    return {
        "bucket": bucket,
        "headline": headline,
        "line2": line2,
        "line3": line3,
    }


def _render_t3_entry_card(entry: Mapping[str, Any], background_style: str) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": _safe_text(entry.get("headline"))},
        {"tag": "markdown", "content": _safe_text(entry.get("line2"))},
    ]
    line3 = _safe_text(entry.get("line3"))
    if line3:
        elements.append({"tag": "markdown", "content": line3})

    return {
        "tag": "column_set",
        "flex_mode": "stretch",
        "horizontal_spacing": "8px",
        "margin": "0px 0px 8px 0px",
        "columns": [
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "background_style": background_style,
                "padding": "12px",
                "vertical_spacing": "4px",
                "elements": elements,
            }
        ],
    }


def _render_case_t3_focus_layout(
    *,
    records: list[Mapping[str, Any]],
    style: str,
    style_cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    variant = _resolve_case_focus_variant(style)
    date_specs = _t3_variant_date_specs(variant)
    dsl_records = [_build_dsl_record(record, "case") for record in records]

    overdue_entries: list[dict[str, Any]] = []
    within7_entries: list[dict[str, Any]] = []
    within30_entries: list[dict[str, Any]] = []

    for record in dsl_records:
        for label, key in date_specs:
            entry = _build_t3_entry_line(record, label, key)
            if not isinstance(entry, Mapping):
                continue
            bucket = _safe_text(entry.get("bucket"))
            payload = dict(entry)
            if bucket == "overdue":
                overdue_entries.append(payload)
            elif bucket == "within_7":
                within7_entries.append(payload)
            elif bucket == "within_30":
                within30_entries.append(payload)

    header_summary = _safe_text(_render_value("query_list_v2.texts.t3_header_summary", "")) or _t3_header_summary(variant)
    base_layout_file = _safe_text(style_cfg.get("list_layout_file"))
    if base_layout_file:
        elements = _load_layout_template_elements(base_layout_file, {"header_summary": header_summary})
    else:
        elements = [
            _markdown(f"### {header_summary}"),
            {"tag": "hr", "margin": "8px 0px 8px 0px"},
        ]

    elements.append(_markdown("🚨 **已过期 / 今日到期**"))
    if overdue_entries:
        for entry in overdue_entries:
            elements.append(_render_t3_entry_card(entry, "red-50"))
    else:
        elements.append(_markdown("- 暂无"))

    elements.append(_markdown("⏰ **未来7天**"))
    if within7_entries:
        for entry in within7_entries:
            elements.append(_render_t3_entry_card(entry, "yellow-50"))
    else:
        elements.append(_markdown("- 暂无"))

    if within30_entries:
        elements.append(_markdown("📆 **未来30天**"))
        for entry in within30_entries:
            elements.append(_render_t3_entry_card(entry, "orange-50"))

    elements.append({"tag": "hr", "margin": "8px 0px 8px 0px"})
    stats_summary = (
        f"📊 统计：<font color='red'>{len(overdue_entries)}项已过期</font>"
        f" | <font color='yellow'>{len(within7_entries)}项7天内</font>"
        f" | {len(within30_entries)}项30天内"
    )
    elements.append(_markdown(stats_summary))

    wrapper_file = _safe_text(style_cfg.get("wrapper_file"))
    wrapper_values = {
        "header_title": _safe_text(style_cfg.get("header_title")) or "重要日期提醒",
        "style": style,
    }
    wrapper = _load_wrapper_template(wrapper_file, wrapper_values) if wrapper_file else {}
    return {
        "elements": elements,
        "wrapper": wrapper,
    }


def _render_case_t5_focus_layout(
    *,
    records: list[Mapping[str, Any]],
    style: str,
    style_cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    variant = _resolve_case_focus_variant(style)
    dsl_records = [_build_dsl_record(record, "case") for record in records]
    values = _render_case_t5_template_values(dsl_records, variant)

    layout_file = _safe_text(style_cfg.get("list_layout_file"))
    if layout_file:
        elements = _load_layout_template_elements(layout_file, values)
    else:
        elements = [
            _markdown(f"### {_safe_text(values.get('header_summary'))}"),
            {"tag": "hr", "margin": "8px 0px 8px 0px"},
            _markdown(_safe_text(values.get("content"))),
            {"tag": "hr", "margin": "8px 0px 8px 0px"},
            _markdown(_safe_text(values.get("stats_summary"))),
        ]

    wrapper_file = _safe_text(style_cfg.get("wrapper_file"))
    wrapper_values = {
        "header_title": _safe_text(style_cfg.get("header_title")) or "待办事项与案件进展",
        "style": style,
    }
    wrapper = _load_wrapper_template(wrapper_file, wrapper_values) if wrapper_file else {}
    return {
        "elements": elements,
        "wrapper": wrapper,
    }


def _render_case_t3_template_values(records: Sequence[Mapping[str, Any]], variant: str) -> dict[str, str]:
    if variant == "t3c":
        date_fields = [
            ("hearing_date", "开庭日"),
            ("jurisdiction_deadline", "管辖权异议截止日"),
            ("evidence_deadline", "举证截止日"),
            ("seizure_expiry", "查封到期日"),
            ("counterclaim_deadline", "反诉截止日"),
            ("appeal_deadline", "上诉截止日"),
        ]
        blocks: list[str] = []
        date_hits = 0
        for index, record in enumerate(records, start=1):
            lines = [f"**{_case_brief_title_line(record, index)}**"]
            for key, label in date_fields:
                raw = _normalize_inline_text(record.get(key))
                normalized = _normalize_datetime_text(raw) if raw else "—"
                if normalized != "—":
                    date_hits += 1
                lines.append(f"- {label}: {normalized}")
            blocks.append("\n".join(lines))
        return {
            "header_summary": "特定案件日期聚焦",
            "bucket_content": "\n\n".join(blocks) if blocks else "- 暂无日期信息",
            "stats_summary": f"案件 {len(records)} 条，日期命中 {date_hits} 项",
        }

    bucket_order = ["已过期", "今天", "3天内", "7天内", "7天后", "未标注日期"]
    bucket_lines: dict[str, list[str]] = {name: [] for name in bucket_order}
    overdue_count = 0
    today_count = 0
    near_count = 0

    for index, record in enumerate(records, start=1):
        raw_date, date_label = _pick_case_t3_date_value(record, variant)
        bucket = _date_bucket_label(raw_date)
        if bucket == "已过期":
            overdue_count += 1
        elif bucket == "今天":
            today_count += 1
        elif bucket == "3天内":
            near_count += 1

        date_text = _normalize_datetime_text(raw_date) if raw_date else "—"
        status = _normalize_inline_text(record.get("case_status") or record.get("status")) or "—"
        label_text = date_label or "关键日期"
        bucket_lines.setdefault(bucket, []).append(
            f"- {_case_brief_title_line(record, index)} | {label_text}: {date_text} | 状态: {status}"
        )

    sections: list[str] = []
    for bucket_name in bucket_order:
        lines = bucket_lines.get(bucket_name) or []
        if not lines:
            continue
        sections.append(f"**{bucket_name}（{len(lines)}）**\n" + "\n".join(lines))

    return {
        "header_summary": "开庭日聚焦" if variant == "t3a" else "截止日聚焦",
        "bucket_content": "\n\n".join(sections) if sections else "- 暂无日期信息",
        "stats_summary": f"今天 {today_count} 条，3天内 {near_count} 条，已过期 {overdue_count} 条",
    }


def _render_case_t5_template_values(records: Sequence[Mapping[str, Any]], variant: str) -> dict[str, str]:
    if variant == "t5b":
        lines: list[str] = []
        progress_count = 0
        for index, record in enumerate(records, start=1):
            progress_entries = _split_progress_entries(record.get("progress"))
            latest_progress = progress_entries[-1] if progress_entries else "—"
            if progress_entries:
                progress_count += 1
            lines.append(f"- {_case_brief_title_line(record, index)}\\n  最新进展: {latest_progress}")
        return {
            "header_summary": "进展时间线",
            "content": "\n".join(lines) if lines else "- 暂无进展",
            "stats_summary": f"有进展 {progress_count}/{len(records)}",
        }

    if variant == "t5c":
        grouped: dict[str, list[str]] = {}
        for index, record in enumerate(records, start=1):
            status = _normalize_inline_text(record.get("case_status") or record.get("status")) or "未标注状态"
            grouped.setdefault(status, []).append(f"- {_case_brief_title_line(record, index)}")

        sections: list[str] = []
        sorted_groups = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
        for status, items in sorted_groups:
            sections.append(f"**{status}（{len(items)}）**\n" + "\n".join(items))
        stats = "；".join([f"{status} {len(items)} 条" for status, items in sorted_groups[:4]])
        return {
            "header_summary": "状态筛选",
            "content": "\n\n".join(sections) if sections else "- 暂无状态数据",
            "stats_summary": stats or "无状态统计",
        }

    lines: list[str] = []
    todo_count = 0
    for index, record in enumerate(records, start=1):
        todo_items = _split_lines(record.get("todo"))
        todo_text = "；".join(todo_items[:2]) if todo_items else "—"
        if todo_items:
            todo_count += 1
        status = _normalize_inline_text(record.get("case_status") or record.get("status")) or "—"
        lines.append(f"- {_case_brief_title_line(record, index)} | 状态: {status} | 待办: {todo_text}")

    return {
        "header_summary": "待办看板",
        "content": "\n".join(lines) if lines else "- 暂无待办",
        "stats_summary": f"待办非空 {todo_count}/{len(records)}",
    }


def _render_case_focus_template_layout(
    *,
    records: list[Mapping[str, Any]],
    style: str,
    title: str,
    count: int,
    table_name: str,
    table_id: str,
) -> dict[str, Any] | None:
    family = _case_focus_template_family(style)
    if not family:
        return None

    style_cfg = _style_dsl("case", family)
    if _safe_text(style_cfg.get("render_mode")).lower() != "template_files":
        return None

    if family == "T3":
        layout = _render_case_t3_focus_layout(records=records, style=style, style_cfg=style_cfg)
        if isinstance(layout, Mapping):
            return dict(layout)
    if family == "T5":
        layout = _render_case_t5_focus_layout(records=records, style=style, style_cfg=style_cfg)
        if isinstance(layout, Mapping):
            return dict(layout)

    template_file = _safe_text(style_cfg.get("list_template_file"))
    if not template_file:
        return None
    template_text = _read_template_file(template_file)
    if not template_text:
        return None

    dsl_records = [_build_dsl_record(record, "case") for record in records]
    variant = _resolve_case_focus_variant(style)
    values: dict[str, Any] = {
        "title": title,
        "count": str(count),
        "table_name": table_name,
        "table_id": table_id,
        "style": style,
        "table_badge": _build_table_badge_text(table_name, table_id, style),
    }
    if family == "T3":
        values.update(_render_case_t3_template_values(dsl_records, variant))
    else:
        values.update(_render_case_t5_template_values(dsl_records, variant))

    markdown = _render_placeholders(template_text, values)
    if not markdown:
        return None

    elements: list[dict[str, Any]] = [_markdown(markdown)]
    if _safe_bool(style_cfg.get("append_detail_button"), True):
        for record in records:
            _append_view_detail_action(elements, record)

    wrapper_file = _safe_text(style_cfg.get("wrapper_file"))
    if not wrapper_file:
        return {"elements": elements}
    wrapper_values = {
        "header_title": title,
        "table_name": table_name,
        "style": style,
    }
    wrapper = _load_wrapper_template(wrapper_file, wrapper_values)
    if not wrapper:
        return {"elements": elements}
    return {
        "elements": elements,
        "wrapper": wrapper,
    }


def _resolve_list_item_value_builder(
    domain: str,
    style: str,
) -> Callable[[Mapping[str, Any], int], dict[str, str]] | None:
    normalized = style.upper()
    if domain == "case" and normalized == "T2":
        return _render_case_t2_template_values
    if domain == "contracts" and normalized == "HT-T2":
        return _render_contract_t2_template_values
    if domain == "bidding" and normalized == "ZB-T2":
        return _render_bidding_t2_template_values
    return None


def _render_list_template_layout(
    *,
    records: list[Mapping[str, Any]],
    domain: str,
    style_cfg: Mapping[str, Any],
    title: str,
    count: int,
    style: str,
    table_name: str,
    table_id: str,
) -> dict[str, Any] | None:
    if _safe_text(style_cfg.get("render_mode")).lower() != "template_files":
        return None

    value_builder = _resolve_list_item_value_builder(domain, style)
    if value_builder is None:
        return None

    item_template_file = _safe_text(style_cfg.get("list_item_template_file"))
    item_template = _read_template_file(item_template_file)
    if not item_template:
        return None

    table_badge = _build_table_badge_text(table_name, table_id, style)
    header_values: dict[str, Any] = {
        "title": title,
        "count": str(count),
        "table_name": table_name,
        "table_id": table_id,
        "style": style,
        "table_badge": table_badge,
    }

    elements: list[dict[str, Any]] = []
    header_template_file = _safe_text(style_cfg.get("list_header_template_file"))
    if header_template_file:
        header_template = _read_template_file(header_template_file)
        header_markdown = _render_placeholders(header_template, header_values)
    else:
        header_markdown = f"**{title}（共 {count} 条）**"
        if table_badge:
            header_markdown = f"{header_markdown}\n- {table_badge}"
    if header_markdown:
        elements.append(_markdown(header_markdown))

    append_detail_button = _safe_bool(style_cfg.get("append_detail_button"), True)
    for index, record in enumerate(records, start=1):
        dsl_record = _build_dsl_record(record, domain)
        values = value_builder(dsl_record, index)
        values.update(header_values)
        item_markdown = _render_placeholders(item_template, values)
        if item_markdown:
            elements.append(_markdown(item_markdown))
        if append_detail_button:
            _append_view_detail_action(elements, record)

    if not elements:
        return None

    wrapper_file = _safe_text(style_cfg.get("wrapper_file"))
    if not wrapper_file:
        return {"elements": elements}

    wrapper_values = {
        "header_title": title,
        "table_name": table_name,
        "style": style,
    }
    wrapper = _load_wrapper_template(wrapper_file, wrapper_values)
    if not wrapper:
        return {"elements": elements}
    return {
        "elements": elements,
        "wrapper": wrapper,
    }


def _render_single_record_template_layout(
    *,
    record: Mapping[str, Any],
    domain: str,
    style: str,
    style_cfg: Mapping[str, Any],
    title: str,
    table_name: str,
) -> dict[str, Any] | None:
    dsl_record = _build_dsl_record(record, domain)
    values: dict[str, Any] = {
        "title": title,
        "table_name": table_name,
    }
    if domain == "case" and style.upper() == "T1":
        values.update(_render_case_t1_template_values(dsl_record))
    elif domain == "contracts" and style.upper() == "HT-T1":
        values.update(_render_contract_t1_template_values(dsl_record))
    elif domain == "bidding" and style.upper() == "ZB-T1":
        values.update(_render_bidding_t1_template_values(dsl_record))
    else:
        for key, value in dsl_record.items():
            if str(key).startswith("_"):
                continue
            values[str(key)] = _normalize_inline_text(value) or "—"

    elements: list[dict[str, Any]] = []
    layout_file = _safe_text(style_cfg.get("layout_file"))
    if layout_file:
        elements = _load_layout_template_elements(layout_file, values)

    if not elements:
        template_file = _safe_text(style_cfg.get("template_file"))
        if not template_file:
            return None
        template_text = _read_template_file(template_file)
        if not template_text:
            return None
        markdown = _render_placeholders(template_text, values)
        if not markdown:
            return None
        elements = [_markdown(markdown)]

    if _safe_bool(style_cfg.get("append_detail_button"), True):
        _append_view_detail_action(elements, record)

    wrapper_file = _safe_text(style_cfg.get("wrapper_file"))
    if not wrapper_file:
        return {"elements": elements}
    header_title = _safe_text(style_cfg.get("header_title")) or title
    wrapper_values = {
        "header_title": header_title,
        "table_name": table_name,
        "style": style,
    }
    wrapper = _load_wrapper_template(wrapper_file, wrapper_values)
    if not wrapper:
        return {"elements": elements}
    return {
        "elements": elements,
        "wrapper": wrapper,
    }


def _build_dsl_record(record: Mapping[str, Any], domain: str) -> dict[str, Any]:
    fields = _record_fields(record)
    mapped: dict[str, Any] = {"_origin_record": record}
    mapping_raw = _render_value(f"query_list_v2.field_mapping.{domain}", {})
    mapping = mapping_raw if isinstance(mapping_raw, Mapping) else {}
    for source_name, target_key in mapping.items():
        source = _safe_text(source_name)
        target = _safe_text(target_key)
        if source and target and source in fields:
            mapped[target] = fields.get(source)
    for key, value in fields.items():
        text_key = _safe_text(key)
        if text_key and text_key not in mapped:
            mapped[text_key] = value
    return mapped


def _bucket_condition(label: str) -> dict[str, str]:
    mapping = {
        "已过期": {"label": f"{_OK_MARKER} 已过期", "condition": "< today"},
        "今日": {"label": f"{_OK_MARKER} 今日", "condition": "= today"},
        "本周": {"label": f"{_OK_MARKER} 本周", "condition": ">= today AND <= this_week_end"},
        "下周": {"label": f"{_OK_MARKER} 下周", "condition": ">= next_week_start AND <= next_week_end"},
        "本月": {"label": f"{_OK_MARKER} 本月", "condition": ">= this_month_start AND <= this_month_end"},
        "7 天内": {"label": f"{_OK_MARKER} 7 天内", "condition": ">= today AND <= today+7"},
        "30 天内": {"label": f"{_OK_MARKER} 30 天内", "condition": ">= today AND <= today+30"},
        "更远": {"label": f"{_OK_MARKER} 更远", "condition": "> this_month_end"},
    }
    return mapping.get(label, {"label": label, "condition": ""})


def _render_advanced_query_layout(
    records: list[Mapping[str, Any]],
    domain: str,
    style: str,
    style_cfg: Mapping[str, Any],
    context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not records:
        return []

    dsl_records = [_build_dsl_record(record, domain) for record in records]
    filter_engine = FilterEngine()
    summary_engine = SummaryEngine(filter_engine)
    section_engine = SectionEngine(filter_engine)
    group_engine = GroupEngine()
    elements: list[dict[str, Any]] = []
    detail_action_keys: set[str] = set()

    header_fields_raw = style_cfg.get("header_fields")
    header_fields = header_fields_raw if isinstance(header_fields_raw, list) else []
    for header in header_fields:
        if not isinstance(header, Mapping):
            continue
        template = _safe_text(header.get("template"))
        if template:
            text = summary_engine.execute(dsl_records, {"template": template, "variables": header.get("variables") or {}})
            if text:
                elements.append(_markdown(text))

    sections_raw = style_cfg.get("sections")
    sections = sections_raw if isinstance(sections_raw, list) else []
    if sections:
        rendered_sections = section_engine.execute(
            all_records=dsl_records,
            sections_config=sections,
            context=context,
            render_item=lambda item, specs: _render_fields_by_dsl(item, domain, specs, detail_mode=False),
        )
        for section in rendered_sections:
            name = _safe_text(section.get("name")) or "分段"
            icon = _safe_text(section.get("icon"))
            items = section.get("items") if isinstance(section.get("items"), list) else []
            section_head = f"{icon} {name}".strip()
            section_title = f"━━ {section_head} ━━" if section_head else f"━━ {name} ━━"
            elements.append(_markdown(section_title))
            table = section.get("table") if isinstance(section.get("table"), Mapping) else None
            if table:
                headers = table.get("headers") if isinstance(table.get("headers"), list) else []
                rows = table.get("rows") if isinstance(table.get("rows"), list) else []
                if headers:
                    elements.append(_markdown("| " + " | ".join(str(h) for h in headers) + " |"))
                for row in rows[:8]:
                    if isinstance(row, list):
                        elements.append(_markdown("| " + " | ".join(str(c) for c in row) + " |"))
                continue

            if not items:
                empty_text = _safe_text(section.get("empty_text")) or "暂无数据"
                elements.append(_markdown(f"- {empty_text}"))
                continue
            collapsed = bool(section.get("collapsible", False) and section.get("collapsed", False))
            display_items = items[:3] if collapsed else items
            for item in display_items:
                if not isinstance(item, Mapping):
                    continue
                lines = item.get("lines") if isinstance(item.get("lines"), list) else []
                if lines:
                    elements.append(_markdown("\n".join(str(line) for line in lines)))
                source = item.get("record") if isinstance(item.get("record"), Mapping) else {}
                origin = source.get("_origin_record") if isinstance(source, Mapping) else {}
                if isinstance(origin, Mapping):
                    action_key = _safe_text(origin.get("record_url") or origin.get("record_id"))
                    if action_key and action_key not in detail_action_keys:
                        _append_view_detail_action(elements, origin)
                        detail_action_keys.add(action_key)
            if collapsed and len(items) > len(display_items):
                expand_label = _safe_text(section.get("expand_label")) or "展开查看全部 {count} 条"
                elements.append(_markdown(f"- {expand_label.replace('{count}', str(len(items)))}"))

    elif _safe_text(style_cfg.get("group_by")):
        group_by = _safe_text(style_cfg.get("group_by"))
        buckets_raw = style_cfg.get("group_buckets")
        order_raw = style_cfg.get("group_order")
        icons_raw = style_cfg.get("group_icons")
        group_config: dict[str, Any] = {"field": group_by}
        if isinstance(buckets_raw, list) and buckets_raw:
            group_config["buckets"] = [_bucket_condition(_safe_text(item)) for item in buckets_raw]
        if isinstance(order_raw, list) and order_raw:
            group_config["order"] = [str(item) for item in order_raw]
        if isinstance(icons_raw, Mapping):
            group_config["icons"] = dict(icons_raw)

        grouped = group_engine.execute(dsl_records, group_config)
        list_fields_raw = style_cfg.get("list_fields")
        list_fields = [item for item in list_fields_raw if isinstance(item, Mapping)] if isinstance(list_fields_raw, list) else []
        for label, items in grouped:
            elements.append(_markdown(f"━━ {label}（{len(items)}） ━━"))
            if not items:
                elements.append(_markdown("- 暂无"))
                continue
            for item in items:
                lines = _render_fields_by_dsl(item, domain, list_fields, detail_mode=False)
                if lines:
                    elements.append(_markdown("\n".join(lines)))
                origin = item.get("_origin_record") if isinstance(item, Mapping) else None
                if isinstance(origin, Mapping):
                    action_key = _safe_text(origin.get("record_url") or origin.get("record_id"))
                    if action_key and action_key not in detail_action_keys:
                        _append_view_detail_action(elements, origin)
                        detail_action_keys.add(action_key)

    summary_raw = style_cfg.get("summary")
    summary = summary_raw if isinstance(summary_raw, Mapping) else None
    if summary is not None:
        summary_text = summary_engine.execute(dsl_records, summary)
        if summary_text:
            elements.append(_markdown(summary_text))
    return elements


def _kv_lines(record: Mapping[str, Any], max_items: int = 8) -> list[str]:
    lines: list[str] = []
    for index, (key, value) in enumerate(record.items()):
        if index >= max_items:
            break
        key_text = _safe_text(key)
        value_text = _safe_text(value)
        if not key_text and not value_text:
            continue
        if key_text and value_text:
            lines.append(f"- **{key_text}**: {value_text}")
        elif key_text:
            lines.append(f"- **{key_text}**")
        else:
            lines.append(f"- {value_text}")
    return lines


def _record_fields(record: Mapping[str, Any]) -> Mapping[str, Any]:
    fields_text = record.get("fields_text")
    if isinstance(fields_text, Mapping):
        return fields_text
    fields = record.get("fields")
    if isinstance(fields, Mapping):
        return fields
    return {}


def _pick_first(fields: Mapping[str, Any], keys: list[str]) -> str:
    for key in keys:
        text = _safe_text(fields.get(key))
        if text:
            return text
    return ""


def _domain_from_style(style: str) -> str:
    normalized = style.upper()
    if normalized.startswith("HT-"):
        return "contracts"
    if normalized.startswith("ZB-"):
        return "bidding"
    if normalized.startswith("RW-"):
        return "team_overview"
    if normalized.startswith("T"):
        return "case"
    return "case"


def _date_status_symbol(date_text: str, status_text: str = "") -> str:
    normalized_status = status_text.lower()
    if any(token in normalized_status for token in ("完成", "已结", "closed", "done", "归档")):
        return _OK_MARKER
    if not date_text:
        return _OK_MARKER

    parsed = ""
    for separator in ("T", " "):
        if separator in date_text:
            parsed = date_text.split(separator, 1)[0]
            break
    parsed = parsed or date_text

    try:
        due = date.fromisoformat(parsed)
    except ValueError:
        return _OK_MARKER

    today = date.today()
    if due < today:
        return _OK_MARKER
    if due == today:
        return _OK_MARKER
    if (due - today).days <= 3:
        return _OK_MARKER
    return _OK_MARKER


def _urgency_symbol(urgency_text: str) -> str:
    _ = urgency_text
    return _OK_MARKER


def _fmt_detail(value: Any) -> str:
    text = _safe_text(value)
    return text or "—"


def _append_view_detail_action(elements: list[dict[str, Any]], record: Mapping[str, Any]) -> None:
    url = _safe_text(record.get("record_url"))
    if not url:
        return
    button_text = _safe_text(_render_value("query_list_v2.texts.view_detail", "查看详情")) or "查看详情"
    elements.append(
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": button_text},
                    "type": "default",
                    "multi_url": {"url": url},
                }
            ],
        }
    )


def _normalize_callback_value(
    raw: Mapping[str, Any] | None,
    *,
    callback_action: str,
    table_type: str = "",
    record_id: str = "",
    extra_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(raw) if isinstance(raw, Mapping) else {}
    reserved = {"callback_action", "table_type", "record_id", "extra_data"}

    merged_extra: dict[str, Any] = {}
    existing_extra = payload.get("extra_data")
    if isinstance(existing_extra, Mapping):
        merged_extra.update(dict(existing_extra))
    for key, value in payload.items():
        if key not in reserved:
            merged_extra[str(key)] = value
    if isinstance(extra_data, Mapping):
        merged_extra.update(dict(extra_data))

    return {
        "callback_action": _safe_text(payload.get("callback_action") or callback_action),
        "table_type": _safe_text(payload.get("table_type") or table_type),
        "record_id": _safe_text(payload.get("record_id") or record_id),
        "extra_data": merged_extra,
    }


def _normalize_button_type(value: Any, default: str = "primary_filled") -> str:
    normalized = _safe_text(value).lower()
    if normalized in {"primary_filled", "primary", "default"}:
        return normalized
    if normalized == "danger":
        return "primary_filled"
    return default


def _decorate_button_text(raw: Any, *, prefix: str, fallback: str) -> str:
    text = _safe_text(raw) or fallback
    if not text:
        text = fallback
    if text and text[0] in {"✅", "❌", "✏️", "⛔", "⚠️", "📌"}:
        return text
    return f"{prefix} {text}".strip()


def _build_callback_button(*, text: str, button_type: str, value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {
            "tag": "plain_text",
            "content": text,
        },
        "type": button_type,
        "width": "default",
        "margin": "4px 0px 4px 0px",
        "value": dict(value),
    }


def _build_open_url_button(*, text: str, button_type: str, url: str) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {
            "tag": "plain_text",
            "content": text,
        },
        "type": button_type,
        "width": "default",
        "margin": "4px 0px 4px 0px",
        "behaviors": [
            {
                "type": "open_url",
                "default_url": url,
                "pc_url": "",
                "ios_url": "",
                "android_url": "",
            }
        ],
    }


def _build_button_row(buttons: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    columns: list[dict[str, Any]] = []
    for button in buttons:
        if not isinstance(button, Mapping):
            continue
        columns.append(
            {
                "tag": "column",
                "width": "auto",
                "elements": [dict(button)],
            }
        )
    if not columns:
        return None
    return {
        "tag": "column_set",
        "flex_mode": "stretch",
        "horizontal_spacing": "8px",
        "margin": "0px",
        "columns": columns,
    }


def _prune_empty_markdown_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for element in elements:
        if not isinstance(element, Mapping):
            continue
        if _safe_text(element.get("tag")) == "markdown" and not _safe_text(element.get("content")):
            continue
        cleaned.append(dict(element))
    return cleaned


def _flatten_action_payload_fields(payload: Mapping[str, Any]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for key in ("source_fields", "fields", "preview_fields"):
        raw = payload.get(key)
        fields = raw if isinstance(raw, Mapping) else {}
        for field_name, value in fields.items():
            name = _safe_text(field_name)
            if not name:
                continue
            merged[name] = _safe_text(value)
    return merged


def _resolve_action_identity(payload: Mapping[str, Any], record_id: str = "") -> str:
    fields = _flatten_action_payload_fields(payload)
    project_id = _pick_first(fields, ["项目 ID", "项目ID", "项目号", "合同号", "合同编号", "记录 ID", "record_id"])
    case_no = _pick_first(fields, ["案号"]) or _safe_text(payload.get("case_no"))
    title = _pick_first(fields, ["合同名称", "投标项目名称", "项目名称", "标题", "案由"])
    left_party = _pick_first(fields, ["委托人", "客户名称", "甲方", "招标方名称"])
    right_party = _pick_first(fields, ["对方当事人", "乙方"])
    court = _pick_first(fields, ["审理法院"])
    stage = _pick_first(fields, ["程序阶段", "案件状态", "状态"])

    lines: list[str] = []
    if project_id and title:
        lines.append(f"📋 **{project_id}** | {title}")
    elif project_id:
        lines.append(f"🔖 {project_id}")
    elif title:
        lines.append(f"📋 {title}")

    if case_no:
        lines.append(f"📄 {case_no}")

    if left_party and right_party:
        lines.append(f"🏢 {left_party} vs {right_party}")
    elif left_party:
        lines.append(f"🏢 {left_party}")

    if court and stage:
        lines.append(f"⚖️ {court} | {stage}")
    elif court:
        lines.append(f"⚖️ {court}")

    if not lines and record_id:
        lines.append(f"🔖 {record_id}")
    return "\n".join(lines)


def _normalize_error_class(value: Any, message: str) -> str:
    explicit = _safe_text(value).lower().replace("-", "_")
    if explicit in {"missing_params", "record_not_found", "permission_denied"}:
        return explicit

    normalized = message.lower()
    if any(token in normalized for token in ["权限", "无权", "forbidden", "permission denied", "access denied"]):
        return "permission_denied"
    if any(token in normalized for token in ["未找到", "不存在", "没有找到", "not found", "recordidnotfound", "notfound"]):
        return "record_not_found"
    if any(token in normalized for token in ["缺少", "必填", "参数", "未提供", "无法解析更新字段"]):
        return "missing_params"
    return "general"


def _error_class_label(error_class: str) -> str:
    labels = {
        "missing_params": "缺少参数",
        "record_not_found": "记录不存在",
        "permission_denied": "权限不足",
        "general": "一般错误",
    }
    return labels.get(error_class, labels["general"])


def _error_next_step(error_class: str, explicit: Any) -> str:
    explicit_text = _safe_text(explicit)
    if explicit_text:
        return explicit_text

    guidance = {
        "missing_params": "请补充必填字段后重试，例如：案号是 A-2026-001。",
        "record_not_found": "请先查询确认记录是否存在，并提供准确的案号或记录 ID。",
        "permission_denied": "请确认当前账号具备目标表的查看/编辑权限，必要时联系管理员开通。",
        "general": "请稍后重试；若持续失败，请附上操作步骤联系管理员。",
    }
    return guidance.get(error_class, guidance["general"])


def render_query_list_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "查询结果"
    total = int(params.get("total") or 0)
    records = params.get("records")
    if not isinstance(records, list) or not records:
        return [_markdown(f"**{title}**\n暂无记录")]

    elements: list[dict[str, Any]] = [_markdown(f"**{title}**（共 {max(total, len(records))} 条）")]
    for i, record in enumerate(records[:8], start=1):
        if not isinstance(record, Mapping):
            continue
        fields_text = record.get("fields_text")
        if isinstance(fields_text, Mapping):
            lines = _kv_lines(fields_text, max_items=4)
            link_line = build_record_link_line(record.get("record_id"), record.get("record_url"))
            if link_line:
                lines.append(f"- {link_line}")
            body = "\n".join(lines) if lines else "- 记录详情"
        else:
            body = _safe_text(record.get("record_id")) or "记录详情"
        elements.append(_markdown(f"**{i}.**\n{body}"))
    return elements


def _query_summary_lines(record: Mapping[str, Any]) -> list[str]:
    fields = _record_fields(record)
    case_no = _safe_text(fields.get("案号") or fields.get("项目ID") or record.get("record_id"))
    left = _safe_text(fields.get("委托人及联系方式") or fields.get("委托人"))
    right = _safe_text(fields.get("对方当事人"))
    cause = _safe_text(fields.get("案由"))
    court = _safe_text(fields.get("审理法院"))
    stage = _safe_text(fields.get("程序阶段"))

    title = " vs ".join([part for part in [left, right] if part])
    if cause:
        title = f"{title} | {cause}" if title else cause
    if not title:
        title = case_no or "记录摘要"

    lines = [f"- {title}"]
    if case_no:
        lines.append(f"- 案号: {case_no}")
    if court:
        lines.append(f"- 法院: {court}")
    if stage:
        lines.append(f"- 程序: {stage}")

    link_line = build_record_link_line(record.get("record_id"), record.get("record_url"))
    if link_line:
        lines.append(f"- {link_line}")
    return lines


def render_query_list_v2(params: dict[str, Any]) -> Any:
    title = _safe_text(params.get("title")) or _safe_text(_render_value("query_list_v2.texts.default_title", "查询结果"))
    if not title:
        title = "查询结果"
    total = _safe_int(params.get("total"), 0)
    records = params.get("records")
    style = _safe_text(params.get("style")).upper()
    if not style:
        style = "T2"
    style_variant = _safe_text(params.get("style_variant")).upper()
    effective_style = style_variant or style
    domain = _safe_text(params.get("domain")) or _domain_from_style(style)

    if not isinstance(records, list):
        records = []

    count = max(total, len(records))
    if count <= 0 or not records:
        not_found = _safe_text(_render_value("query_list_v2.texts.not_found", "咦，好像没能查到任何相关记录 🤔")) or "咦，好像没能查到任何相关记录 🤔"
        suggestion = _safe_text(params.get("not_found_suggestion")) or _safe_text(
            _render_value("query_list_v2.texts.not_found_suggestion", "建议补充案号、负责人、时间范围等条件后重试。")
        )
        return [_markdown(f"**{title}**\n{not_found}\n- 建议: {suggestion}")]

    large_limit = _safe_int(_render_value("query_list_v2.list_limits.large", 5), 5)
    small_limit = _safe_int(_render_value("query_list_v2.list_limits.small", 10), 10)
    list_limit = large_limit if count >= 6 else small_limit
    shown_records = records[:list_limit]
    remaining = max(count - len(shown_records), 0)
    table_name = _safe_text(params.get("table_name")) or _domain_table_label(domain)
    table_id = _safe_text(params.get("table_id"))
    actions_raw = params.get("actions")
    actions = actions_raw if isinstance(actions_raw, Mapping) else {}

    style_cfg = _style_dsl(domain, effective_style)

    if count == 1 and shown_records and isinstance(shown_records[0], Mapping):
        single_layout = _render_single_record_template_layout(
            record=shown_records[0],
            domain=domain,
            style=effective_style,
            style_cfg=style_cfg,
            title=title,
            table_name=table_name,
        )
        if isinstance(single_layout, Mapping):
            elements_raw = single_layout.get("elements")
            elements = [item for item in elements_raw if isinstance(item, dict)] if isinstance(elements_raw, list) else []
            wrapper_raw = single_layout.get("wrapper")
            wrapper = dict(wrapper_raw) if isinstance(wrapper_raw, Mapping) else {}
            return {
                "elements": elements,
                "wrapper": wrapper,
            }

    if domain == "contracts" and effective_style == "HT-T2":
        ht_t2_layout = _render_contract_t2_cardkit_layout(
            records=[record for record in shown_records if isinstance(record, Mapping)],
            style_cfg=style_cfg,
            count=count,
            shown_count=len(shown_records),
            remaining=remaining,
            actions=actions,
        )
        if isinstance(ht_t2_layout, Mapping):
            elements_raw = ht_t2_layout.get("elements")
            elements = [item for item in elements_raw if isinstance(item, dict)] if isinstance(elements_raw, list) else []
            wrapper_raw = ht_t2_layout.get("wrapper")
            wrapper = dict(wrapper_raw) if isinstance(wrapper_raw, Mapping) else {}
            return {
                "elements": elements,
                "wrapper": wrapper,
            }

    if domain == "bidding" and effective_style == "ZB-T2":
        zb_t2_layout = _render_bidding_t2_cardkit_layout(
            records=[record for record in shown_records if isinstance(record, Mapping)],
            style_cfg=style_cfg,
            count=count,
            shown_count=len(shown_records),
            remaining=remaining,
            actions=actions,
        )
        if isinstance(zb_t2_layout, Mapping):
            elements_raw = zb_t2_layout.get("elements")
            elements = [item for item in elements_raw if isinstance(item, dict)] if isinstance(elements_raw, list) else []
            wrapper_raw = zb_t2_layout.get("wrapper")
            wrapper = dict(wrapper_raw) if isinstance(wrapper_raw, Mapping) else {}
            return {
                "elements": elements,
                "wrapper": wrapper,
            }

    if domain == "case" and effective_style == "T2":
        t2_layout = _render_case_t2_cardkit_layout(
            records=[record for record in shown_records if isinstance(record, Mapping)],
            style_cfg=style_cfg,
            title=title,
            count=count,
            shown_count=len(shown_records),
            remaining=remaining,
            actions=actions,
            table_name=table_name,
            table_id=table_id,
        )
        if isinstance(t2_layout, Mapping):
            elements_raw = t2_layout.get("elements")
            elements = [item for item in elements_raw if isinstance(item, dict)] if isinstance(elements_raw, list) else []
            wrapper_raw = t2_layout.get("wrapper")
            wrapper = dict(wrapper_raw) if isinstance(wrapper_raw, Mapping) else {}
            return {
                "elements": elements,
                "wrapper": wrapper,
            }

    wrapper: dict[str, Any] = {}
    case_focus_layout = None
    if domain == "case":
        case_focus_layout = _render_case_focus_template_layout(
            records=[record for record in shown_records if isinstance(record, Mapping)],
            style=effective_style,
            title=title,
            count=count,
            table_name=table_name,
            table_id=table_id,
        )

    if isinstance(case_focus_layout, Mapping):
        elements_raw = case_focus_layout.get("elements")
        elements = [item for item in elements_raw if isinstance(item, dict)] if isinstance(elements_raw, list) else []
        wrapper_raw = case_focus_layout.get("wrapper")
        wrapper = dict(wrapper_raw) if isinstance(wrapper_raw, Mapping) else {}
    else:
        list_template_layout = _render_list_template_layout(
            records=[record for record in shown_records if isinstance(record, Mapping)],
            domain=domain,
            style_cfg=style_cfg,
            title=title,
            count=count,
            style=effective_style,
            table_name=table_name,
            table_id=table_id,
        )

        if isinstance(list_template_layout, Mapping):
            elements_raw = list_template_layout.get("elements")
            elements = [item for item in elements_raw if isinstance(item, dict)] if isinstance(elements_raw, list) else []
            wrapper_raw = list_template_layout.get("wrapper")
            wrapper = dict(wrapper_raw) if isinstance(wrapper_raw, Mapping) else {}
        else:
            elements = [_markdown(f"**{title}（共 {count} 条）**")]
            badge_text = _build_table_badge_text(table_name, table_id, effective_style)
            if badge_text:
                elements.append(_markdown(f"- {badge_text}"))

            advanced_elements = _render_advanced_query_layout(
                records=[record for record in shown_records if isinstance(record, Mapping)],
                domain=domain,
                style=effective_style,
                style_cfg=style_cfg,
                context={
                    "style": effective_style,
                    "table_name": table_name,
                },
            )
            if advanced_elements:
                elements.extend(advanced_elements)
            elif count == 1 and shown_records and isinstance(shown_records[0], Mapping):
                detail_elements = _render_query_focus_card(
                    record=shown_records[0],
                    style=effective_style,
                    domain=domain,
                )
                elements.extend(detail_elements)
            else:
                for i, record in enumerate(shown_records, start=1):
                    if not isinstance(record, Mapping):
                        continue
                    lines = _render_query_list_item_lines(record=record, style=effective_style, domain=domain)
                    if lines:
                        elements.append(_markdown(f"**{i}.**\n" + "\n".join(lines)))
                    _append_view_detail_action(elements, record)

    if count >= 10:
        hint = _safe_text(
            _render_value("query_list_v2.texts.narrowing_hint", "结果较多，建议补充关键词或时间范围缩小范围。")
        )
        if hint:
            elements.append(_markdown(f"- 提示: {hint}"))

    suppress_footer_actions = domain == "case" and _case_focus_template_family(effective_style) in {"T3", "T5"}
    if not suppress_footer_actions:
        next_page_raw = actions.get("next_page")
        today_raw = actions.get("today_hearing")
        week_raw = actions.get("week_hearing")
        next_page_value = _normalize_callback_value(
            next_page_raw if isinstance(next_page_raw, Mapping) else None,
            callback_action="query_list_next_page",
            table_type=domain,
        )
        today_value = _normalize_callback_value(
            today_raw if isinstance(today_raw, Mapping) else None,
            callback_action="query_list_today_hearing",
            table_type=domain,
        )
        week_value = _normalize_callback_value(
            week_raw if isinstance(week_raw, Mapping) else None,
            callback_action="query_list_week_hearing",
            table_type=domain,
        )

        action_items: list[dict[str, Any]] = []
        next_extra_raw = next_page_value.get("extra_data")
        next_extra: dict[str, Any] = dict(next_extra_raw) if isinstance(next_extra_raw, Mapping) else {}
        next_kind = _safe_text(next_page_value.get("kind") or next_extra.get("kind"))
        if remaining > 0 or next_kind == "no_more":
            next_text = _safe_text(_render_value("query_list_v2.actions.next_page", "下一页")) or "下一页"
            if remaining > 0:
                template = _safe_text(
                    _render_value("query_list_v2.actions.next_page_with_remaining", "下一页（剩余 {remaining} 条）")
                )
                next_text = template.format(remaining=remaining)
            action_items.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": next_text},
                    "type": "default",
                    "value": next_page_value,
                }
            )

        if domain == "case":
            today_text = _safe_text(_render_value("query_list_v2.actions.today_hearing", "今天开庭")) or "今天开庭"
            week_text = _safe_text(_render_value("query_list_v2.actions.week_hearing", "本周开庭")) or "本周开庭"
            action_items.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": today_text},
                    "type": "default",
                    "value": today_value,
                }
            )
            action_items.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": week_text},
                    "type": "default",
                    "value": week_value,
                }
            )

        if action_items:
            elements.append({"tag": "action", "actions": action_items})
    if wrapper:
        return {
            "elements": elements,
            "wrapper": wrapper,
        }
    return elements


def _render_query_focus_card(record: Mapping[str, Any], style: str, domain: str) -> list[dict[str, Any]]:
    fields = _record_fields(record)
    elements: list[dict[str, Any]] = []
    style_upper = style.upper()

    dsl = _style_dsl(domain, style_upper)
    detail_specs_raw = dsl.get("detail_fields") if isinstance(dsl, Mapping) else None
    if isinstance(detail_specs_raw, list):
        detail_specs = [item for item in detail_specs_raw if isinstance(item, Mapping)]
        lines = _render_fields_by_dsl(fields=fields, domain=domain, specs=detail_specs, detail_mode=True)
        if lines:
            elements.append(_markdown("\n".join(lines)))
            _append_view_detail_action(elements, record)
            return elements

    if domain == "contracts":
        contracts_keys = "contracts"
        lines = [
            f"- 合同编号: {_fmt_detail(_pick_first(fields, _field_keys(contracts_keys, 'id', ['合同编号', '编号', '项目ID'])))}",
            f"- 合同名称: {_fmt_detail(_pick_first(fields, _field_keys(contracts_keys, 'name', ['合同名称', '标题'])))}",
            f"- 甲方: {_fmt_detail(_pick_first(fields, _field_keys(contracts_keys, 'party_a', ['甲方'])))}",
            f"- 乙方: {_fmt_detail(_pick_first(fields, _field_keys(contracts_keys, 'party_b', ['乙方'])))}",
            f"- 金额: {_fmt_detail(_pick_first(fields, _field_keys(contracts_keys, 'amount', ['合同金额', '金额'])))}",
            f"- 状态: {_fmt_detail(_pick_first(fields, _field_keys(contracts_keys, 'status', ['合同状态', '状态'])))}",
        ]
        if style_upper == "HT-T3":
            due = _pick_first(fields, _field_keys(contracts_keys, "date", ["签约日期", "到期日期", "付款截止", "截止日"]))
            status_text = _pick_first(fields, _field_keys(contracts_keys, "status", ["合同状态", "状态"]))
            due_status = _date_status_symbol(due, status_text)
            lines.append(f"- 日期状态: {due_status} {_fmt_detail(due)}")
    elif domain == "bidding":
        bidding_keys = "bidding"
        lines = [
            f"- 项目名称: {_fmt_detail(_pick_first(fields, _field_keys(bidding_keys, 'name', ['项目名称', '标段名称'])))}",
            f"- 招标方: {_fmt_detail(_pick_first(fields, _field_keys(bidding_keys, 'owner_org', ['招标方', '业主单位'])))}",
            f"- 当前阶段: {_fmt_detail(_pick_first(fields, _field_keys(bidding_keys, 'phase', ['阶段', '进度', '状态'])))}",
            f"- 投标截止: {_fmt_detail(_pick_first(fields, _field_keys(bidding_keys, 'due', ['投标截止日', '截止日', '开标时间'])))}",
            f"- 负责人: {_fmt_detail(_pick_first(fields, _field_keys(bidding_keys, 'owner', ['负责人', '主办律师'])))}",
        ]
    elif domain == "team_overview":
        team_keys = "team_overview"
        lines = [
            f"- 成员: {_fmt_detail(_pick_first(fields, _field_keys(team_keys, 'member', ['成员', '姓名', '负责人'])))}",
            f"- 在办事项: {_fmt_detail(_pick_first(fields, _field_keys(team_keys, 'workload', ['在办事项', '任务数'])))}",
            f"- 今日节点: {_fmt_detail(_pick_first(fields, _field_keys(team_keys, 'today', ['今日节点', '今日安排'])))}",
            f"- 风险事项: {_fmt_detail(_pick_first(fields, _field_keys(team_keys, 'risk', ['风险事项', '风险'])))}",
            f"- 当前状态: {_fmt_detail(_pick_first(fields, _field_keys(team_keys, 'status', ['状态', '进展'])))}",
        ]
    else:
        case_keys = "case"
        case_no = _pick_first(fields, _field_keys(case_keys, "case_no", ["案号", "项目ID"]))
        date_value = _pick_first(fields, _field_keys(case_keys, "date", ["开庭日", "截止日", "上诉截止日", "举证截止日"]))
        status_value = _pick_first(fields, _field_keys(case_keys, "status", ["案件状态", "进展", "程序阶段"]))
        urgency_value = _pick_first(fields, _field_keys(case_keys, "urgency", ["紧急程度", "优先级", "风险等级"]))
        lines = [
            f"- 案号: {_fmt_detail(case_no)}",
            f"- 委托人: {_fmt_detail(_pick_first(fields, _field_keys(case_keys, 'title_left', ['委托人及联系方式', '委托人'])))}",
            f"- 对方当事人: {_fmt_detail(_pick_first(fields, _field_keys(case_keys, 'title_right', ['对方当事人'])))}",
            f"- 案由: {_fmt_detail(_pick_first(fields, _field_keys(case_keys, 'cause', ['案由'])))}",
            f"- 审理法院: {_fmt_detail(_pick_first(fields, _field_keys(case_keys, 'court', ['审理法院'])))}",
            f"- 程序阶段: {_fmt_detail(_pick_first(fields, _field_keys(case_keys, 'stage', ['程序阶段'])))}",
            f"- 进展状态: {_fmt_detail(status_value)}",
            f"- 日期状态: {_date_status_symbol(date_value, status_value)} {_fmt_detail(date_value)}",
            f"- 紧急程度: {_urgency_symbol(urgency_value)} {_fmt_detail(urgency_value)}",
        ]
        if style_upper == "T4":
            lines.append(
                f"- 主办律师: {_fmt_detail(_pick_first(fields, _field_keys(case_keys, 'owner', ['主办律师', '负责人'])))}"
            )
            lines.append(f"- 协办律师: {_fmt_detail(_pick_first(fields, _field_keys(case_keys, 'co_owner', ['协办律师'])))}")
        if style_upper == "T6":
            lines.append(f"- 承办法庭: {_fmt_detail(_pick_first(fields, _field_keys(case_keys, 'courtroom', ['承办法庭'])))}")

    elements.append(_markdown("\n".join(lines)))
    _append_view_detail_action(elements, record)
    return elements


def _render_query_list_item_lines(record: Mapping[str, Any], style: str, domain: str) -> list[str]:
    fields = _record_fields(record)
    style_upper = style.upper()

    dsl = _style_dsl(domain, style_upper)
    list_specs_raw = dsl.get("list_fields") if isinstance(dsl, Mapping) else None
    if isinstance(list_specs_raw, list):
        list_specs = [item for item in list_specs_raw if isinstance(item, Mapping)]
        lines = _render_fields_by_dsl(fields=fields, domain=domain, specs=list_specs, detail_mode=False)
        if lines:
            return lines

    if domain == "contracts":
        lines: list[str] = []
        contract_name = _pick_first(fields, _field_keys("contracts", "name", ["合同名称", "标题"]))
        if contract_name:
            lines.append(f"- {contract_name}")
        status = _pick_first(fields, _field_keys("contracts", "status", ["合同状态", "状态"]))
        if status:
            lines.append(f"- 状态: {status}")
        amount = _pick_first(fields, _field_keys("contracts", "amount", ["合同金额", "金额"]))
        if amount:
            lines.append(f"- 金额: {amount}")
        if style_upper == "HT-T3":
            due = _pick_first(fields, _field_keys("contracts", "date", ["签约日期", "到期日期", "付款截止", "截止日"]))
            if due:
                lines.append(f"- 日期状态: {_date_status_symbol(due, status)} {due}")
        return lines

    if domain == "bidding":
        lines = []
        project = _pick_first(fields, _field_keys("bidding", "name", ["项目名称", "标段名称"]))
        if project:
            lines.append(f"- {project}")
        phase = _pick_first(fields, _field_keys("bidding", "phase", ["阶段", "进度", "状态"]))
        if phase:
            lines.append(f"- 阶段: {phase}")
        due = _pick_first(fields, _field_keys("bidding", "due", ["投标截止日", "截止日", "开标时间"]))
        if due:
            lines.append(f"- 节点: {_date_status_symbol(due, phase)} {due}")
        owner = _pick_first(fields, _field_keys("bidding", "owner", ["负责人", "主办律师"]))
        if owner and style_upper in {"ZB-T4", "ZB-T3"}:
            lines.append(f"- 负责人: {owner}")
        return lines

    if domain == "team_overview":
        lines = []
        member = _pick_first(fields, _field_keys("team_overview", "member", ["成员", "姓名", "负责人"]))
        if member:
            lines.append(f"- {member}")
        workload = _pick_first(fields, _field_keys("team_overview", "workload", ["在办事项", "任务数"]))
        if workload:
            lines.append(f"- 在办: {workload}")
        status = _pick_first(fields, _field_keys("team_overview", "status", ["状态", "进展"]))
        if status:
            lines.append(f"- 状态: {status}")
        due = _pick_first(fields, _field_keys("team_overview", "due", ["截止日", "下个节点"]))
        if due and style_upper in {"RW-T3", "RW-T2"}:
            lines.append(f"- 节点: {_date_status_symbol(due, status)} {due}")
        return lines

    lines = []
    case_no = _pick_first(fields, _field_keys("case", "case_no", ["案号", "项目ID"]))
    title_left = _pick_first(fields, _field_keys("case", "title_left", ["委托人及联系方式", "委托人"]))
    title_right = _pick_first(fields, _field_keys("case", "title_right", ["对方当事人"]))
    cause = _pick_first(fields, _field_keys("case", "cause", ["案由"]))
    if title_left or title_right or cause:
        title = " vs ".join([part for part in [title_left, title_right] if part])
        if cause:
            title = f"{title} | {cause}" if title else cause
        if title:
            lines.append(f"- {title}")
    if case_no:
        lines.append(f"- 案号: {case_no}")

    status = _pick_first(fields, _field_keys("case", "status", ["案件状态", "进展", "程序阶段"]))
    if style_upper in {"T5", "T2"} and status:
        lines.append(f"- 状态: {status}")

    if style_upper in {"T6", "T2"}:
        court = _pick_first(fields, _field_keys("case", "court", ["审理法院"]))
        if court:
            lines.append(f"- 法院: {court}")
        stage = _pick_first(fields, _field_keys("case", "stage", ["程序阶段"]))
        if stage and stage != status:
            lines.append(f"- 程序: {stage}")

    if style_upper in {"T3", "T5", "T2"}:
        date_value = _pick_first(fields, _field_keys("case", "date", ["开庭日", "截止日", "上诉截止日", "举证截止日"]))
        if date_value:
            lines.append(f"- 日期状态: {_date_status_symbol(date_value, status)} {date_value}")

    if style_upper in {"T4", "T2"}:
        owner = _pick_first(fields, _field_keys("case", "owner", ["主办律师", "负责人"]))
        if owner:
            lines.append(f"- 负责人: {owner}")

    if style_upper in {"T5", "T2"}:
        urgency = _pick_first(fields, _field_keys("case", "urgency", ["紧急程度", "优先级", "风险等级"]))
        if urgency:
            lines.append(f"- 紧急程度: {_urgency_symbol(urgency)} {urgency}")
    return lines


def render_query_detail_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "记录详情"
    record = params.get("record")
    if not isinstance(record, Mapping):
        return [_markdown(f"**{title}**\n未提供记录信息")]

    fields_text = record.get("fields_text")
    lines = _kv_lines(fields_text, max_items=12) if isinstance(fields_text, Mapping) else _kv_lines(record, max_items=12)
    link_line = build_record_link_line(record.get("record_id"), record.get("record_url"))
    if link_line:
        lines.append(f"- {link_line}")
    body = "\n".join(lines) if lines else "暂无可展示字段"
    return [_markdown(f"**{title}**"), _markdown(body)]


def render_action_confirm_v1(params: dict[str, Any]) -> Any:
    title = _safe_text(params.get("title")) or _safe_text(_render_value("action_cards.confirm.title", "操作确认"))
    message = _safe_text(params.get("message")) or _safe_text(
        _render_value("action_cards.confirm.message", "请确认是否继续执行该操作。")
    )
    action = _safe_text(params.get("action"))
    payload_raw = params.get("payload")
    payload = payload_raw if isinstance(payload_raw, Mapping) else {}
    table_name = _safe_text(params.get("table_name") or payload.get("table_name"))
    action_title, body_lines = _ACTION_ENGINE.build_confirm_lines(
        action=action,
        message=message,
        table_name=table_name,
        payload=payload,
    )
    headline = "⚠️ **请确认操作**"
    template_config_path = "action_cards.confirm.template_file"
    template_default_file = "action/C1_confirm.md"
    layout_config_path = "action_cards.confirm.layout_file"
    layout_default_file = "action/C1_confirm_layout.json"
    wrapper_config_path = "action_cards.confirm.wrapper_file"
    wrapper_default_file = "wrapper/card_action_C1_confirm.json"
    if action == "create_record":
        title = _safe_text(_render_value("action_cards.create_confirm.title", "新增案件 - 请确认")) or title
        template_config_path = "action_cards.create_confirm.template_file"
        template_default_file = "action/C1_confirm.md"
        layout_config_path = "action_cards.create_confirm.layout_file"
        layout_default_file = "action/C1_confirm_layout.json"
        wrapper_config_path = "action_cards.create_confirm.wrapper_file"
        wrapper_default_file = "wrapper/card_action_C1_confirm.json"
        headline = "📋 **新增案件 - 请确认**"
    elif action == "update_record":
        title = _safe_text(_render_value("action_cards.update_confirm.title", "修改确认")) or title
        template_config_path = "action_cards.update_confirm.template_file"
        template_default_file = "action/C2_confirm.md"
        layout_config_path = "action_cards.update_confirm.layout_file"
        layout_default_file = "action/C2_confirm_layout.json"
        wrapper_config_path = "action_cards.update_confirm.wrapper_file"
        wrapper_default_file = "wrapper/card_action_C2_confirm.json"
        diff_raw = payload.get("diff")
        diff_items = diff_raw if isinstance(diff_raw, list) else []
        has_append_diff = False
        for item in diff_items:
            if not isinstance(item, Mapping):
                continue
            mode = _safe_text(item.get("mode")).lower()
            field_name = _safe_text(item.get("field"))
            if mode == "append" or "进展" in field_name:
                has_append_diff = True
                break
        headline = "✏️ **追加案件进展 - 请确认**" if has_append_diff else "✏️ **修改案件 - 请确认**"
    elif action == "close_record":
        title = _safe_text(_render_value("action_cards.close_confirm.title", "操作确认")) or title
        template_config_path = "action_cards.close_confirm.template_file"
        template_default_file = "action/C3_confirm.md"
        layout_config_path = "action_cards.close_confirm.layout_file"
        layout_default_file = "action/C3_confirm_layout.json"
        wrapper_config_path = "action_cards.close_confirm.wrapper_file"
        wrapper_default_file = "wrapper/card_action_C3_close_confirm.json"
        close_title = action_title or _safe_text(payload.get("close_title")) or "案件结案"
        headline = f"📌 **{close_title} - 请确认**"
    elif action == "delete_record":
        title = _safe_text(_render_value("action_cards.delete_confirm.title", "危险操作确认")) or title
        template_config_path = "action_cards.delete_confirm.template_file"
        template_default_file = "action/C3_confirm.md"
        layout_config_path = "action_cards.delete_confirm.layout_file"
        layout_default_file = "action/C3_confirm_layout.json"
        wrapper_config_path = "action_cards.delete_confirm.wrapper_file"
        wrapper_default_file = "wrapper/card_action_C3_confirm.json"
        headline = "⚠️ **删除案件 - 请慎重确认**"
    elif action_title:
        title = action_title
        headline = f"⚠️ **{action_title}**"

    extra_note = _safe_text(_render_value("action_cards.confirm.extra_note", ""))
    message_line = body_lines[0] if body_lines else message
    content = "\n".join(body_lines[1:]) if len(body_lines) > 1 else ""
    fallback_lines = [message_line]
    if content:
        fallback_lines.append(content)
    if extra_note:
        fallback_lines.append(f"- {extra_note}" if not extra_note.startswith("-") else extra_note)
    body = _render_text_template(
        template_config_path,
        template_default_file,
        {
            "message": message_line,
            "content": content,
            "table_name": table_name,
            "subtitle": _safe_text(payload.get("delete_subtitle") or payload.get("close_subtitle")),
            "extra_note": extra_note,
        },
        fallback="\n".join([line for line in fallback_lines if line]),
    )
    subtitle = _safe_text(payload.get("delete_subtitle") or payload.get("close_subtitle"))
    record_id = _safe_text(params.get("record_id") or payload.get("record_id"))
    identity = _resolve_action_identity(payload, record_id)
    headline = headline or f"⚠️ **{title or '请确认操作'}**"

    layout_values = {
        "headline": headline,
        "identity": identity,
        "message": message_line,
        "subtitle": subtitle,
        "body": body,
    }
    elements = _render_layout_template(layout_config_path, layout_default_file, layout_values)
    if not elements:
        fallback_text = f"{subtitle}\n{body}" if subtitle else body
        elements = [_markdown(headline)]
        if identity:
            elements.append(_markdown(identity))
        elements.append(_markdown(fallback_text))
    else:
        elements = _prune_empty_markdown_elements(elements)

    actions_raw = params.get("actions")
    actions: Mapping[str, Any] = actions_raw if isinstance(actions_raw, Mapping) else {}
    confirm_raw = actions.get("confirm")
    cancel_raw = actions.get("cancel")
    modify_raw = actions.get("modify")
    table_type = _safe_text(params.get("table_type") or payload.get("table_type"))
    default_confirm_action = f"{action}_confirm" if action else "pending_action_confirm"
    default_cancel_action = f"{action}_cancel" if action else "pending_action_cancel"
    confirm_value = _normalize_callback_value(
        confirm_raw if isinstance(confirm_raw, Mapping) else None,
        callback_action=default_confirm_action,
        table_type=table_type,
        record_id=record_id,
    )
    cancel_value = _normalize_callback_value(
        cancel_raw if isinstance(cancel_raw, Mapping) else None,
        callback_action=default_cancel_action,
        table_type=table_type,
        record_id=record_id,
    )
    modify_value = _normalize_callback_value(
        modify_raw if isinstance(modify_raw, Mapping) else (cancel_raw if isinstance(cancel_raw, Mapping) else None),
        callback_action=default_cancel_action,
        table_type=table_type,
        record_id=record_id,
        extra_data={"intent": "modify"},
    )
    confirm_text = _safe_text(params.get("confirm_text")) or _safe_text(
        _render_value("action_cards.confirm.confirm_text", "确认")
    ) or "确认"
    cancel_text = _safe_text(params.get("cancel_text")) or _safe_text(
        _render_value("action_cards.confirm.cancel_text", "取消")
    ) or "取消"
    if action == "delete_record" and not _safe_text(params.get("confirm_text")):
        confirm_text = _safe_text(_render_value("action_cards.delete_confirm.confirm_text", "确认删除")) or confirm_text
    modify_text = _safe_text(params.get("modify_text")) or "修改"
    confirm_type = _normalize_button_type(params.get("confirm_type"), default="primary_filled")

    if action == "create_record":
        fields_raw = payload.get("fields")
        fields = fields_raw if isinstance(fields_raw, Mapping) else {}
        required_raw = payload.get("required_fields")
        required = [str(item).strip() for item in required_raw if str(item).strip()] if isinstance(required_raw, list) else []
        missing = [name for name in required if not _safe_text(fields.get(name))]
        if not missing:
            optional_candidates = ["联系人", "联系方式", "主办律师", "协办律师"]
            missing = [name for name in optional_candidates if not _safe_text(fields.get(name))]
        if missing:
            elements.append({"tag": "hr", "margin": "0px"})
            elements.append(
                {
                    "tag": "markdown",
                    "content": "❓ 以下字段未提供，是否需要补充？",
                    "margin": "0px",
                    "text_size": "normal",
                }
            )
            elements.append(
                {
                    "tag": "markdown",
                    "content": "\n".join([f"• {name}" for name in missing]),
                    "margin": "0px",
                    "text_size": "normal",
                }
            )

    if action == "delete_record":
        confirm_label = _decorate_button_text(confirm_text, prefix="⛔", fallback="确认删除")
    else:
        confirm_label = _decorate_button_text(confirm_text, prefix="✅", fallback="确认")
    cancel_label = _decorate_button_text(cancel_text, prefix="❌", fallback="取消")
    modify_label = _decorate_button_text(modify_text, prefix="✏️", fallback="修改")

    buttons: list[dict[str, Any]] = [
        _build_callback_button(text=confirm_label, button_type=confirm_type, value=confirm_value),
    ]
    if action in {"create_record", "delete_record"}:
        buttons.append(_build_callback_button(text=modify_label, button_type="default", value=modify_value))
    buttons.append(_build_callback_button(text=cancel_label, button_type="default", value=cancel_value))
    button_row = _build_button_row(buttons)
    if button_row is not None:
        elements.append(button_row)

    wrapper = _load_wrapper_from_config(
        wrapper_config_path,
        wrapper_default_file,
        {
            "header_title": title,
        },
    )
    if wrapper:
        return {
            "elements": elements,
            "wrapper": wrapper,
        }
    return elements


def render_error_notice_v1(params: dict[str, Any]) -> Any:
    title = _safe_text(params.get("title")) or "操作失败"
    message = _safe_text(params.get("message")) or "请稍后重试。"
    skill_name = _safe_text(params.get("skill_name"))
    error_class = _normalize_error_class(params.get("error_class"), message)
    next_step = _error_next_step(error_class, params.get("next_step"))
    headline = "❌ **新增失败**" if any(token in message for token in ("新增", "创建")) else "❌ **操作失败**"

    fallback_lines = [message, f"- 错误类型: {_error_class_label(error_class)}", f"- 建议下一步: {next_step}"]
    if skill_name:
        fallback_lines.append(f"- 场景: {skill_name}")
    body = _render_text_template(
        "action_cards.feedback.template_file",
        "action/feedback.md",
        {
            "message": message,
            "detail": "",
            "error_type": _error_class_label(error_class),
            "next_step": next_step,
            "scene": skill_name,
        },
        fallback="\n".join(fallback_lines),
    )

    elements = _render_layout_template(
        "action_cards.feedback.layout_file",
        "action/feedback_layout.json",
        {"headline": headline, "body": body},
    )
    if not elements:
        elements = [_markdown(headline), _markdown(body)]

    actions_raw = params.get("actions")
    actions = actions_raw if isinstance(actions_raw, Mapping) else {}
    primary_raw = actions.get("primary") if isinstance(actions, Mapping) else None
    secondary_raw = actions.get("secondary") if isinstance(actions, Mapping) else None
    buttons: list[dict[str, Any]] = []
    if isinstance(primary_raw, Mapping):
        primary_url = _safe_text(primary_raw.get("url") or primary_raw.get("default_url"))
        if primary_url:
            primary_text = _decorate_button_text(primary_raw.get("text"), prefix="🔎", fallback="查看详情")
            buttons.append(_build_open_url_button(text=primary_text, button_type="primary_filled", url=primary_url))
    if isinstance(secondary_raw, Mapping):
        secondary_url = _safe_text(secondary_raw.get("url") or secondary_raw.get("default_url"))
        if secondary_url:
            secondary_text = _decorate_button_text(secondary_raw.get("text"), prefix="✏️", fallback="修改重试")
            buttons.append(_build_open_url_button(text=secondary_text, button_type="default", url=secondary_url))
    button_row = _build_button_row(buttons)
    if button_row is not None:
        elements.append(button_row)

    wrapper = _load_wrapper_from_config(
        "action_cards.feedback.error_wrapper_file",
        "wrapper/card_action_feedback_error.json",
        {"header_title": title},
    )
    if wrapper:
        return {
            "elements": elements,
            "wrapper": wrapper,
        }
    return elements


def render_create_success_v1(params: dict[str, Any]) -> Any:
    title = _safe_text(params.get("title")) or _safe_text(_render_value("action_cards.create_success.title", "新增成功"))
    record = params.get("record")
    if not isinstance(record, Mapping):
        record = {}

    record_fields = _record_fields(record)
    lines = _kv_lines(record_fields, max_items=8)
    if not lines:
        record_id = _safe_text(record.get("record_id"))
        if record_id:
            lines.append(f"- **记录 ID**: {record_id}")
        else:
            lines.append("- 已创建新记录")

    detail_text = "\n".join(lines)
    table_name = _safe_text(params.get("table_name"))
    reminder_lines = _ACTION_ENGINE.build_auto_reminders(table_name, _record_fields(record))
    reminders_text = "\n".join([f"- {item}" for item in reminder_lines]) if reminder_lines else ""
    fallback_body = detail_text
    if reminders_text:
        fallback_body = f"{fallback_body}\n{_OK_MARKER} 自动提醒:\n{reminders_text}".strip()
    body = _render_text_template(
        "action_cards.create_success.template_file",
        "action/C1_success.md",
        {
            "details": detail_text,
            "reminders": reminders_text,
        },
        fallback=fallback_body,
    )

    headline = "✅ **新增成功！**"
    if any(token in table_name for token in ("案件", "项目")):
        headline = "✅ **案件新增成功！**"
    identity = _resolve_action_identity(
        {"fields": record_fields},
        _safe_text(record.get("record_id")),
    )
    main_lines = [line for line in [identity, detail_text] if line]
    main_block = "\n".join(main_lines) if main_lines else body

    elements = _render_layout_template(
        "action_cards.create_success.layout_file",
        "action/C1_success_layout.json",
        {"headline": headline, "body": main_block},
    )
    if not elements:
        elements = [_markdown(headline), _markdown(main_block)]

    if reminders_text:
        elements.append({"tag": "hr", "margin": "0px"})
        elements.append(
            {
                "tag": "markdown",
                "content": f"⚠️ **提醒已设置：**\n{reminders_text}",
                "margin": "0px",
                "text_size": "normal",
            }
        )

    missing_candidates = ["联系人", "联系方式", "主办律师", "协办律师", "重要紧急程度"]
    missing = [field for field in missing_candidates if not _safe_text(record_fields.get(field))]
    if missing:
        elements.append({"tag": "hr", "margin": "0px"})
        elements.append(
            {
                "tag": "markdown",
                "content": "❓ **以下字段暂未填写，后续可补充：**\n" + "\n".join([f"• {name}" for name in missing[:5]]),
                "margin": "0px",
                "text_size": "normal",
            }
        )

    record_url = _safe_text(params.get("record_url") or record.get("record_url"))
    record_id = _safe_text(record.get("record_id"))
    continue_url = _safe_text(params.get("continue_url") or params.get("add_url"))
    buttons: list[dict[str, Any]] = []
    if record_url:
        buttons.append(_build_open_url_button(text="查看详情", button_type="primary_filled", url=record_url))
    if continue_url:
        buttons.append(_build_open_url_button(text="继续新增", button_type="default", url=continue_url))
    button_row = _build_button_row(buttons)
    if button_row is not None:
        elements.append(button_row)
    elif not record_url:
        link_line = build_record_link_line(record_id, record_url)
        if link_line:
            elements.append(_markdown(link_line))

    wrapper = _load_wrapper_from_config(
        "action_cards.create_success.wrapper_file",
        "wrapper/card_action_C1_success.json",
        {"header_title": title},
    )
    if wrapper:
        return {
            "elements": elements,
            "wrapper": wrapper,
        }
    return elements


def render_update_success_v1(params: dict[str, Any]) -> Any:
    title = _safe_text(params.get("title")) or _safe_text(_render_value("action_cards.update_success.title", "操作成功"))
    changes_raw = params.get("changes")
    changes = changes_raw if isinstance(changes_raw, list) else []

    lines: list[str] = []
    for change in changes[:12]:
        if not isinstance(change, Mapping):
            continue
        field = _safe_text(change.get("field")) or "字段"
        old_value = _safe_text(change.get("old")) or "(空)"
        new_value = _safe_text(change.get("new")) or "(空)"
        lines.append(f"- **{field}**: {old_value} -> {new_value}")
    if not lines:
        lines.append("- 已完成记录更新")

    progress_append = _safe_text(params.get("progress_append"))
    if progress_append:
        progress_prefix = _safe_text(_render_value("action_cards.update_success.progress_prefix", "进展追加")) or "进展追加"
        lines.append(f"- {progress_prefix}: {progress_append}")

    detail_text = "\n".join(lines)
    body = _render_text_template(
        "action_cards.feedback.template_file",
        "action/feedback.md",
        {
            "message": "已完成记录更新",
            "detail": detail_text,
            "error_type": "",
            "next_step": "",
            "scene": "",
        },
        fallback=detail_text,
    )
    headline = "✅ **修改成功！**"

    elements = _render_layout_template(
        "action_cards.feedback.layout_file",
        "action/feedback_layout.json",
        {"headline": headline, "body": body},
    )
    if not elements:
        elements = [_markdown(headline), _markdown(body)]

    record_url = _safe_text(params.get("record_url"))
    record_id = _safe_text(params.get("record_id"))
    if record_url:
        button_row = _build_button_row([_build_open_url_button(text="查看详情", button_type="primary_filled", url=record_url)])
        if button_row is not None:
            elements.append(button_row)
    else:
        link_line = build_record_link_line(record_id, record_url)
        if link_line:
            elements.append(_markdown(link_line))

    wrapper = _load_wrapper_from_config(
        "action_cards.feedback.success_wrapper_file",
        "wrapper/card_action_feedback_success.json",
        {"header_title": title},
    )
    if wrapper:
        return {
            "elements": elements,
            "wrapper": wrapper,
        }
    return elements


def render_update_guide_v1(params: dict[str, Any]) -> Any:
    title = _safe_text(params.get("title")) or "修改案件"
    record_id = _safe_text(params.get("record_id"))
    table_type = _safe_text(params.get("table_type")) or "case"
    case_no = _safe_text(params.get("record_case_no")) or record_id or "（未识别案号）"
    identity = _safe_text(params.get("record_identity"))

    located_lines = ["✏️ **已定位到案件：**", "", f"🔖 {case_no}"]
    if identity:
        located_lines.append(f"🏢 {identity}")
    located_markdown = "\n".join(located_lines)

    examples = [
        '• "开庭日改成2024-12-01"',
        '• "案件状态改为已结案"',
        '• "追加进展：今天收到法院通知"',
        '• "主办律师改成张三"',
    ]
    prompt_markdown = "请告诉我要修改什么，例如：\n" + "\n".join(examples)

    cancel_action_raw = params.get("cancel_action")
    cancel_action = cancel_action_raw if isinstance(cancel_action_raw, Mapping) else None
    cancel_value = _normalize_callback_value(
        cancel_action,
        callback_action="update_collect_fields_cancel",
        table_type=table_type,
        record_id=record_id,
        extra_data={},
    )
    cancel_text = _safe_text(params.get("cancel_text")) or "取消"
    cancel_button = _build_callback_button(
        text=_decorate_button_text(cancel_text, prefix="❌", fallback="取消"),
        button_type="primary_filled",
        value=cancel_value,
    )

    elements: list[dict[str, Any]] = [
        _markdown(located_markdown),
        {"tag": "hr", "margin": "0px"},
        _markdown(prompt_markdown),
    ]
    button_row = _build_button_row([cancel_button])
    if button_row is not None:
        elements.append(button_row)

    wrapper = {
        "schema": "2.0",
        "config": {"update_multi": True},
        "body": {"direction": "vertical"},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": title},
            "icon": {"tag": "standard_icon", "token": "edit_outlined"},
            "padding": "12px 8px 12px 8px",
        },
    }
    return {
        "elements": elements,
        "wrapper": wrapper,
    }


def render_delete_confirm_v1(params: dict[str, Any]) -> Any:
    summary_raw = params.get("summary")
    summary = summary_raw if isinstance(summary_raw, Mapping) else {}
    summary_lines = _kv_lines(summary, max_items=4)
    empty_summary = _safe_text(_render_value("action_cards.delete_confirm.empty_summary", "- 即将删除目标记录"))
    body = "\n".join(summary_lines) if summary_lines else empty_summary

    actions_raw = params.get("actions")
    actions: Mapping[str, Any] = actions_raw if isinstance(actions_raw, Mapping) else {}
    confirm_raw = actions.get("confirm")
    cancel_raw = actions.get("cancel")
    record_id = _safe_text(params.get("record_id") or summary.get("记录 ID"))
    table_type = _safe_text(params.get("table_type"))
    confirm_value = _normalize_callback_value(
        confirm_raw if isinstance(confirm_raw, Mapping) else None,
        callback_action="delete_record_confirm",
        table_type=table_type,
        record_id=record_id,
    )
    cancel_value = _normalize_callback_value(
        cancel_raw if isinstance(cancel_raw, Mapping) else None,
        callback_action="delete_record_cancel",
        table_type=table_type,
        record_id=record_id,
    )
    modify_raw = actions.get("modify")
    modify_value = _normalize_callback_value(
        modify_raw if isinstance(modify_raw, Mapping) else (cancel_raw if isinstance(cancel_raw, Mapping) else None),
        callback_action="delete_record_cancel",
        table_type=table_type,
        record_id=record_id,
        extra_data={"intent": "modify"},
    )

    title = _safe_text(params.get("title")) or _safe_text(
        _render_value("action_cards.delete_confirm.title", "危险操作确认")
    ) or "危险操作确认"
    subtitle = _safe_text(params.get("subtitle")) or _safe_text(
        _render_value("action_cards.delete_confirm.subtitle", "该操作不可撤销，请再次确认。")
    ) or "该操作不可撤销，请再次确认。"
    warnings_raw = params.get("warnings")
    warnings = [str(item).strip() for item in warnings_raw if str(item).strip()] if isinstance(warnings_raw, list) else []
    suggestion = _safe_text(params.get("suggestion"))

    body_lines = [body]
    for warn in warnings[:6]:
        body_lines.append(f"- 警告: {warn}")
    if suggestion:
        body_lines.append(f"- 建议: {suggestion}")

    body_text = "\n".join(body_lines)
    body_rendered = _render_text_template(
        "action_cards.delete_confirm.template_file",
        "action/C3_confirm.md",
        {
            "message": "",
            "subtitle": "",
            "content": body_text,
            "extra_note": "",
        },
        fallback=body_text,
    )

    confirm_text = _safe_text(params.get("confirm_text")) or _safe_text(
        _render_value("action_cards.delete_confirm.confirm_text", "确认删除")
    ) or "确认删除"
    cancel_text = _safe_text(params.get("cancel_text")) or _safe_text(
        _render_value("action_cards.delete_confirm.cancel_text", "取消")
    ) or "取消"
    modify_text = _safe_text(params.get("modify_text")) or "修改"
    confirm_type = _normalize_button_type(params.get("confirm_type"), default="primary_filled")

    identity_lines: list[str] = []
    record_identity = _safe_text(summary.get("记录 ID"))
    if record_identity:
        identity_lines.append(f"🔖 {record_identity}")
    case_no_identity = _safe_text(summary.get("案号"))
    if case_no_identity:
        identity_lines.append(f"📄 {case_no_identity}")
    cause_identity = _safe_text(summary.get("案由"))
    if cause_identity:
        identity_lines.append(f"🏢 {cause_identity}")
    identity = "\n".join(identity_lines)
    headline = "⚠️ **删除案件 - 请慎重确认**"

    elements = _render_layout_template(
        "action_cards.delete_confirm.layout_file",
        "action/C3_confirm_layout.json",
        {
            "headline": headline,
            "identity": identity,
            "message": subtitle,
            "subtitle": subtitle,
            "body": body_rendered,
        },
    )
    if not elements:
        elements = [_markdown(headline)]
        if identity:
            elements.append(_markdown(identity))
        elements.append(_markdown(body_rendered))
    else:
        elements = _prune_empty_markdown_elements(elements)

    button_row = _build_button_row(
        [
            _build_callback_button(
                text=_decorate_button_text(confirm_text, prefix="⛔", fallback="确认删除"),
                button_type=confirm_type,
                value=confirm_value,
            ),
            _build_callback_button(
                text=_decorate_button_text(modify_text, prefix="✏️", fallback="修改"),
                button_type="default",
                value=modify_value,
            ),
            _build_callback_button(
                text=_decorate_button_text(cancel_text, prefix="❌", fallback="取消"),
                button_type="default",
                value=cancel_value,
            ),
        ]
    )
    if button_row is not None:
        elements.append(button_row)

    wrapper = _load_wrapper_from_config(
        "action_cards.delete_confirm.wrapper_file",
        "wrapper/card_action_C3_confirm.json",
        {"header_title": title},
    )
    if wrapper:
        return {
            "elements": elements,
            "wrapper": wrapper,
        }
    return elements


def render_delete_success_v1(params: dict[str, Any]) -> Any:
    title = _safe_text(params.get("title")) or "删除成功"
    message = _safe_text(params.get("message")) or "目标记录已删除。"
    headline = "✅ **删除成功！**"
    body = _render_text_template(
        "action_cards.feedback.template_file",
        "action/feedback.md",
        {
            "message": message,
            "detail": "",
            "error_type": "",
            "next_step": "",
            "scene": "",
        },
        fallback=message,
    )
    elements = _render_layout_template(
        "action_cards.feedback.layout_file",
        "action/feedback_layout.json",
        {"headline": headline, "body": body},
    )
    if not elements:
        elements = [_markdown(headline), _markdown(body)]

    wrapper = _load_wrapper_from_config(
        "action_cards.feedback.success_wrapper_file",
        "wrapper/card_action_feedback_success.json",
        {"header_title": title},
    )
    if wrapper:
        return {
            "elements": elements,
            "wrapper": wrapper,
        }
    return elements


def render_delete_cancelled_v1(params: dict[str, Any]) -> Any:
    title = _safe_text(params.get("title")) or "已取消删除"
    message = _safe_text(params.get("message")) or "本次删除操作已取消。"
    headline = "ℹ️ **已取消删除**"
    body = _render_text_template(
        "action_cards.feedback.template_file",
        "action/feedback.md",
        {
            "message": message,
            "detail": "",
            "error_type": "",
            "next_step": "",
            "scene": "",
        },
        fallback=message,
    )
    elements = _render_layout_template(
        "action_cards.feedback.layout_file",
        "action/feedback_layout.json",
        {"headline": headline, "body": body},
    )
    if not elements:
        elements = [_markdown(headline), _markdown(body)]

    wrapper = _load_wrapper_from_config(
        "action_cards.feedback.success_wrapper_file",
        "wrapper/card_action_feedback_success.json",
        {"header_title": title},
    )
    if wrapper:
        return {
            "elements": elements,
            "wrapper": wrapper,
        }
    return elements


def render_todo_reminder_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "提醒结果"
    message = _safe_text(params.get("message"))
    content = _safe_text(params.get("content"))
    remind_time = _safe_text(params.get("remind_time"))

    lines = [f"**{title}**"]
    if content:
        lines.append(f"- 内容: {content}")
    if remind_time:
        lines.append(f"- 时间: {remind_time}")
    if message:
        lines.append(f"- 说明: {message}")
    return [_markdown("\n".join(lines))]


def _format_file_size(raw_size: Any) -> str:
    try:
        size = int(raw_size)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _truncate_text(raw: Any, max_chars: int) -> str:
    text = _safe_text(raw)
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def render_upload_result_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    status = _safe_text(params.get("status")).lower() or "processing"
    title = _safe_text(params.get("title")) or _safe_text(_render_value(f"upload_result.titles.{status}", "")) or "上传结果"
    status_label = _safe_text(_render_value(f"upload_result.status_labels.{status}", "")) or status
    status_icon = _safe_text(_render_value(f"upload_result.status_icons.{status}", ""))

    file_name = _safe_text(params.get("file_name")) or "文件"
    file_type = _safe_text(params.get("file_type"))
    file_size = _format_file_size(params.get("file_size"))

    message_type = _safe_text(params.get("message_type")).lower()
    message_type_label = {
        "file": "文件",
        "image": "图片",
        "audio": "语音",
    }.get(message_type, "文件")

    provider = _safe_text(params.get("provider") or "none").lower()
    provider_label = _safe_text(_render_value(f"upload_result.provider_labels.{provider}", "")) or provider or "none"

    reason_code = _safe_text(params.get("reason_code"))
    reason_text = _safe_text(params.get("reason_text")) or _safe_text(_render_value(f"upload_result.reason_texts.{reason_code}", ""))

    guidance = _safe_text(params.get("guidance") or params.get("message"))
    next_step = _safe_text(params.get("next_step")) or _safe_text(_render_value(f"upload_result.next_steps.{status}", ""))

    preview_max_chars = _safe_int(_render_value("upload_result.preview.max_chars", 240), 240)
    markdown_preview = _truncate_text(params.get("markdown_preview"), preview_max_chars)

    lines = [
        f"- 文件: {file_name}",
        f"- 来源类型: {message_type_label}",
    ]
    if file_type:
        lines.append(f"- 类型: {file_type}")
    if file_size:
        lines.append(f"- 大小: {file_size}")
    lines.append(f"- 解析通道: {provider_label}")
    lines.append(f"- 状态: {status_icon} {status_label}".strip())
    if reason_text:
        lines.append(f"- 原因: {reason_text}")
    if guidance:
        lines.append(f"- 说明: {guidance}")
    if markdown_preview:
        lines.append(f"- 识别摘要: {markdown_preview}")
    if next_step:
        lines.append(f"- 下一步: {next_step}")

    return [_markdown(f"**{title}**\n" + "\n".join(lines))]
