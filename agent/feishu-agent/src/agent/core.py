"""
Agent core orchestration.
"""

from __future__ import annotations

from typing import Any
import re

from src.agent.session import SessionManager
from src.config import Settings
from src.llm.client import LLMClient
from src.mcp.client import MCPClient
from src.utils.time_parser import parse_time_range


class AgentCore:
    _DATE_PATTERN = re.compile(
        r"(?:\d{4}年)?\d{1,2}月\d{1,2}[日号]?|\d{4}-\d{1,2}-\d{1,2}"
    )
    def __init__(
        self,
        settings: Settings,
        session_manager: SessionManager,
        mcp_client: MCPClient,
        llm_client: LLMClient,
    ) -> None:
        self._settings = settings
        self._sessions = session_manager
        self._mcp = mcp_client
        self._llm = llm_client

    async def handle_message(self, user_id: str, text: str) -> dict[str, Any]:
        self._sessions.cleanup_expired()
        self._sessions.add_message(user_id, "user", text)
        tool_name = self._select_tool(text)

        date_range = await self._resolve_time_range(text)
        keyword = self._extract_keyword(text)
        if date_range:
            keyword = self._strip_date_tokens(keyword)
        params: dict[str, Any] = {"keyword": keyword}
        if date_range:
            params["date_from"] = date_range.get("date_from")
            params["date_to"] = date_range.get("date_to")

        if tool_name == "feishu.v1.doc.search":
            params = {"keyword": keyword or text}

        try:
            result = await self._mcp.call_tool(tool_name, params)
            reply = self._format_reply(tool_name, text, result)
        except Exception as exc:
            message = self._settings.reply.templates.error.format(message=str(exc))
            if "TIMEOUT" in str(exc):
                message = self._settings.reply.templates.timeout
            reply = {"type": "text", "text": message}
        self._sessions.add_message(user_id, "assistant", reply["text"])
        return reply

    async def _resolve_time_range(self, text: str) -> dict[str, str] | None:
        parsed = parse_time_range(text)
        if parsed:
            return {"date_from": parsed.date_from, "date_to": parsed.date_to}
        if not self._has_time_hint(text):
            return None
        try:
            content = await self._llm.parse_time_range(text)
            if "date_from" in content and "date_to" in content:
                return {"date_from": content["date_from"], "date_to": content["date_to"]}
        except Exception:
            return None
        return None

    def _has_time_hint(self, text: str) -> bool:
        keywords = ["今天", "明天", "本周", "这周", "下周", "本月", "这个月"]
        if any(keyword in text for keyword in keywords):
            return True
        return bool(
            __import__("re").search(r"\d{1,2}月\d{1,2}[日号]?|\d{4}-\d{1,2}-\d{1,2}", text)
        )

    def _select_tool(self, text: str) -> str:
        if "文档" in text or "资料" in text or "文件" in text:
            return "feishu.v1.doc.search"
        return "feishu.v1.bitable.search"

    def _extract_keyword(self, text: str) -> str:
        keyword = text
        for phrase in (
            "找一下",
            "查一下",
            "查询",
            "搜索",
            "帮我",
            "请帮我",
            "一下",
            "案子",
            "案件",
            "有什么",
            "有哪些",
            "庭要开",
            "庭审",
            "信息",
            "详情",
        ):
            keyword = keyword.replace(phrase, "")
        keyword = keyword.replace("的", "")
        return keyword.strip()

    def _strip_date_tokens(self, keyword: str) -> str:
        cleaned = self._DATE_PATTERN.sub("", keyword)
        cleaned = re.sub(r"[\s，。！？,!.?]+", "", cleaned)
        cleaned = re.sub(r"(有什么|哪些|什么|有|开庭|庭审|安排|情况|信息)$", "", cleaned)
        return cleaned.strip()

    def _format_reply(self, tool_name: str, text: str, result: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "feishu.v1.doc.search":
            documents = result.get("documents") or []
            if not documents:
                return {"type": "text", "text": self._fallback_reply(text)}
            lines = []
            for index, doc in enumerate(documents, start=1):
                title = doc.get("title") or "未命名文档"
                url = doc.get("url") or ""
                preview = doc.get("preview") or ""
                lines.append(f"{index}. {title}\n{preview}\n{url}")
            return {"type": "text", "text": "\n\n".join(lines)}

        records = result.get("records") or []
        if not records:
            return {"type": "text", "text": self._fallback_reply(text)}

        title = self._settings.reply.case_list.title.format(count=len(records))
        items = []
        for index, record in enumerate(records, start=1):
            fields = record.get("fields_text") or record.get("fields") or {}
            item = self._settings.reply.case_list.item.format(
                index=index,
                client=fields.get("委托人及联系方式", ""),
                opponent=fields.get("对方当事人", ""),
                cause=fields.get("案由", ""),
                case_number=fields.get("案号", ""),
                court=fields.get("审理法院", ""),
                stage=fields.get("程序阶段", ""),
                record_url=record.get("record_url", ""),
            )
            items.append(item)

        text_reply = "\n\n".join([title] + items)
        card = self._build_case_card(title, items)
        return {"type": "card", "text": text_reply, "card": card}

    def _build_case_card(self, title: str, items: list[str]) -> dict[str, Any]:
        elements = [{"tag": "markdown", "content": item} for item in items]
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": elements,
        }

    def _fallback_reply(self, text: str) -> str:
        templates = self._settings.reply.templates
        lowered = text.lower()
        greetings = ["你好", "您好", "嗨", "在吗", "在不", "早上好", "下午好", "晚上好", "hi", "hello"]
        thanks = ["谢谢", "多谢", "感谢", "辛苦", "thank"]
        goodbyes = ["再见", "拜拜", "bye", "回头见"]
        if any(token in text for token in greetings) or any(token in lowered for token in ("hi", "hello")):
            return templates.small_talk
        if any(token in text for token in thanks) or "thank" in lowered:
            return templates.thanks
        if any(token in text for token in goodbyes) or "bye" in lowered:
            return templates.goodbye
        return f"{templates.no_result} {templates.guide}"
