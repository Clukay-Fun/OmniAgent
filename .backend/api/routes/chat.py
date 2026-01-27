"""
描述: 对话会话路由
主要功能:
    - 会话创建
    - SSE 对话消息
    - 会话历史查询
依赖: fastapi
"""

from __future__ import annotations

import json
from typing import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from db.connection import get_connection, get_database_url
from schemas.chat import (
    ChatCitation,
    ChatHistoryResponse,
    ChatMessageItem,
    ChatMessageRequest,
    ChatSessionItem,
    ChatSessionCreateRequest,
    ChatSessionCreateResponse,
)
from services.chat_service import (
    append_message,
    create_session,
    get_recent_sessions,
    get_recent_history,
)

router = APIRouter(prefix="/chat", tags=["chat"])


# ============================================
# region _sse_event
# ============================================
def _sse_event(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
# endregion
# ============================================


# ============================================
# region _build_preview
# ============================================
def _build_preview(text: str, limit: int = 50) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."
# endregion
# ============================================


# ============================================
# region _normalize_citation
# ============================================
def _normalize_citation(payload: dict[str, object]) -> ChatCitation:
    document_id = payload.get("document_id") or payload.get("source_id")
    document_id_value = str(document_id) if document_id is not None else None

    node_id = payload.get("node_id")
    if node_id is None:
        node_id = payload.get("chunk_index")
    if node_id is None:
        node_id = payload.get("chunk_id")
    node_id_value: int | None = None
    if isinstance(node_id, int):
        node_id_value = node_id
    elif isinstance(node_id, str) and node_id.isdigit():
        node_id_value = int(node_id)

    filename = payload.get("filename") or payload.get("file_name")
    filename_value = filename if isinstance(filename, str) else None

    preview = payload.get("preview")
    preview_value = preview if isinstance(preview, str) else None

    score = payload.get("score")
    score_value: float | None = None
    if isinstance(score, (int, float)):
        score_value = float(score)
    elif isinstance(score, str):
        try:
            score_value = float(score)
        except ValueError:
            score_value = None

    path = payload.get("path")
    path_value = [str(item) for item in path] if isinstance(path, list) else []

    return ChatCitation(
        document_id=document_id_value,
        node_id=node_id_value,
        filename=filename_value,
        preview=preview_value,
        score=score_value,
        path=path_value,
    )
# endregion
# ============================================


# ============================================
# region list_chat_sessions
# ============================================
@router.get("/sessions", response_model=list[ChatSessionItem])
def list_chat_sessions(limit: int = 10) -> list[ChatSessionItem]:
    """
    列出最近会话
    """

    db_url = get_database_url()
    with get_connection(db_url) as conn:
        sessions = get_recent_sessions(conn, limit)

    items: list[ChatSessionItem] = []
    for session in sessions:
        raw_session_id = session.get("session_id")
        session_id = str(raw_session_id) if raw_session_id is not None else ""
        title = session.get("title")
        message_count = session.get("message_count")
        updated_at = session.get("updated_at")
        message_count_value = message_count if isinstance(message_count, int) else 0
        items.append(
            ChatSessionItem(
                session_id=session_id,
                title=str(title) if isinstance(title, str) else None,
                message_count=message_count_value,
                updated_at=str(updated_at) if updated_at is not None else None,
            )
        )
    return items
# endregion
# ============================================


# ============================================
# region create_chat_session
# ============================================
@router.post("/sessions", response_model=ChatSessionCreateResponse)
def create_chat_session(payload: ChatSessionCreateRequest) -> ChatSessionCreateResponse:
    """
    创建会话
    """

    db_url = get_database_url()
    with get_connection(db_url) as conn:
        session_id, title = create_session(conn, payload.user_id)
    return ChatSessionCreateResponse(session_id=session_id, title=title)
# endregion
# ============================================


# ============================================
# region get_chat_history
# ============================================
@router.get("/sessions/{session_id}/history", response_model=ChatHistoryResponse)
def get_chat_history(session_id: str) -> ChatHistoryResponse:
    """
    获取会话历史
    """

    db_url = get_database_url()
    with get_connection(db_url) as conn:
        history = get_recent_history(conn, session_id, limit=20)

    messages: list[ChatMessageItem] = []
    for item in history:
        citations = item.get("citations", [])
        raw_message_id = item.get("message_id")
        if isinstance(raw_message_id, int):
            message_id = raw_message_id
        elif isinstance(raw_message_id, str) and raw_message_id.isdigit():
            message_id = int(raw_message_id)
        else:
            message_id = 0
        citation_items: list[ChatCitation] = []
        if isinstance(citations, list):
            for cite in citations:
                if isinstance(cite, dict):
                    citation_items.append(_normalize_citation(cite))
        messages.append(
            ChatMessageItem(
                message_id=message_id,
                role=str(item.get("role") or ""),
                content=str(item.get("content") or ""),
                citations=citation_items,
                created_at=str(item.get("created_at")),
            )
        )

    return ChatHistoryResponse(session_id=session_id, messages=messages)
# endregion
# ============================================


# ============================================
# region send_chat_message
# ============================================
from services.agent_service import run_agent_loop

@router.post("/sessions/{session_id}/messages")
async def send_chat_message(session_id: str, request: Request) -> StreamingResponse:
    """
    SSE 发送消息 (Agent Mode)
    """

    payload_json = await request.json()
    payload = ChatMessageRequest(**payload_json)
    assistant_id: UUID | None = None
    if payload.assistant_id:
        try:
            assistant_id = UUID(payload.assistant_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="assistant_id 无效") from exc

    async def event_stream() -> AsyncGenerator[str, None]:
        db_url = get_database_url()
        with get_connection(db_url) as conn:
            # 1. 获取并格式化历史
            history_rows = get_recent_history(conn, session_id, limit=20)
            history = []
            for row in history_rows:
                history.append({
                    "role": str(row.get("role")), 
                    "content": str(row.get("content"))
                })

            # 2. 保存用户消息
            append_message(conn, session_id, "user", payload.content)

            # 3. 运行 Agent Loop
            final_content = []
            
            # 使用 Agent Loop 生成回复
            async for sse_string in run_agent_loop(
                session_id,
                payload.content,
                history,
                agent_id=assistant_id,
            ):
                yield sse_string
                
                # 捕获最终回答的内容以保存到数据库
                if sse_string.startswith("event: chunk"):
                    try:
                        line = sse_string.split("\n")[1]
                        if line.startswith("data: "):
                            data_str = line[len("data: "):]
                            data = json.loads(data_str)
                            chunk = data.get("content", "")
                            final_content.append(chunk)
                    except Exception:
                        pass
            
            # 4. 保存助手回复并结束
            full_answer = "".join(final_content)
            if full_answer:
                # Agent 模式下 citations 暂时为空，或者后续从 Agent 状态中提取
                message_id = append_message(
                    conn,
                    session_id,
                    "assistant",
                    full_answer,
                    [],
                )
                yield _sse_event("done", {"message_id": message_id})
            else:
                 # 即使没有回答也发送 done
                 yield _sse_event("done", {"message_id": 0})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
# endregion
# ============================================
