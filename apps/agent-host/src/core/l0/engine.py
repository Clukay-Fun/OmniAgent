"""
描述: L0 规则硬约束引擎。
主要功能:
    - 处理精确触发与状态检查
    - 不做语义理解
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
    """
    L0 规则引擎的决策结果。

    功能:
        - 表示是否已处理请求
        - 包含回复内容
        - 强制执行的技能
        - 强制的上一个结果
        - 额外的强制参数
        - 意图提示
    """
    handled: bool = False
    reply: dict[str, Any] | None = None
    force_skill: str | None = None
    force_last_result: dict[str, Any] | None = None
    force_extra: dict[str, Any] = field(default_factory=dict)
    intent_hint: str | None = None


class L0RuleEngine:
    """
    L0 规则引擎。

    功能:
        - 初始化规则引擎
        - 评估用户输入并返回决策结果
    """

    _EMPTY_SET = {"", "...", "。。。", "???", "？？？", ".", "。", "?", "？"}

    def __init__(
        self,
        state_manager: ConversationStateManager,
        l0_rules: dict[str, Any] | None = None,
        skills_config: dict[str, Any] | None = None,
    ) -> None:
        """
        初始化 L0 规则引擎。

        功能:
            - 设置状态管理器
            - 加载规则和技能配置
            - 初始化确认短语、取消短语、下一页触发词等
        """
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

        self._update_triggers = {
            "更新", "修改", "改", "改成", "改为", "设成", "设置为", "设为", "调整", "变更",
        }
        self._delete_triggers = {
            "删除", "删掉", "移除", "去掉",
        }
        self._reference_tokens = {
            "这个", "这条", "那条", "上一条", "刚才", "刚刚", "前一条",
        }

        self._pending_field_hints = {
            "案号", "案由", "委托人", "主办", "协办", "法院", "开庭", "备注", "项目",
            "状态", "进展", "金额", "费用", "付款", "第", "这个", "那条",
        }
        self._generic_confirm_tokens = {"确认", "是", "是的", "ok", "yes"}

        chitchat_cfg = self._skills_config.get("chitchat", {})
        if not chitchat_cfg:
            chitchat_cfg = self._skills_config.get("skills", {}).get("chitchat", {})
        whitelist = chitchat_cfg.get("whitelist", [])
        self._chitchat_keywords = {
            str(item).strip().lower() for item in whitelist if str(item).strip()
        }
        self._chitchat_keywords.update({"在吗", "吃了吗", "你是谁", "你好呀", "hello", "hi"})
        self._domain_hints = {
            "案件", "案号", "项目", "开庭", "庭审", "法院", "律师", "当事人",
            "委托人", "提醒", "查询", "新增", "更新", "删除", "总结",
        }

    def evaluate(self, user_id: str, text: str) -> L0Decision:
        """
        评估用户输入并返回决策结果。

        功能:
            - 清理过期状态
            - 处理空消息与纯符号
            - 拦截批量删除操作
            - 处理删除确认状态
            - 处理通用待办动作状态
            - 处理分页请求
            - 预判闲聊
            - 处理第N个记录请求
            - 处理指代 + 动作请求
        """
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

        # 3.1) 通用待办动作状态（如创建补充字段）
        pending_action = self._state.get_pending_action(user_id)
        if pending_action:
            if normalized in self._cancel_phrases:
                self._state.clear_pending_action(user_id)
                return L0Decision(
                    handled=True,
                    reply={"type": "text", "text": "好的，已取消当前操作。"},
                )

            force_skill = self._map_pending_action_skill(pending_action.action)
            if force_skill and self._should_continue_pending_action(query, pending_action.action):
                return L0Decision(
                    handled=False,
                    force_skill=force_skill,
                    force_extra={
                        "pending_action": {
                            "action": pending_action.action,
                            "payload": pending_action.payload,
                        }
                    },
                )

            logger.info("L0 implicit cancel pending action for user: %s", user_id)
            self._state.clear_pending_action(user_id)

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

        # 4.5) 闲聊预判（只打 hint，不做拦截）
        if self._is_chitchat_like(query):
            return L0Decision(handled=False, intent_hint="chitchat")

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
            action_skill = self._detect_action_skill(query)
            if action_skill:
                return L0Decision(
                    handled=False,
                    force_skill=action_skill,
                    force_last_result={"records": [record]},
                )

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

        # 6) 指代 + 动作（这个/那条/刚才那条）
        action_skill = self._detect_action_skill(query)
        active_record = self._state.get_active_record(user_id)
        if action_skill and active_record:
            if action_skill == "DeleteSkill" and not self._has_reference_token(query):
                return L0Decision(handled=False)
            record = active_record.record or {
                "record_id": active_record.record_id,
                "fields_text": {
                    "案号": active_record.record_summary,
                },
            }
            return L0Decision(
                handled=False,
                force_skill=action_skill,
                force_last_result={"records": [record]},
            )

        return L0Decision(handled=False)

    def _normalize_text(self, text: str) -> str:
        """
        规范化文本。

        功能:
            - 去除首尾空白字符
            - 转换为小写
            - 去除特定标点符号
        """
        normalized = (text or "").strip().lower()
        return normalized.strip("，。！？!?,. ")

    def _is_empty_like(self, text: str) -> bool:
        """
        判断文本是否为空或仅包含无意义字符。

        功能:
            - 检查文本是否在空集合中
            - 检查文本是否为空
            - 检查文本是否不包含中文、数字、字母
        """
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
        """
        提取文本中的序号。

        功能:
            - 使用正则表达式查找序号
            - 将中文数字转换为阿拉伯数字
        """
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

    def _detect_action_skill(self, query: str) -> str | None:
        """
        检测并返回相应的技能。

        功能:
            - 检查文本中是否包含删除触发词
            - 检查文本中是否包含更新触发词
        """
        text = (query or "").strip()
        if not text:
            return None
        if any(token in text for token in self._delete_triggers):
            return "DeleteSkill"
        if any(token in text for token in self._update_triggers):
            return "UpdateSkill"
        return None

    def _has_reference_token(self, query: str) -> bool:
        """
        检查文本中是否包含指代词。

        功能:
            - 检查文本中是否包含序号
            - 检查文本中是否包含指代词
        """
        text = (query or "").strip()
        if not text:
            return False
        if self._extract_ordinal_index(text) is not None:
            return True
        return any(token in text for token in self._reference_tokens)

    def _map_pending_action_skill(self, action: str) -> str | None:
        """
        映射待办动作到相应的技能。

        功能:
            - 根据动作类型返回相应的技能
        """
        mapping = {
            "create_record": "CreateSkill",
            "update_record": "UpdateSkill",
            "update_collect_fields": "UpdateSkill",
            "delete_record": "DeleteSkill",
            "repair_child_write": "CreateSkill",
            "repair_child_create": "CreateSkill",
            "repair_child_update": "UpdateSkill",
        }
        key = str(action or "").strip()
        return mapping.get(key)

    def _should_continue_pending_action(self, query: str, action: str | None = None) -> bool:
        """
        判断是否应继续待办动作。

        功能:
            - 检查文本中是否包含确认或取消触发词
            - 检查文本中是否包含更新提示
            - 检查文本中是否包含序号或字段提示
        """
        text = (query or "").strip()
        if not text:
            return False
        normalized = self._normalize_text(text)
        action_key = str(action or "").strip()
        if action_key == "delete_record":
            if normalized in self._cancel_phrases:
                return True
            return normalized in self._confirm_phrases or normalized in {"确认删除"}
        if action_key == "update_collect_fields":
            if normalized in self._generic_confirm_tokens or normalized in self._cancel_phrases:
                return True
            update_hints = {
                "案号", "项目ID", "项目id", "项目编号", "项目号",
                "开庭日", "日期", "状态", "进展", "主办", "协办", "法院", "案由", "金额", "备注",
            }
            if any(token in text for token in update_hints):
                return True
            if any(token in text for token in ("改成", "改为", "变成", "变为", "更新为", "修改为", "设为", "设成", "调整为", "追加")):
                return True
            if ":" in text or "：" in text:
                return True
            return False
        if normalized in self._generic_confirm_tokens or normalized in self._cancel_phrases:
            return True
        if self._extract_ordinal_index(text) is not None:
            return True
        if any(token in text for token in self._pending_field_hints):
            return True
        return any(("\u4e00" <= ch <= "\u9fff") or ch.isalnum() for ch in text)

    def _is_chitchat_like(self, query: str) -> bool:
        """
        判断文本是否为闲聊内容。

        功能:
            - 检查文本中是否包含领域提示词
            - 检查文本中是否包含闲聊关键词
            - 检查文本长度和特定短语
        """
        text = (query or "").strip()
        if not text:
            return False
        lowered = self._normalize_text(text)
        if any(hint in text for hint in self._domain_hints):
            return False
        if lowered in self._chitchat_keywords:
            return True
        if len(text) <= 8 and any(token in lowered for token in ("你好", "在吗", "谢谢", "bye", "help")):
            return True
        return False
