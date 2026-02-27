"""
描述: 提供数据过滤、分组和汇总的功能
主要功能:
    - 数据过滤
    - 数据分组
    - 数据汇总
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from typing import Any, Callable, Mapping


Record = dict[str, Any]


def _safe_text(value: Any) -> str:
    """
    将输入值转换为安全的文本字符串

    功能:
        - 如果输入值为 None 或空字符串，则返回空字符串
        - 去除字符串两端的空白字符
    """
    return str(value or "").strip()


def parse_date_value(value: Any) -> date | None:
    """
    解析日期值

    功能:
        - 将输入值转换为标准日期格式
        - 支持多种日期格式的转换
        - 如果解析失败，返回 None
    """
    text = _safe_text(value)
    if not text:
        return None
    normalized = (
        text.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace(".", "-")
        .replace("/", "-")
    )
    if "T" in normalized:
        normalized = normalized.split("T", 1)[0]
    if " " in normalized:
        normalized = normalized.split(" ", 1)[0]
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _resolve_date_ref(token: str) -> date:
    """
    解析日期引用

    功能:
        - 根据输入的日期引用字符串返回相应的日期
        - 支持相对日期和固定日期的解析
    """
    today = date.today()
    cleaned = token.strip().lower()
    if cleaned.startswith("today+"):
        try:
            return today + timedelta(days=int(cleaned.split("+", 1)[1]))
        except ValueError:
            return today
    refs: dict[str, date] = {
        "today": today,
        "this_week_start": today - timedelta(days=today.weekday()),
        "this_week_end": today + timedelta(days=(6 - today.weekday())),
        "next_week_start": today + timedelta(days=(7 - today.weekday())),
        "next_week_end": today + timedelta(days=(13 - today.weekday())),
        "this_month_start": today.replace(day=1),
    }
    month_end = (today.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    refs["this_month_end"] = month_end
    return refs.get(cleaned, today)


@dataclass
class FilterCondition:
    """
    过滤条件的数据类

    功能:
        - 存储单个过滤条件的字段、操作符和值
    """
    field: str
    op: str
    value: Any


class FilterEngine:
    """
    过滤引擎类

    功能:
        - 执行数据过滤
        - 解析过滤字符串
        - 解析过滤条件
        - 解析值
        - 匹配条件
        - 比较值
        - 获取排序键
    """
    _OPS: tuple[str, ...] = (">=", "<=", "!=", "=", ">", "<", "contains", "in_range")

    def execute(self, records: list[Record], filter_str: str, context: Mapping[str, Any] | None = None) -> list[Record]:
        """
        执行过滤操作

        功能:
            - 解析过滤字符串
            - 过滤记录
            - 对结果进行排序
            - 限制结果数量
        """
        if not filter_str:
            return list(records)
        conditions, sort_rule, limit = self.parse_filter(filter_str, context or {})
        filtered = [record for record in records if self.match_all(record, conditions)]

        if sort_rule is not None:
            field, direction = sort_rule
            reverse = direction == "desc"
            filtered.sort(key=lambda item: self._sort_key(item.get(field)), reverse=reverse)

        if limit is not None and limit >= 0:
            filtered = filtered[:limit]
        return filtered

    def parse_filter(
        self,
        filter_str: str,
        context: Mapping[str, Any],
    ) -> tuple[list[FilterCondition], tuple[str, str] | None, int | None]:
        """
        解析过滤字符串

        功能:
            - 分割过滤字符串
            - 解析排序规则
            - 解析限制数量
            - 解析过滤条件
        """
        parts = [part.strip() for part in filter_str.split(",") if part.strip()]
        conditions: list[FilterCondition] = []
        sort_rule: tuple[str, str] | None = None
        limit: int | None = None
        for part in parts:
            lower = part.lower()
            if lower.startswith("sort:"):
                tail = part.split(":", 1)[1].strip()
                tokens = tail.split()
                if tokens:
                    direction = tokens[1].lower() if len(tokens) > 1 else "asc"
                    sort_rule = (tokens[0], "desc" if direction == "desc" else "asc")
                continue
            if lower.startswith("limit:"):
                raw = part.split(":", 1)[1].strip()
                try:
                    limit = int(raw)
                except ValueError:
                    limit = None
                continue
            cond = self.parse_condition(part, context)
            if cond is not None:
                conditions.append(cond)
        return conditions, sort_rule, limit

    def parse_condition(self, condition_str: str, context: Mapping[str, Any]) -> FilterCondition | None:
        """
        解析单个过滤条件

        功能:
            - 根据操作符分割条件字符串
            - 解析字段和值
            - 返回 FilterCondition 对象
        """
        for op in self._OPS:
            token = f" {op} "
            if token in condition_str:
                left, right = condition_str.split(token, 1)
                field = left.strip()
                if op == "in_range":
                    chunks = [part.strip() for part in right.strip().split() if part.strip()]
                    if len(chunks) >= 2:
                        value = (self.resolve_value(chunks[0], context), self.resolve_value(chunks[1], context))
                    else:
                        value = (self.resolve_value(right.strip(), context), self.resolve_value(right.strip(), context))
                else:
                    value = self.resolve_value(right.strip(), context)
                return FilterCondition(field=field, op=op, value=value)
        return None

    def resolve_value(self, raw: str, context: Mapping[str, Any]) -> Any:
        """
        解析值

        功能:
            - 解析上下文中的值
            - 解析日期引用
            - 返回解析后的值
        """
        value = raw.strip()
        if value.startswith("{") and value.endswith("}"):
            return context.get(value[1:-1], "")
        if value.lower() in {
            "today",
            "this_week_start",
            "this_week_end",
            "next_week_start",
            "next_week_end",
            "this_month_start",
            "this_month_end",
        } or value.lower().startswith("today+"):
            return _resolve_date_ref(value)
        return value

    def match_all(self, record: Record, conditions: list[FilterCondition]) -> bool:
        """
        匹配所有条件

        功能:
            - 检查记录是否满足所有过滤条件
        """
        return all(self.match_condition(record, cond) for cond in conditions)

    def match_condition(self, record: Record, condition: FilterCondition) -> bool:
        """
        匹配单个条件

        功能:
            - 根据操作符比较记录中的值和条件值
        """
        value = record.get(condition.field)
        op = condition.op
        target = condition.value
        if op == "contains":
            return _safe_text(target) in _safe_text(value)
        if op == "in_range":
            left_date = parse_date_value(value)
            if left_date is None or not isinstance(target, tuple) or len(target) < 2:
                return False
            start = target[0]
            end = target[1]
            start_date = start if isinstance(start, date) else parse_date_value(start)
            end_date = end if isinstance(end, date) else parse_date_value(end)
            if start_date is None or end_date is None:
                return False
            return start_date <= left_date <= end_date
        left_date = parse_date_value(value)
        right_date = parse_date_value(target) if not isinstance(target, date) else target
        if left_date is not None and right_date is not None:
            return self._cmp(left_date, right_date, op)
        return self._cmp(_safe_text(value), _safe_text(target), op)

    def _cmp(self, left: Any, right: Any, op: str) -> bool:
        """
        比较两个值

        功能:
            - 根据操作符比较两个值
        """
        if op == "=":
            return left == right
        if op == "!=":
            return left != right
        if op == ">=":
            return left >= right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == "<":
            return left < right
        return False

    def _sort_key(self, value: Any) -> Any:
        """
        获取排序键

        功能:
            - 将值转换为排序键
        """
        as_date = parse_date_value(value)
        if as_date is not None:
            return (0, as_date)
        text = _safe_text(value)
        if text:
            return (1, text)
        return (2, "")


class GroupEngine:
    """
    分组引擎类

    功能:
        - 执行数据分组
        - 按值分组
        - 按日期分组
        - 匹配日期条件
    """
    def execute(self, records: list[Record], group_config: Mapping[str, Any]) -> list[tuple[str, list[Record]]]:
        """
        执行分组操作

        功能:
            - 根据配置分组记录
        """
        field = _safe_text(group_config.get("field"))
        buckets_raw = group_config.get("buckets")
        order_raw = group_config.get("order")
        icons_raw = group_config.get("icons")
        icons = icons_raw if isinstance(icons_raw, Mapping) else {}
        if field and isinstance(buckets_raw, list):
            return self.group_by_date_bucket(records, field=field, buckets=buckets_raw)
        if field and isinstance(order_raw, list):
            return self.group_by_value(records, field=field, order=order_raw, icons=icons)
        return [("全部", list(records))]

    def group_by_value(
        self,
        records: list[Record],
        field: str,
        order: list[Any],
        icons: Mapping[str, Any],
    ) -> list[tuple[str, list[Record]]]:
        """
        按值分组

        功能:
            - 根据字段值对记录进行分组
        """
        groups: dict[str, list[Record]] = {}
        labels: list[str] = []
        for raw in order:
            key = _safe_text(raw)
            icon = _safe_text(icons.get(key) or (icons.get("") if not key else ""))
            label = f"{icon} {key}".strip() if key else (icon or "未标注")
            groups[label] = []
            labels.append(label)

        for record in records:
            value = _safe_text(record.get(field))
            icon = _safe_text(icons.get(value) or (icons.get("") if not value else ""))
            label = f"{icon} {value}".strip() if value else (icon or "未标注")
            if label not in groups:
                groups[label] = []
                labels.append(label)
            groups[label].append(record)
        return [(label, groups.get(label, [])) for label in labels]

    def group_by_date_bucket(self, records: list[Record], field: str, buckets: list[Any]) -> list[tuple[str, list[Record]]]:
        """
        按日期分组

        功能:
            - 根据日期条件对记录进行分组
        """
        pairs: list[tuple[str, list[Record]]] = []
        for bucket_raw in buckets:
            if not isinstance(bucket_raw, Mapping):
                continue
            label = _safe_text(bucket_raw.get("label") or bucket_raw.get("id"))
            if not label:
                continue
            pairs.append((label, []))

        for record in records:
            value = record.get(field)
            assigned = False
            for index, bucket_raw in enumerate(buckets):
                if not isinstance(bucket_raw, Mapping):
                    continue
                condition = _safe_text(bucket_raw.get("condition"))
                if self._match_date_condition(value, condition):
                    pairs[index][1].append(record)
                    assigned = True
                    break
            if not assigned and pairs:
                pairs[-1][1].append(record)
        return pairs

    def _match_date_condition(self, value: Any, condition: str) -> bool:
        """
        匹配日期条件

        功能:
            - 根据条件检查日期值是否匹配
        """
        parsed = parse_date_value(value)
        if parsed is None:
            return False
        cond = condition.strip()
        if not cond:
            return True
        if " AND " in cond:
            return all(self._match_date_condition(value, part.strip()) for part in cond.split(" AND "))
        if cond.startswith(">="):
            return parsed >= _resolve_date_ref(cond[2:].strip())
        if cond.startswith("<="):
            return parsed <= _resolve_date_ref(cond[2:].strip())
        if cond.startswith(">"):
            return parsed > _resolve_date_ref(cond[1:].strip())
        if cond.startswith("<"):
            return parsed < _resolve_date_ref(cond[1:].strip())
        if cond.startswith("="):
            return parsed == _resolve_date_ref(cond[1:].strip())
        return False


class SummaryEngine:
    """
    汇总引擎类

    功能:
        - 执行数据汇总
        - 计算变量
        - 计算自动变量
    """
    def __init__(self, filter_engine: FilterEngine) -> None:
        """
        初始化汇总引擎

        功能:
            - 初始化过滤引擎
        """
        self._filter = filter_engine

    def execute(self, records: list[Record], summary_config: Mapping[str, Any]) -> str:
        """
        执行汇总操作

        功能:
            - 根据配置生成汇总字符串
        """
        template = _safe_text(summary_config.get("template"))
        if not template:
            return ""
        variables_raw = summary_config.get("variables")
        variables = variables_raw if isinstance(variables_raw, Mapping) else {}
        computed: dict[str, Any] = {}
        for key, config in variables.items():
            computed[str(key)] = self._compute_variable(records, config)
        computed.update(self._auto_variables(records))
        for key, value in computed.items():
            template = template.replace("{" + key + "}", str(value))
        return template

    def _compute_variable(self, records: list[Record], config: Any) -> Any:
        """
        计算变量

        功能:
            - 根据配置计算变量值
        """
        if not isinstance(config, Mapping):
            return 0
        kind = _safe_text(config.get("type") or "count")
        if kind == "count":
            filtered = self._filter.execute(records, _safe_text(config.get("filter")), {})
            return len(filtered)
        if kind == "sum":
            field = _safe_text(config.get("field"))
            filtered = self._filter.execute(records, _safe_text(config.get("filter")), {})
            total = 0.0
            for record in filtered:
                raw = _safe_text(record.get(field)).replace("¥", "").replace(",", "")
                try:
                    total += float(raw)
                except ValueError:
                    continue
            return round(total, 2)
        if kind == "percentage":
            num = self._compute_variable(records, config.get("numerator"))
            den = self._compute_variable(records, config.get("denominator"))
            if not den:
                return 0
            return round(float(num) / float(den) * 100, 1)
        return 0

    def _auto_variables(self, records: list[Record]) -> dict[str, Any]:
        """
        计算自动变量

        功能:
            - 计算一些预定义的自动变量
        """
        total = len(records)
        today = date.today()
        overdue = 0
        pending = 0
        in_progress = 0
        done = 0
        urgent = 0
        for record in records:
            status = _safe_text(record.get("status") or record.get("task_status"))
            if status in {"待开始", "未结"}:
                pending += 1
            if status in {"进行中", "处理中"}:
                in_progress += 1
            if status in {"已完成", "已结案", "已归档", "已关闭"}:
                done += 1
            if _safe_text(record.get("urgency")) == "重要紧急":
                urgent += 1
            due = parse_date_value(record.get("deadline") or record.get("hearing_date") or record.get("end_date"))
            if due is not None and due < today:
                overdue += 1
        return {
            "total": total,
            "pending": pending,
            "in_progress": in_progress,
            "done": done,
            "open": pending + in_progress,
            "urgent": urgent,
            "overdue": overdue,
        }


class SectionEngine:
    """
    部分引擎类

    功能:
        - 执行部分操作
        - 渲染表格
        - 替换上下文
    """
    def __init__(self, filter_engine: FilterEngine) -> None:
        """
        初始化部分引擎

        功能:
            - 初始化过滤引擎
        """
        self._filter = filter_engine

    def execute(
        self,
        all_records: list[Record],
        sections_config: list[Any],
        context: Mapping[str, Any],
        render_item: Callable[[Record, list[Mapping[str, Any]]], list[str]],
    ) -> list[dict[str, Any]]:
        """
        执行部分操作

        功能:
            - 根据配置生成部分数据
        """
        rendered: list[dict[str, Any]] = []
        for section in sections_config:
            if not isinstance(section, Mapping):
                continue
            filter_str = self._replace_context(_safe_text(section.get("filter")), context)
            section_records = self._filter.execute(all_records, filter_str, context) if filter_str else list(all_records)
            list_fields_raw = section.get("list_fields")
            list_fields = list_fields_raw if isinstance(list_fields_raw, list) else []
            items: list[dict[str, Any]] = []
            for record in section_records:
                lines = render_item(record, [spec for spec in list_fields if isinstance(spec, Mapping)])
                if lines:
                    items.append({"record": record, "lines": lines})
            rendered.append(
                {
                    "name": _safe_text(section.get("name")),
                    "icon": _safe_text(section.get("icon")),
                    "empty_text": _safe_text(section.get("empty_text")) or "暂无数据",
                    "collapsible": bool(section.get("collapsible", False)),
                    "collapsed": bool(section.get("collapsed_by_default", False)),
                    "expand_label": _safe_text(section.get("expand_label")) or "展开查看全部 {count} 条",
                    "items": items,
                    "table": self._render_table(section_records, section),
                }
            )
        return rendered

    def _render_table(self, records: list[Record], section: Mapping[str, Any]) -> dict[str, Any] | None:
        """
        渲染表格

        功能:
            - 根据配置渲染表格数据
        """
        if _safe_text(section.get("format")) != "compact_table":
            return None
        columns_raw = section.get("columns")
        mapping_raw = section.get("field_mapping")
        columns = [str(item) for item in columns_raw] if isinstance(columns_raw, list) else []
        mapping = mapping_raw if isinstance(mapping_raw, Mapping) else {}
        rows: list[list[str]] = []
        for index, record in enumerate(records, start=1):
            row: list[str] = []
            for col in columns:
                key = _safe_text(mapping.get(col) or col)
                if key == "_row_number":
                    row.append(str(index))
                else:
                    row.append(_safe_text(record.get(key)) or "—")
            rows.append(row)
        return {"headers": columns, "rows": rows}

    def _replace_context(self, text: str, context: Mapping[str, Any]) -> str:
        """
        替换上下文

        功能:
            - 在文本中替换上下文变量
        """
        out = text
        for key, value in context.items():
            out = out.replace("{" + str(key) + "}", str(value))
        return out
