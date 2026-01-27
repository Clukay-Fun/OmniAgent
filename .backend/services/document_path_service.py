"""
描述: 文档节点路径回填
主要功能:
    - 重建节点路径
依赖: psycopg
"""

from __future__ import annotations

from pathlib import Path

from psycopg import Connection


# ============================================
# region rebuild_node_paths
# ============================================
def rebuild_node_paths(conn: Connection, doc_id: int | None = None) -> tuple[int, int]:
    """
    回填节点路径

    参数:
        conn: 数据库连接
        doc_id: 文档ID（可选）
    返回:
        处理文档数与节点数
    """

    if doc_id is None:
        docs = conn.execute(
            """
            SELECT doc_id, title, file_name, party_a_name
            FROM documents
            ORDER BY doc_id
            """
        ).fetchall()
    else:
        docs = conn.execute(
            """
            SELECT doc_id, title, file_name, party_a_name
            FROM documents
            WHERE doc_id = %s
            """,
            (doc_id,),
        ).fetchall()

    total_nodes = 0
    for doc in docs:
        doc_id_value = doc[0]
        doc_title = doc[1]
        file_name = doc[2]
        party_name = doc[3]
        if not doc_title or str(doc_title).startswith("tmp"):
            if file_name:
                doc_title = Path(str(file_name)).stem
        rows = conn.execute(
            """
            SELECT node_id, parent_id, title
            FROM document_nodes
            WHERE doc_id = %s
            """,
            (doc_id_value,),
        ).fetchall()

        node_map = {int(row[0]): {"parent_id": row[1], "title": row[2]} for row in rows}
        cache: dict[int, list[str]] = {}

        def build_path(node_id: int) -> list[str]:
            if node_id in cache:
                return cache[node_id]
            node = node_map.get(node_id)
            if not node:
                return []
            parent_id = node["parent_id"]
            if parent_id is None:
                path = [str(node["title"] or "Section")]
            else:
                path = build_path(int(parent_id)) + [str(node["title"] or "Section")]
            cache[node_id] = path
            return path

        updates = []
        for node_id in node_map:
            base_path = build_path(node_id)
            if doc_title and base_path and base_path[0] == doc_title:
                base_path = base_path[1:]

            prefix = []
            if doc_title:
                prefix.append(doc_title)
            if party_name:
                prefix.append(party_name)
            updates.append((prefix + base_path, node_id))

        with conn.cursor() as cursor:
            cursor.executemany(
                """
                UPDATE document_nodes
                SET path = %s
                WHERE node_id = %s
                """,
                updates,
            )
        total_nodes += len(updates)

    conn.commit()
    return len(docs), total_nodes
# endregion
# ============================================
