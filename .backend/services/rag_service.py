"""
描述: RAG 问答服务
主要功能:
    - 向量检索
    - 生成答案
    - 构造引用
依赖: psycopg
"""

from __future__ import annotations

from psycopg import Connection

from schemas.rag import QACitation, QAResponse
from services.model_service import chat
from services.vector_service import search_document_nodes


# ============================================
# region _fetch_document_meta
# ============================================
def _fetch_document_meta(conn: Connection, doc_id: int) -> tuple[str | None, str | None]:
    row = conn.execute(
        """
        SELECT title, file_name FROM documents WHERE doc_id = %s
        """,
        (doc_id,),
    ).fetchone()
    if not row:
        return None, None
    return row[0], row[1]
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
# region build_answer
# ============================================
def build_answer(conn: Connection, question: str, top_k: int, doc_id: int | None) -> QAResponse:
    """
    构建问答响应

    参数:
        conn: 数据库连接
        question: 问题
        top_k: 召回数量
        doc_id: 文档ID
    返回:
        问答响应
    """

    hits = search_document_nodes(conn, question, top_k, doc_id)
    citations: list[QACitation] = []

    meta_cache: dict[int, tuple[str | None, str | None]] = {}
    context_parts: list[str] = []
    for hit in hits:
        hit_doc_id = int(hit[0])
        node_id = int(hit[1])
        title = hit[2]
        content = hit[3]
        path = hit[4]
        score = float(hit[7])

        if hit_doc_id not in meta_cache:
            meta_cache[hit_doc_id] = _fetch_document_meta(conn, hit_doc_id)
        _, file_name = meta_cache[hit_doc_id]

        citations.append(
            QACitation(
                document_id=str(hit_doc_id),
                node_id=node_id,
                filename=file_name,
                preview=_build_preview(content),
                score=score,
                path=path,
            )
        )

        context_parts.append(
            "\n".join(
                [
                    f"[Source {hit_doc_id}:{node_id}]",
                    f"Title: {title}",
                    f"Path: {' > '.join(path)}",
                    f"Content: {content}",
                ]
            )
        )

    context_block = "\n\n".join(context_parts)
    prompt = (
        "You are a RAG assistant. Answer the question using ONLY the context. "
        "Cite sources in the answer using [doc_id:node_id].\n\n"
        f"Question: {question}\n\n"
        f"Context:\n{context_block}"
    )

    response = chat(
        [
            {"role": "system", "content": "Answer in Chinese."},
            {"role": "user", "content": prompt},
        ]
    )

    answer = response.content or ""
    return QAResponse(answer=answer, citations=citations)
# endregion
# ============================================


# ============================================
# region retrieve_with_context
# ============================================
def retrieve_with_context(
    conn: Connection,
    query: str,
    history: list[dict[str, object]],
    top_k: int,
    doc_id: int | None,
) -> tuple[list[tuple], list[QACitation], str]:
    if history:
        last_content = str(history[-1].get("content") or "")[:100]
        context_query = f"{last_content} {query}".strip()
    else:
        context_query = query

    hits = search_document_nodes(conn, context_query, top_k, doc_id)
    citations: list[QACitation] = []
    meta_cache: dict[int, tuple[str | None, str | None]] = {}
    context_parts: list[str] = []

    for hit in hits:
        hit_doc_id = int(hit[0])
        node_id = int(hit[1])
        title = hit[2]
        content = hit[3]
        path = hit[4]
        score = float(hit[7])

        if hit_doc_id not in meta_cache:
            meta_cache[hit_doc_id] = _fetch_document_meta(conn, hit_doc_id)
        _, file_name = meta_cache[hit_doc_id]

        citations.append(
            QACitation(
                document_id=str(hit_doc_id),
                node_id=node_id,
                filename=file_name,
                preview=_build_preview(content),
                score=score,
                path=path,
            )
        )

        context_parts.append(
            "\n".join(
                [
                    f"[Source {hit_doc_id}:{node_id}]",
                    f"Title: {title}",
                    f"Path: {' > '.join(path)}",
                    f"Content: {content}",
                ]
            )
        )

    context_block = "\n\n".join(context_parts)
    return hits, citations, context_block
# endregion
# ============================================


# ============================================
# region generate_answer
# ============================================
def generate_answer(question: str, context_block: str, history_text: str) -> str:
    prompt = (
        "你是一个基于文档的问答助手。根据提供的上下文回答问题，"
        "如果上下文中没有相关信息，请诚实说明。\n\n"
        f"历史对话：\n{history_text}\n\n"
        f"参考文档：\n{context_block}\n\n"
        f"问题：{question}"
    )

    response = chat(
        [
            {"role": "system", "content": "Answer in Chinese."},
            {"role": "user", "content": prompt},
        ]
    )
    return response.content or ""
# endregion
# ============================================
