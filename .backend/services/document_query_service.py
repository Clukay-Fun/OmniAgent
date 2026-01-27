"""
描述: 文档结构化查询服务
主要功能:
    - 查询结构化 JSON
    - 构建节点树
依赖: psycopg
"""

from __future__ import annotations

import json
from datetime import datetime

from psycopg import Connection

from schemas.document import (
    DocumentNodeSearchResponse,
    DocumentStructureResponse,
    DocumentTreeNode,
)


# ============================================
# region get_document_structure
# ============================================
def get_document_structure(conn: Connection, doc_id: int) -> DocumentStructureResponse | None:
    """
    获取文档结构化结果

    参数:
        conn: 数据库连接
        doc_id: 文档ID
    返回:
        结构化响应或 None
    """

    row = conn.execute(
        """
        SELECT doc_id, structure_model, structure_payload, structure_raw,
               structure_error, structure_created_at
        FROM document_nodes
        WHERE doc_id = %s AND parent_id IS NULL
        ORDER BY COALESCE(order_index, 0), node_id
        LIMIT 1
        """,
        (doc_id,),
    ).fetchone()

    if not row:
        return None

    payload = row[2]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None

    created_at = row[5].isoformat() if isinstance(row[5], datetime) else None
    return DocumentStructureResponse(
        doc_id=row[0],
        model_name=row[1],
        payload=payload,
        raw_text=row[3],
        error=row[4],
        created_at=created_at,
    )
# endregion
# ============================================


# ============================================
# region get_document_tree
# ============================================
def get_document_tree(conn: Connection, doc_id: int) -> DocumentTreeNode | None:
    """
    构建文档节点树

    参数:
        conn: 数据库连接
        doc_id: 文档ID
    返回:
        文档树节点
    """

    rows = conn.execute(
        """
        SELECT node_id, parent_id, level, title, content, path
        FROM document_nodes
        WHERE doc_id = %s
        ORDER BY order_index
        """,
        (doc_id,),
    ).fetchall()

    if not rows:
        return None

    nodes: dict[int, DocumentTreeNode] = {}
    children_map: dict[int | None, list[int]] = {}
    for row in rows:
        node_id = int(row[0])
        parent_id = row[1]
        parent_id = int(parent_id) if parent_id is not None else None
        nodes[node_id] = DocumentTreeNode(
            title=row[3] or "Section",
            level=int(row[2]) if row[2] is not None else 0,
            content=row[4] or "",
            children=[],
            path=list(row[5]) if row[5] is not None else [],
        )
        children_map.setdefault(parent_id, []).append(node_id)

    def attach_children(node_id: int) -> None:
        node = nodes[node_id]
        child_ids = children_map.get(node_id, [])
        for child_id in child_ids:
            attach_children(child_id)
            node.children.append(nodes[child_id])

    root_ids = children_map.get(None, [])
    for root_id in root_ids:
        attach_children(root_id)

    if len(root_ids) == 1:
        return nodes[root_ids[0]]

    title_row = conn.execute(
        """
        SELECT title, metadata->>'party_a_name' FROM documents WHERE doc_id = %s
        """,
        (doc_id,),
    ).fetchone()
    root_title = title_row[0] if title_row and title_row[0] else "Document"
    party_name = title_row[1] if title_row else None

    root_path = [root_title]
    if party_name:
        root_path.append(party_name)

    root = DocumentTreeNode(title=root_title, level=0, content="", children=[], path=root_path)
    for root_id in root_ids:
        root.children.append(nodes[root_id])
    return root
# endregion
# ============================================


# ============================================
# region search_document_nodes
# ============================================
def search_document_nodes(
    conn: Connection,
    query: str | None,
    title: str | None,
    path: str | None,
    limit: int,
) -> list[DocumentNodeSearchResponse]:
    """
    搜索文档节点

    参数:
        conn: 数据库连接
        query: 正文查询
        title: 标题查询
        path: 路径查询
        limit: 返回数量
    返回:
        搜索结果
    """

    filters = []
    params: list[object] = []

    query_text = query.strip() if query else ""
    if query_text:
        filters.append("to_tsvector('simple', n.content) @@ plainto_tsquery('simple', %s)")
        params.append(query_text)

    if title:
        filters.append("n.title ILIKE %s")
        params.append(f"%{title}%")

    if path:
        filters.append("n.path @> %s::text[]")
        params.append([path])

    where_clause = " AND ".join(filters) if filters else "TRUE"
    if query_text:
        sql = (
            "SELECT n.doc_id, n.node_id, n.title, n.content, n.path, "
            "d.metadata->>'party_a_name', d.metadata->>'party_a_credit_code', "
            "ts_rank(to_tsvector('simple', n.content), plainto_tsquery('simple', %s)) AS score "
            "FROM document_nodes n JOIN documents d ON d.doc_id = n.doc_id WHERE "
            f"{where_clause} "
            "ORDER BY score DESC NULLS LAST, n.node_id "
            "LIMIT %s"
        )
        params_for_rank = params.copy()
        params_for_rank.insert(0, query_text)
        params_for_rank.append(limit)
    else:
        sql = (
            "SELECT n.doc_id, n.node_id, n.title, n.content, n.path, "
            "d.metadata->>'party_a_name', d.metadata->>'party_a_credit_code', "
            "NULL AS score "
            "FROM document_nodes n JOIN documents d ON d.doc_id = n.doc_id WHERE "
            f"{where_clause} "
            "ORDER BY n.node_id "
            "LIMIT %s"
        )
        params_for_rank = params.copy()
        params_for_rank.append(limit)

    rows = conn.execute(sql, params_for_rank).fetchall()
    results = []
    for row in rows:
        results.append(
            DocumentNodeSearchResponse(
                doc_id=row[0],
                node_id=row[1],
                title=row[2] or "",
                content=row[3] or "",
                path=list(row[4]) if row[4] is not None else [],
                party_a_name=row[5],
                party_a_credit_code=row[6],
                score=float(row[7]) if row[7] is not None else None,
            )
        )
    return results
# endregion
# ============================================
