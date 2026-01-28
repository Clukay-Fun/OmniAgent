"""
Agent core orchestration.
"""

from __future__ import annotations

from typing import Any

from src.agent.session import SessionManager
from src.config import Settings
from src.llm.client import LLMClient
from src.mcp.client import MCPClient
from src.utils.time_parser import parse_time_range


class AgentCore:
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
        self._sessions.add_message(user_id, "user", text)
        tool_name = self._select_tool(text)

        date_range = await self._resolve_time_range(text)
        params: dict[str, Any] = {"keyword": text}
        if date_range:
            params["date_from"] = date_range.get("date_from")
            params["date_to"] = date_range.get("date_to")

        if tool_name == "feishu.v1.doc.search":
            params = {"keyword": text}

        result = await self._mcp.call_tool(tool_name, params)
        reply = self._format_reply(tool_name, text, result)
        self._sessions.add_message(user_id, "assistant", reply["text"])
        return reply

    async def _resolve_time_range(self, text: str) -> dict[str, str] | None:
        content = await self._llm.parse_time_range(text)
        if "date_from" in content and "date_to" in content:
            return {"date_from": content["date_from"], "date_to": content["date_to"]}
        parsed = parse_time_range(text)
        if parsed:
            return {"date_from": parsed.date_from, "date_to": parsed.date_to}
        return None

    def _select_tool(self, text: str) -> str:
        if "文档" in text or "资料" in text or "文件" in text:
            return "feishu.v1.doc.search"
        return "feishu.v1.bitable.search"

    def _format_reply(self, tool_name: str, text: str, result: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "feishu.v1.doc.search":
            documents = result.get("documents") or []
            if not documents:
                return {"type": "text", "text": self._settings.reply.templates.no_result}
            lines = []
            for index, doc in enumerate(documents, start=1):
                title = doc.get("title") or "未命名文档"
                url = doc.get("url") or ""
                preview = doc.get("preview") or ""
                lines.append(f"{index}. {title}\n{preview}\n{url}")
            return {"type": "text", "text": "\n\n".join(lines)}

        records = result.get("records") or []
        if not records:
            return {"type": "text", "text": self._settings.reply.templates.no_result}

        title = self._settings.reply.case_list.title.format(period="本周", count=len(records))
        items = []
        for index, record in enumerate(records, start=1):
            fields = record.get("fields") or {}
            item = self._settings.reply.case_list.item.format(
                index=index,
                client=fields.get("委托人及联系方式", ""),
                opponent=fields.get("对方当事人", ""),
                cause=fields.get("案由", ""),
                case_number=fields.get("案号", ""),
                hearing_date=fields.get("开庭日", ""),
                court=fields.get("审理法院", ""),
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
