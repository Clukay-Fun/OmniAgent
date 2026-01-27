"""
描述: 文档解析路由
主要功能:
    - 文档解析与结构化
依赖: fastapi, markitdown
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from db.connection import get_connection, get_database_url
from schemas.document import (
    DocumentNodeSearchResponse,
    DocumentParseResponse,
    DocumentStructureResponse,
    DocumentTreeNode,
)
from services.document_query_service import (
    get_document_structure,
    get_document_tree,
    search_document_nodes,
)
from services.document_service import parse_document

router = APIRouter(prefix="/documents", tags=["documents"])


# ============================================
# region parse_document_endpoint
# ============================================
@router.post("/parse", response_model=DocumentParseResponse, status_code=status.HTTP_200_OK)
def parse_document_endpoint(
    file: UploadFile = File(...),
    include_markdown: bool = False,
    persist: bool = False,
    use_model_structure: bool = True,
) -> DocumentParseResponse:
    """
    解析文档

    参数:
        file: 上传文件
        include_markdown: 是否返回 Markdown
        persist: 是否持久化
        use_model_structure: 是否调用模型结构化
    返回:
        解析响应
    """

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file name is required")

    suffix = Path(file.filename).suffix or ".docx"
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(file.file.read())
            temp_path = temp_file.name

        if persist:
            db_url = get_database_url()
            with get_connection(db_url) as conn:
                return parse_document(
                    temp_path,
                    file.filename,
                    include_markdown,
                    True,
                    use_model_structure,
                    conn,
                )

        return parse_document(
            temp_path,
            file.filename,
            include_markdown,
            False,
            use_model_structure,
            None,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
# endregion
# ============================================


# ============================================
# region get_document_structure_endpoint
# ============================================
@router.get("/{doc_id}/structure", response_model=DocumentStructureResponse)
def get_document_structure_endpoint(doc_id: int) -> DocumentStructureResponse:
    """
    获取结构化结果

    参数:
        doc_id: 文档ID
    返回:
        结构化响应
    """

    db_url = get_database_url()
    with get_connection(db_url) as conn:
        result = get_document_structure(conn, doc_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="structure not found")
    return result
# endregion
# ============================================


# ============================================
# region get_document_tree_endpoint
# ============================================
@router.get("/{doc_id}/tree", response_model=DocumentTreeNode)
def get_document_tree_endpoint(doc_id: int) -> DocumentTreeNode:
    """
    获取文档节点树

    参数:
        doc_id: 文档ID
    返回:
        文档树
    """

    db_url = get_database_url()
    with get_connection(db_url) as conn:
        result = get_document_tree(conn, doc_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return result
# endregion
# ============================================


# ============================================
# region search_document_nodes_endpoint
# ============================================
@router.get("/nodes/search", response_model=list[DocumentNodeSearchResponse])
def search_document_nodes_endpoint(
    query: str | None = None,
    title: str | None = None,
    path: str | None = None,
    limit: int = 20,
) -> list[DocumentNodeSearchResponse]:
    """
    搜索文档节点

    参数:
        query: 正文查询
        title: 标题查询
        path: 路径匹配
        limit: 返回数量
    返回:
        搜索结果
    """

    db_url = get_database_url()
    with get_connection(db_url) as conn:
        return search_document_nodes(conn, query, title, path, limit)
# endregion
# ============================================
