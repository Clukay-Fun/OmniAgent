"""
描述: RAG 问答路由
主要功能:
    - 检索增强问答
依赖: fastapi
"""

from __future__ import annotations

from fastapi import APIRouter

from db.connection import get_connection, get_database_url
from schemas.rag import QARequest, QAResponse
from services.rag_service import build_answer

router = APIRouter(prefix="/qa", tags=["qa"])


# ============================================
# region ask_question
# ============================================
@router.post("/ask", response_model=QAResponse)
def ask_question(payload: QARequest) -> QAResponse:
    """
    RAG 问答

    参数:
        payload: 问答请求
    返回:
        问答响应
    """

    db_url = get_database_url()
    with get_connection(db_url) as conn:
        return build_answer(conn, payload.question, payload.top_k, payload.doc_id)
# endregion
# ============================================
