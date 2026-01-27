"""
描述: 向量化与检索路由
主要功能:
    - 文档节点向量化
    - 相似度检索
依赖: fastapi
"""

from __future__ import annotations

from fastapi import APIRouter

from db.connection import get_connection, get_database_url
from schemas.vector import (
    VectorBuildRequest,
    VectorBuildResponse,
    VectorSearchRequest,
    VectorSearchResponse,
    VectorSearchHit,
)
from services.vector_service import build_document_node_embeddings, search_document_nodes

router = APIRouter(prefix="/vectors", tags=["vectors"])


# ============================================
# region build_document_embeddings
# ============================================
@router.post("/document-nodes", response_model=VectorBuildResponse)
def build_document_embeddings(payload: VectorBuildRequest) -> VectorBuildResponse:
    """
    构建文档节点向量

    参数:
        payload: 向量构建请求
    返回:
        构建响应
    """

    db_url = get_database_url()
    with get_connection(db_url) as conn:
        processed, updated = build_document_node_embeddings(
            conn, payload.doc_id, payload.batch_size
        )

    return VectorBuildResponse(doc_id=payload.doc_id, processed=processed, updated=updated)
# endregion
# ============================================


# ============================================
# region search_document_embeddings
# ============================================
@router.post("/search", response_model=VectorSearchResponse)
def search_document_embeddings(payload: VectorSearchRequest) -> VectorSearchResponse:
    """
    向量检索

    参数:
        payload: 检索请求
    返回:
        检索响应
    """

    db_url = get_database_url()
    with get_connection(db_url) as conn:
        rows = search_document_nodes(
            conn, payload.query_text, payload.top_k, payload.doc_id
        )

    hits = [
        VectorSearchHit(
            doc_id=row[0],
            node_id=row[1],
            title=row[2],
            content=row[3],
            path=row[4],
            party_a_name=row[5],
            party_a_credit_code=row[6],
            score=float(row[7]) if row[7] is not None else 0.0,
        )
        for row in rows
    ]

    return VectorSearchResponse(query_text=payload.query_text, hits=hits)
# endregion
# ============================================
