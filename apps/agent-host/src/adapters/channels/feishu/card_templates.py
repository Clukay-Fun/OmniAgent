from __future__ import annotations

from typing import Any, Mapping


def _markdown(content: str) -> dict[str, Any]:
    return {"tag": "markdown", "content": content}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


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
            body = "\n".join(lines) if lines else "- 记录详情"
        else:
            body = _safe_text(record.get("record_id")) or "记录详情"
        elements.append(_markdown(f"**{i}.**\n{body}"))
    return elements


def render_query_detail_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "记录详情"
    record = params.get("record")
    if not isinstance(record, Mapping):
        return [_markdown(f"**{title}**\n未提供记录信息")]

    fields_text = record.get("fields_text")
    lines = _kv_lines(fields_text, max_items=12) if isinstance(fields_text, Mapping) else _kv_lines(record, max_items=12)
    body = "\n".join(lines) if lines else "暂无可展示字段"
    return [_markdown(f"**{title}**"), _markdown(body)]


def render_action_confirm_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "请确认"
    message = _safe_text(params.get("message")) or "请确认是否继续。"
    action = _safe_text(params.get("action"))
    body = f"{message}\n\n- 操作: {action}" if action else message
    return [_markdown(f"**{title}**"), _markdown(body)]


def render_error_notice_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "处理失败"
    message = _safe_text(params.get("message")) or "请稍后重试。"
    skill_name = _safe_text(params.get("skill_name"))
    lines = [f"**{title}**", message]
    if skill_name:
        lines.append(f"- 场景: {skill_name}")
    return [_markdown("\n".join(lines))]


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


def render_upload_result_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "上传结果"
    file_name = _safe_text(params.get("file_name")) or "文件"
    status = _safe_text(params.get("status")) or "已处理"
    return [_markdown(f"**{title}**\n- 文件: {file_name}\n- 状态: {status}")]
