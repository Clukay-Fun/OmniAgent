from __future__ import annotations

import re
from typing import Any


class SmartEngine:
    """Lightweight suggestion engine for action cards."""

    _DATE_PATTERNS: tuple[str, ...] = (
        r"(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)",
        r"(\d{1,2}[-/.月]\d{1,2}日?)",
    )

    def analyze_progress_for_suggestions(self, progress_text: str, table_type: str = "case") -> list[dict[str, Any]]:
        text = str(progress_text or "").strip()
        if not text or table_type != "case":
            return []

        suggestions: list[dict[str, Any]] = []
        mapping = {
            "hearing_date": ["开庭时间变更", "开庭改为", "改期", "延期开庭"],
            "evidence_deadline": ["举证期限", "举证截止"],
            "appeal_deadline": ["上诉期限", "上诉截止"],
            "seizure_expiry": ["查封到期", "续封"],
        }
        field_labels = {
            "hearing_date": "开庭日",
            "evidence_deadline": "举证截止日",
            "appeal_deadline": "上诉截止日",
            "seizure_expiry": "查封到期日",
            "status": "案件状态",
        }
        parsed_date = self.extract_date_from_text(text)
        for field_key, keywords in mapping.items():
            for keyword in keywords:
                if keyword in text:
                    suggestions.append(
                        {
                            "field": field_key,
                            "field_label": field_labels.get(field_key, field_key),
                            "suggested_value": parsed_date,
                            "reason": f"进展内容提到{keyword}",
                        }
                    )
                    break

        if any(token in text for token in ["结案", "判决生效", "撤诉", "执行完毕"]):
            suggestions.append(
                {
                    "field": "status",
                    "field_label": field_labels["status"],
                    "suggested_value": "已结案",
                    "reason": "进展内容疑似进入结案状态",
                }
            )
        return suggestions

    def extract_date_from_text(self, text: str) -> str:
        source = str(text or "")
        for pattern in self._DATE_PATTERNS:
            match = re.search(pattern, source)
            if not match:
                continue
            value = match.group(1)
            value = value.replace("年", "-").replace("月", "-").replace("日", "")
            value = value.replace("/", "-").replace(".", "-")
            parts = [chunk for chunk in value.split("-") if chunk]
            if len(parts) == 3:
                return f"{parts[0].zfill(4)}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
        return ""
