"""
描述: Locator Triplet 统一校验模块。
主要功能:
    - 校验 LocatorTriplet 对象，确保其包含必要的字段。
    - 提供一个不可变的数据类来表示 LocatorTriplet。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LocatorTriplet:
    """
    表示多维表格记录数据写入操作的最小定位坐标。

    属性:
        - app_token: 多维表格应用 token。
        - table_id: 数据表 ID。
        - record_id: 记录 ID（create 时可选）。
    """
    app_token: str
    table_id: str
    record_id: str | None = None


# region 校验函数
def validate_locator_triplet(
    *,
    app_token: str | None,
    table_id: str | None,
    record_id: str | None = None,
    require_record_id: bool = False,
) -> LocatorTriplet:
    """
    校验 locator triplet 并返回不可变数据对象。

    功能:
        - 检查 app_token、table_id 是否为空。
        - 根据 require_record_id 参数检查 record_id 是否为空。
        - 如果有必填字段缺失，抛出 ValueError 异常。
        - 返回一个 LocatorTriplet 实例。
    """
    missing: list[str] = []
    _app_token = str(app_token or "").strip()
    _table_id = str(table_id or "").strip()
    _record_id = str(record_id or "").strip() or None

    if not _app_token:
        missing.append("app_token")
    if not _table_id:
        missing.append("table_id")
    if require_record_id and not _record_id:
        missing.append("record_id")

    if missing:
        raise ValueError(f"missing locator triplet: {', '.join(missing)}")

    return LocatorTriplet(app_token=_app_token, table_id=_table_id, record_id=_record_id)
# endregion
