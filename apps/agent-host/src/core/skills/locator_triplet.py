"""
Locator Triplet 统一校验。

LocatorTriplet 是多维表格记录数据写入操作（create/update/delete）的最小定位坐标，
包括 app_token、table_id、record_id（create 时可选）。

所有写链路在执行前必须通过 validate_locator_triplet() 校验。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LocatorTriplet:
    app_token: str
    table_id: str
    record_id: str | None = None


def validate_locator_triplet(
    *,
    app_token: str | None,
    table_id: str | None,
    record_id: str | None = None,
    require_record_id: bool = False,
) -> LocatorTriplet:
    """校验 locator triplet 并返回不可变数据对象。

    Args:
        app_token: 多维表格应用 token。
        table_id: 数据表 ID。
        record_id: 记录 ID（update/delete 必须提供）。
        require_record_id: 若为 True 则 record_id 也为必填。

    Raises:
        ValueError: 必填字段缺失时，抛出 "missing locator triplet: ..." 描述。
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
