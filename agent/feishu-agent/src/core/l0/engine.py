"""
L0 规则硬约束引擎。

仅处理精确触发与状态检查，不做语义理解。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any

from src.core.state import ConversationStateManager

logger = logging.getLogger(__name__)


@dataclass
class L0Decision:
    handled: bool = False
    reply: dict[str, Any] | None = None
    force_skill: str | None = None
    force_last_result: dict[str, Any] | None = None
    force_extra: dict[str, Any] = field(default_factory=dict)


class L0RuleEngine:
    """L0 规则引擎。"""

    _EMPTY_SET = {"", "...", "。。。", "???", "？？？", ".", "。", "?", "？"}

    def __init__(
        self,
        state_manager: ConversationStateManager,
        l0_rules: dict[str, Any] | None = None,
        skills_config: dict[str, Any] | None = None,
    ) -> None:
        self._state = state_manager
        self._rules = l0_rules or {}
        self._skills_config = skills_config or {}

        delete_cfg = self._skills_config.get("delete", {})
        confirm_phrases = delete_cfg.get("confirm_phrases", ["确认删除"])
        self._confirm_phrases = {str(x).strip().lower() for x in confirm_phrases if str(x).strip()}

        cancel_phrases = self._rules.get("cancel_phrases", ["算了", "取消", "不了", "不用了"])
        self._cancel_phrases = {str(x).strip().lower() for x in cancel_phrases if str(x).strip()}

        next_page_triggers = self._rules.get("next_page_triggers", ["下一页", "继续", "更多"])
        self._next_page_triggers = {str(x).strip().lower() for x in next_page_triggers if str(x).strip()}

        self._batch_delete_patterns = [
            re.compile(r"删除所有"),
            re.compile(r"全部删除"),
            re.compile(r"批量删除"),
        ]

    def evaluate(self, user_id: str, text: str) -> L0Decision:
        query = (text or "").strip()
        normalized = self._normalize_text(query)

        # 先触发状态清理
        self._state.cleanup_expired()

        # 1) 空消息与纯符号
        if self._is_empty_like(query):
            return L0Decision(
                handled=True,
                reply={"type": "text", "text": "请问有什么可以帮您？您可以说：查所有案件、我的案件、查案号 XXX。"},
            )

        # 2) 批量删除拦截
        if any(pattern.search(query) for pattern in self._batch_delete_patterns):
            return L0Decision(
                handled=True,
                reply={"type": "text", "text": "不支持批量删除操作，请指定具体案件后再删除。"},
            )

        # 3) 删除确认状态
        pending_delete = self._state.get_pending_delete(user_id)
        if pending_delete:
            if normalized in self._confirm_phrases:
                return L0Decision(
                    handled=False,
                    force_skill="DeleteSkill",
                    force_last_result={
                        "pending_delete": {
                            "record_id": pending_delete.record_id,
                            "case_no": pending_delete.record_summary,
                            "table_id": pending_delete.table_id,
                        }
                    },
                )

            if normalized in self._cancel_phrases:
                self._state.clear_pending_delete(user_id)
                return L0Decision(
                    handled=True,
                    reply={"type": "text", "text": "好的，已取消删除操作。"},
                )

            # 隐式取消：用户说了无关内容，清掉 pending 再继续正常流程
            logger.info("L0 implicit cancel pending delete for user: %s", user_id)
            self._state.clear_pending_delete(user_id)

        # 4) 分页
        if normalized in self._next_page_triggers:
            pagination = self._state.get_pagination(user_id)
            if not pagination:
                return L0Decision(
                    handled=True,
                    reply={"type": "text", "text": "当前没有可继续分页的查询结果，请先执行一次查询。"},
                )

            if not pagination.page_token:
                return L0Decision(
                    handled=True,
                    reply={"type": "text", "text": "已经是最后一页了。"},
                )

            force_extra = {
                "pagination": {
                    "tool": pagination.tool,
                    "params": pagination.params,
                    "page_token": pagination.page_token,
                    "current_page": pagination.current_page,
                    "total": pagination.total,
                }
            }
            return L0Decision(
                handled=False,
                force_skill="QuerySkill",
                force_extra=force_extra,
            )

        # 5) 第N个（使用最近结果）
        ordinal_idx = self._extract_ordinal_index(query)
        if ordinal_idx is not None:
            last_result = self._state.get_last_result(user_id)
            if not last_result or not last_result.records:
                return L0Decision(
                    handled=True,
                    reply={"type": "text", "text": "请先执行查询，我才能识别“第几个”记录。"},
                )

            if ordinal_idx < 0 or ordinal_idx >= len(last_result.records):
                return L0Decision(
                    handled=True,
                    reply={"type": "text", "text": f"当前只有 {len(last_result.records)} 条结果，请重新指定序号。"},
                )

            record = last_result.records[ordinal_idx]
            fields = record.get("fields_text") or record.get("fields") or {}
            case_no = fields.get("案号", "")
            cause = fields.get("案由", "")
            court = fields.get("审理法院", "")
            detail = [
                f"已定位第 {ordinal_idx + 1} 条记录：",
                f"案号：{case_no}",
                f"案由：{cause}",
                f"法院：{court}",
            ]
            record_url = record.get("record_url")
            if record_url:
                detail.append(f"详情：{record_url}")
            return L0Decision(
                handled=True,
                reply={"type": "text", "text": "\n".join(detail)},
            )

        return L0Decision(handled=False)

    def _normalize_text(self, text: str) -> str:
        normalized = (text or "").strip().lower()
        return normalized.strip("，。！？!?,. ")

    def _is_empty_like(self, text: str) -> bool:
        if text in self._EMPTY_SET:
            return True
        if not text:
            return True
        # 不包含中文、数字、字母，则视为符号/表情类输入
        has_meaningful = any(
            ("\u4e00" <= ch <= "\u9fff") or ch.isalnum()
            for ch in text
        )
        return not has_meaningful

    def _extract_ordinal_index(self, text: str) -> int | None:
        m = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*个", text)
        if not m:
            return None
        token = m.group(1)
        if token.isdigit():
            value = int(token)
            return value - 1 if value > 0 else None
        mapping = {
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        if token == "十":
            return 9
        if len(token) == 1 and token in mapping:
            return mapping[token] - 1
        if token.startswith("十") and len(token) == 2 and token[1] in mapping:
            return 9 + mapping[token[1]]
        if token.endswith("十") and len(token) == 2 and token[0] in mapping:
            return mapping[token[0]] * 10 - 1
        return None
