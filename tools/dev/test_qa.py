import asyncio
import os
import sys

# 添加应用根目录到 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../apps/agent-host")))

from src.config import get_settings
from src.core.runtime.state.session import SessionManager
from src.infra.mcp.client import MCPClient
from src.infra.llm.client import LLMClient
from src.core.capabilities.skills.actions.data_writer import build_default_data_writer
from src.core.brain.orchestration.orchestrator import AgentOrchestrator
from src.utils.runtime.workspace import ensure_workspace

async def run_test():
    workspace = ensure_workspace()
    settings = get_settings()
    session_manager = SessionManager(settings=settings.session)
    
    # 初始化客户端 (即使 MCP 断开，LLM 应该能进行 QA)
    mcp_client = MCPClient(settings=settings)
    llm_client = LLMClient(settings.llm)
    data_writer = build_default_data_writer(mcp_client=mcp_client)

    print("=== 初始化 AgentOrchestrator ===")
    orchestrator = AgentOrchestrator(
        settings=settings,
        session_manager=session_manager,
        mcp_client=mcp_client,
        llm_client=llm_client,
        data_writer=data_writer
    )
    
    # 模拟一条强业务/非闲聊的提问
    query = "请问劳动争议案件的诉讼时效是多久？"
    print(f"\n>>> 模拟用户询问: {query}")
    
    result = await orchestrator.handle_message(
        user_id="test_user",
        text=query,
    )
    
    print("\n=== 返回结果 ===")
    print(result.get("text"))
    if "outbound" in result:
        print("\n=== Outbound Meta ===")
        print(result["outbound"].get("meta", {}))

if __name__ == "__main__":
    asyncio.run(run_test())
