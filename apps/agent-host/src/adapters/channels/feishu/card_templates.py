from __future__ import annotations

from typing import Any, Mapping

from src.adapters.channels.feishu.record_links import build_record_link_line


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


def _record_fields(record: Mapping[str, Any]) -> Mapping[str, Any]:
    fields_text = record.get("fields_text")
    if isinstance(fields_text, Mapping):
        return fields_text
    fields = record.get("fields")
    if isinstance(fields, Mapping):
        return fields
    return {}


def _normalize_error_class(value: Any, message: str) -> str:
    explicit = _safe_text(value).lower().replace("-", "_")
    if explicit in {"missing_params", "record_not_found", "permission_denied"}:
        return explicit

    normalized = message.lower()
    if any(token in normalized for token in ["权限", "无权", "forbidden", "permission denied", "access denied"]):
        return "permission_denied"
    if any(token in normalized for token in ["未找到", "不存在", "没有找到", "not found"]):
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


def render_query_list_v2(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "案件查询结果"
    total = int(params.get("total") or 0)
    records = params.get("records")
    if not isinstance(records, list) or not records:
        return [_markdown(f"**{title}**\n暂无记录")]

    count = max(total, len(records))
    elements: list[dict[str, Any]] = [_markdown(f"**{title}（共 {count} 条）**")]
    for i, record in enumerate(records[:3], start=1):
        if not isinstance(record, Mapping):
            continue
        body = "\n".join(_query_summary_lines(record))
        elements.append(_markdown(f"**{i}.**\n{body}"))

    actions_raw = params.get("actions")
    actions = actions_raw if isinstance(actions_raw, Mapping) else {}
    next_page_raw = actions.get("next_page")
    today_raw = actions.get("today_hearing")
    week_raw = actions.get("week_hearing")
    next_page_value: dict[str, Any] = dict(next_page_raw) if isinstance(next_page_raw, Mapping) else {}
    today_value: dict[str, Any] = dict(today_raw) if isinstance(today_raw, Mapping) else {}
    week_value: dict[str, Any] = dict(week_raw) if isinstance(week_raw, Mapping) else {}
    if not next_page_value:
        next_page_value = {"callback_action": "query_list_next_page"}
    if not today_value:
        today_value = {"callback_action": "query_list_today_hearing"}
    if not week_value:
        week_value = {"callback_action": "query_list_week_hearing"}

    elements.append(
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "下一页"},
                    "type": "default",
                    "value": next_page_value,
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "今天开庭"},
                    "type": "default",
                    "value": today_value,
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "本周开庭"},
                    "type": "default",
                    "value": week_value,
                },
            ],
        }
    )
    return elements


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


def render_action_confirm_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "请确认"
    message = _safe_text(params.get("message")) or "请确认是否继续。"
    action = _safe_text(params.get("action"))
    body = f"{message}\n\n- 操作: {action}" if action else message
    actions_raw = params.get("actions")
    actions: Mapping[str, Any] = actions_raw if isinstance(actions_raw, Mapping) else {}
    confirm_raw = actions.get("confirm")
    cancel_raw = actions.get("cancel")
    confirm_value: dict[str, Any] = dict(confirm_raw) if isinstance(confirm_raw, Mapping) else {}
    cancel_value: dict[str, Any] = dict(cancel_raw) if isinstance(cancel_raw, Mapping) else {}
    return [
        _markdown(f"**{title}**"),
        _markdown(body),
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "确认"},
                    "type": "primary",
                    "value": confirm_value,
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "取消"},
                    "type": "default",
                    "value": cancel_value,
                },
            ],
        },
    ]


def render_error_notice_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "处理失败"
    message = _safe_text(params.get("message")) or "请稍后重试。"
    skill_name = _safe_text(params.get("skill_name"))
    error_class = _normalize_error_class(params.get("error_class"), message)
    next_step = _error_next_step(error_class, params.get("next_step"))

    lines = [f"**{title}**", message, f"- 错误类型: {_error_class_label(error_class)}", f"- 建议下一步: {next_step}"]
    if skill_name:
        lines.append(f"- 场景: {skill_name}")
    return [_markdown("\n".join(lines))]


def render_create_success_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "创建成功"
    record = params.get("record")
    if not isinstance(record, Mapping):
        record = {}

    lines = _kv_lines(_record_fields(record), max_items=8)
    if not lines:
        record_id = _safe_text(record.get("record_id"))
        if record_id:
            lines.append(f"- **记录 ID**: {record_id}")
        else:
            lines.append("- 已创建新记录")

    elements = [_markdown(f"**{title}**"), _markdown("\n".join(lines))]
    record_url = _safe_text(params.get("record_url") or record.get("record_url"))
    record_id = _safe_text(record.get("record_id"))
    link_line = build_record_link_line(record_id, record_url)
    if link_line:
        elements.append(_markdown(link_line))
    return elements


def render_update_success_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "更新成功"
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

    elements = [_markdown(f"**{title}**"), _markdown("\n".join(lines))]
    record_url = _safe_text(params.get("record_url"))
    record_id = _safe_text(params.get("record_id"))
    link_line = build_record_link_line(record_id, record_url)
    if link_line:
        elements.append(_markdown(link_line))
    return elements


def render_delete_confirm_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    summary_raw = params.get("summary")
    summary = summary_raw if isinstance(summary_raw, Mapping) else {}
    summary_lines = _kv_lines(summary, max_items=4)
    body = "\n".join(summary_lines) if summary_lines else "- 即将删除目标记录"

    actions_raw = params.get("actions")
    actions: Mapping[str, Any] = actions_raw if isinstance(actions_raw, Mapping) else {}
    confirm_raw = actions.get("confirm")
    cancel_raw = actions.get("cancel")
    confirm_value: dict[str, Any] = dict(confirm_raw) if isinstance(confirm_raw, Mapping) else {}
    cancel_value: dict[str, Any] = dict(cancel_raw) if isinstance(cancel_raw, Mapping) else {}

    return [
        _markdown("🟥 **高风险操作：删除确认**"),
        _markdown("此操作不可撤销，请再次确认。"),
        _markdown(body),
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "确认删除"},
                    "type": "danger",
                    "value": confirm_value,
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "取消"},
                    "type": "default",
                    "value": cancel_value,
                },
            ],
        },
    ]


def render_delete_success_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "删除成功"
    message = _safe_text(params.get("message")) or "目标记录已删除。"
    return [_markdown(f"**{title}**\n{message}")]


def render_delete_cancelled_v1(params: dict[str, Any]) -> list[dict[str, Any]]:
    title = _safe_text(params.get("title")) or "已取消删除"
    message = _safe_text(params.get("message")) or "本次删除操作已取消。"
    return [_markdown(f"**{title}**\n{message}")]


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
