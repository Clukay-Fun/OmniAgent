from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.field_formatter import format_field_value  # noqa: E402


def test_format_number_currency_and_datetime() -> None:
    number = format_field_value("1234567", {"type": 2, "name": "数量"})
    currency = format_field_value("1234.5", {"type": 2, "name": "金额", "type_name": "货币"})
    dt = format_field_value("2026-02-22T12:30:00+00:00", {"type": 5, "name": "开庭日"})

    assert number.text == "1,234,567"
    assert number.status == "success"
    assert currency.text == "¥1,234.50"
    assert currency.field_type == "currency"
    assert dt.text == "2026年02月22日 20:30"


def test_format_select_person_bool_attachment() -> None:
    single = format_field_value({"label": "进行中"}, {"type": 3, "name": "状态"})
    person = format_field_value([{"name": "张三"}, {"user_id": "ou_xxx"}], {"type": 11, "name": "负责人"})
    check_true = format_field_value(True, {"type": 7, "name": "已归档"})
    attachment = format_field_value([{"name": "合同.pdf"}], {"type": 17, "name": "附件"})

    assert single.text == "进行中"
    assert person.text == "@张三、ou_xxx"
    assert check_true.text == "OK 是"
    assert attachment.text == "OK 合同.pdf"


def test_format_multi_value_fields() -> None:
    multi_select = format_field_value(
        [{"label": "待处理"}, {"name": "高优先级"}],
        {"type": 4, "name": "标签"},
    )
    person = format_field_value(
        {"users": [{"name": "张三"}, {"open_id": "ou_001"}]},
        {"type": 11, "name": "关注人"},
    )
    attachment = format_field_value(
        {"files": [{"name": "证据A.pdf"}, {"file_name": "清单.xlsx"}]},
        {"type": 17, "name": "附件"},
    )

    assert multi_select.field_type == "multi_select"
    assert multi_select.text == "待处理、高优先级"
    assert person.text == "@张三、ou_001"
    assert attachment.text == "OK 证据A.pdf、OK 清单.xlsx"


def test_format_unknown_and_malformed_fallback() -> None:
    unknown = format_field_value({"foo": "bar"}, {"type": 999, "name": "未知"})
    malformed_number = format_field_value("abc", {"type": 2, "name": "数量"})

    assert unknown.field_type == "unknown"
    assert unknown.status == "fallback"
    assert malformed_number.text == "abc"
    assert malformed_number.status == "malformed"
