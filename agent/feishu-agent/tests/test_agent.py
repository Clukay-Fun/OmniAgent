from __future__ import annotations

import asyncio

from src.core.orchestrator import AgentCore
from src.core.session import SessionManager
from src.config import Settings


class FakeMCP:
    async def call_tool(self, tool_name: str, params: dict) -> dict:
        return {
            "records": [
                {
                    "record_id": "rec1",
                    "fields_text": {
                        "委托人及联系方式": "张三",
                        "对方当事人": "李四",
                        "案由": "合同纠纷",
                        "案号": "（2025）粤0306民初123号",
                        "审理法院": "深圳福田法院",
                        "程序阶段": "一审",
                    },
                    "record_url": "https://example.com",
                }
            ]
        }


class FakeLLM:
    async def parse_time_range(self, text: str, system_context: str | None = None) -> dict:
        return {}

    async def chat_json(self, prompt: str, system: str | None = None) -> dict:
        return {}


def test_agent_returns_text_reply() -> None:
    async def run() -> None:
        settings = Settings()
        sessions = SessionManager(settings.session)
        agent = AgentCore(settings, sessions, FakeMCP(), FakeLLM())
        reply = await agent.handle_message("u1", "查案号李四")
        assert "案件查询结果" in reply["text"]
        assert "https://example.com" in reply["text"]

    asyncio.run(run())
