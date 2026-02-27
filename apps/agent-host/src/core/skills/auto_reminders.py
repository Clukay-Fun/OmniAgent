"""
描述: 该模块用于构建自动提醒项，根据不同的表和字段生成相应的提醒信息。
主要功能:
    - 根据表名和字段生成提醒项
    - 解析日期字符串为日期对象
"""

def build_auto_reminder_items(table_name: str, fields: Mapping[str, Any]) -> list[dict[str, str]]:
    """
    根据表名和字段生成自动提醒项。

    功能:
        - 根据表名选择相应的提醒定义
        - 遍历选定的提醒定义，解析字段中的日期
        - 计算提醒日期并生成提醒项
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
        target = _parse_date(fields.get(field_name))
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


def _parse_date(value: Any) -> date | None:
    """
    解析日期字符串为日期对象。

    功能:
        - 去除字符串中的多余字符
        - 将中文日期格式转换为ISO格式
        - 尝试将字符串解析为日期对象
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
