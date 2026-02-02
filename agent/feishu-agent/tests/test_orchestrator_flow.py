import os
import pytest

from src.core.orchestrator import AgentOrchestrator
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
    async def parse_time_range(self, text: str, system_context: str | None = None, timeout=None) -> dict:
        return {}

    async def chat_json(self, prompt: str, system: str | None = None, timeout=None) -> dict:
        return {}

    async def chat(self, messages, timeout=None):
        return "ok"


@pytest.mark.asyncio
async def test_orchestrator_query_and_chitchat(tmp_path) -> None:
    os.environ["OMNI_WORKSPACE_ROOT"] = str(tmp_path / "workspace")

    settings = Settings()
    sessions = SessionManager(settings.session)
    agent = AgentOrchestrator(settings, sessions, FakeMCP(), FakeLLM())

    reply = await agent.handle_message("u1", "查案号张三")
    assert "案件查询结果" in reply["text"]

    reply2 = await agent.handle_message("u1", "你好")
    assert "你好" in reply2["text"]
