"""
描述: 向量化与检索服务
主要功能:
    - 文档节点向量化
    - 相似度检索
依赖: psycopg
"""

from __future__ import annotations

from typing import Sequence

from psycopg import Connection

from services.model_service import embed_texts


# ============================================
# region _format_vector
# ============================================
def _format_vector(values: Sequence[float]) -> str:
    return f"[{','.join(str(float(v)) for v in values)}]"
# endregion
# ============================================


# ============================================
# region build_document_node_embeddings
# ============================================
def build_document_node_embeddings(
    conn: Connection,
    doc_id: int | None = None,
    batch_size: int = 16,
) -> tuple[int, int]:
    """
    构建文档节点向量

    参数:
        conn: 数据库连接
        doc_id: 文档ID
        batch_size: 批量大小
    返回:
        处理数量与更新数量
    """

    processed = 0
    updated = 0

    while True:
        if doc_id is None:
            rows = conn.execute(
                """
                SELECT node_id, title, content
                FROM document_nodes
                WHERE embedding IS NULL AND content IS NOT NULL AND content <> ''
                ORDER BY node_id
                LIMIT %s
                """,
                (batch_size,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT node_id, title, content
                FROM document_nodes
                WHERE doc_id = %s AND embedding IS NULL AND content IS NOT NULL AND content <> ''
                ORDER BY node_id
                LIMIT %s
                """,
                (doc_id, batch_size),
            ).fetchall()

        if not rows:
            break

        texts = [f"{row[1]}\n{row[2]}" if row[1] else str(row[2]) for row in rows]
        vectors = embed_texts(texts)

        with conn.cursor() as cursor:
            for row, vector in zip(rows, vectors, strict=False):
                cursor.execute(
                    """
                    UPDATE document_nodes
                    SET embedding = %s
                    WHERE node_id = %s
                    """,
                    (_format_vector(vector), row[0]),
                )
                updated += 1

        processed += len(rows)

    conn.commit()
    return processed, updated
# endregion
# ============================================


# ============================================
# region search_document_nodes
# ============================================
def search_document_nodes(
    conn: Connection,
    query_text: str,
    top_k: int = 5,
    doc_id: int | None = None,
) -> list[tuple[int, int, str, str, list[str], str | None, str | None, float]]:
    """
    向量检索文档节点

    参数:
        conn: 数据库连接
        query_text: 查询文本
        top_k: 返回数量
        doc_id: 文档ID
    返回:
        检索结果元组
    """

    if query_text.strip() == "":
        return []
    query_vector = embed_texts([query_text])[0]
    vector_literal = _format_vector(query_vector)

    if doc_id is None:
        rows = conn.execute(
            """
            SELECT n.doc_id, n.node_id, n.title, n.content, n.path,
                   d.party_a_name, d.party_a_credit_code,
                   n.embedding <-> %s::vector AS score
            FROM document_nodes n
            JOIN documents d ON d.doc_id = n.doc_id
            WHERE n.embedding IS NOT NULL
            ORDER BY n.embedding <-> %s::vector
            LIMIT %s
            """,
            (vector_literal, vector_literal, top_k),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT n.doc_id, n.node_id, n.title, n.content, n.path,
                   d.party_a_name, d.party_a_credit_code,
                   n.embedding <-> %s::vector AS score
            FROM document_nodes n
            JOIN documents d ON d.doc_id = n.doc_id
            WHERE n.doc_id = %s AND n.embedding IS NOT NULL
            ORDER BY n.embedding <-> %s::vector
            LIMIT %s
            """,
            (vector_literal, doc_id, vector_literal, top_k),
        ).fetchall()

    return [(
        row[0],
        row[1],
        row[2] or "",
        row[3] or "",
        list(row[4]) if row[4] is not None else [],
        row[5],
        row[6],
        float(row[7]),
    ) for row in rows]
# endregion
# ============================================
