from __future__ import annotations

from typing import Any


class ActionSmartEngine:
    """Logic-layer smart inference for write actions."""

    def infer_create_fields(self, table_type: str, fields: dict[str, Any]) -> dict[str, Any]:
        inferred: dict[str, Any] = {}
        if table_type != "case":
            return inferred

        case_no = str(fields.get("案号") or "").strip()
        if case_no and "执" in case_no and not str(fields.get("程序阶段") or "").strip():
            inferred["程序阶段"] = "执行"
        return inferred
