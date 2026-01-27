"""
描述: 文档解析服务
主要功能:
    - MarkItDown 转 Markdown
    - Markdown 结构化为节点树
    - 可选持久化节点
依赖: markitdown, psycopg
"""

from __future__ import annotations

import json
import logging
import re
from typing import cast
from datetime import datetime
from pathlib import Path

from markitdown import MarkItDown
from psycopg import Connection

from config.settings import get_settings
from schemas.document import DocumentNode, DocumentParseResponse, DocumentParseStats
from services.model_service import extract_json, structure_document

logger = logging.getLogger(__name__)

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*)$")


# ============================================
# region convert_to_markdown
# ============================================
def convert_to_markdown(file_path: str) -> tuple[str, str | None]:
    """
    转换文件为 Markdown

    参数:
        file_path: 文件路径
    返回:
        Markdown 内容与标题
    """

    converter = MarkItDown()
    result = converter.convert(file_path)
    return result.markdown, result.title
# endregion
# ============================================


# ============================================
# region extract_party_a
# ============================================
def extract_party_a(markdown: str, file_name: str | None) -> tuple[str | None, str | None]:
    prompt = (
        "从合同文本中抽取甲方全称，仅返回JSON。"
        "格式: {\"party_a_name\": \"...\"}. "
        "如果无法识别，返回空字符串。"
    )
    try:
        response = extract_json(f"{prompt}\n\n{markdown}")
        if response.content:
            json_text = _extract_json_text(response.content)
            payload = json.loads(json_text)
            if isinstance(payload, dict):
                name = str(payload.get("party_a_name") or "").strip()
                if name:
                    return name, "content"
    except Exception as exc:
        logger.warning("Failed to extract party_a from content", exc_info=exc)

    if file_name:
        name = Path(file_name).stem.strip()
        return (name if name else None), "filename"

    return None, None
# endregion
# ============================================


# ============================================
# region _populate_node_paths
# ============================================
def _populate_node_paths(
    nodes: list[DocumentNode],
    doc_title: str | None,
    party_a_name: str | None,
) -> None:
    node_map = {node.node_id: node for node in nodes}
    cache: dict[int, list[str]] = {}

    def build_path(node_id: int) -> list[str]:
        if node_id in cache:
            return cache[node_id]
        node = node_map.get(node_id)
        if not node:
            return []
        if node.parent_id is None or node.parent_id not in node_map:
            path = [node.title]
        else:
            path = build_path(node.parent_id) + [node.title]
        cache[node_id] = path
        return path

    for node in nodes:
        base_path = build_path(node.node_id)
        if doc_title and base_path and base_path[0] == doc_title:
            base_path = base_path[1:]

        prefix: list[str] = []
        if doc_title:
            prefix.append(doc_title)
        if party_a_name:
            prefix.append(party_a_name)

        node.path = prefix + base_path
# endregion
# ============================================

# ============================================
# region persist_structure
# ============================================
def persist_structure(
    conn: Connection,
    doc_id: int,
    model_name: str,
    payload: dict[str, object] | None,
    raw_text: str | None,
    error: str | None,
) -> None:
    """
    持久化结构化结果

    参数:
        conn: 数据库连接
        doc_id: 文档ID
        model_name: 模型名称
        payload: 结构化 JSON
        raw_text: 原始文本
        error: 错误信息
    返回:
        None
    """

    payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
    row = conn.execute(
        """
        SELECT node_id
        FROM document_nodes
        WHERE doc_id = %s AND parent_id IS NULL
        ORDER BY COALESCE(order_index, 0), node_id
        LIMIT 1
        """,
        (doc_id,),
    ).fetchone()

    if not row:
        return

    conn.execute(
        """
        UPDATE document_nodes
        SET structure_model = %s,
            structure_payload = %s::jsonb,
            structure_raw = %s,
            structure_error = %s,
            structure_created_at = %s
        WHERE node_id = %s
        """,
        (
            model_name,
            payload_json,
            raw_text,
            error,
            datetime.utcnow(),
            row[0],
        ),
    )
    conn.commit()
# endregion
# ============================================


# ============================================
# region _extract_json_text
# ============================================
def _extract_json_text(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        lines = stripped.splitlines()
        if lines and lines[0].lower().startswith("json"):
            stripped = "\n".join(lines[1:])
    return stripped.strip()
# endregion
# ============================================


# ============================================
# region _build_nodes_from_structure
# ============================================
def _build_nodes_from_structure(payload: dict[str, object]) -> list[DocumentNode] | None:
    nodes_value = payload.get("nodes")
    nodes: list[DocumentNode] = []

    def parse_flat_nodes(items: list[object]) -> None:
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            node_id_value = item.get("node_id")
            try:
                node_id = int(node_id_value) if node_id_value is not None else index
            except (TypeError, ValueError):
                node_id = index

            parent_id = item.get("parent_id")
            if parent_id in ("", "null"):
                parent_id = None
            if parent_id is not None:
                try:
                    parent_id = int(parent_id)
                except (TypeError, ValueError):
                    parent_id = None

            level_value = item.get("level")
            try:
                level = int(level_value) if level_value is not None else 1
            except (TypeError, ValueError):
                level = 1

            title = item.get("title")
            title = str(title) if title is not None else "Section"
            content = item.get("content")
            content = str(content) if content is not None else ""

            nodes.append(
                DocumentNode(
                    node_id=node_id,
                    parent_id=parent_id,
                    level=level,
                    title=title,
                    content=content,
                    path=[],
                )
            )

    def parse_tree(node_data: dict[str, object], parent_id: int | None, level_hint: int) -> None:
        node_id = len(nodes) + 1
        level_value = node_data.get("level")
        if isinstance(level_value, (int, float, str)):
            try:
                safe_value = cast(int | float | str, level_value)
                level = int(safe_value)
            except (TypeError, ValueError):
                level = level_hint
        else:
            level = level_hint

        title = node_data.get("title")
        title = str(title) if title is not None else "Section"
        content = node_data.get("content")
        content = str(content) if content is not None else ""

        nodes.append(
            DocumentNode(
                node_id=node_id,
                parent_id=parent_id,
                level=level,
                title=title,
                content=content,
                path=[],
            )
        )

        children = node_data.get("children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    parse_tree(child, node_id, level + 1)

    if isinstance(nodes_value, list) and nodes_value:
        parse_flat_nodes(nodes_value)
    elif isinstance(payload.get("title"), str):
        parse_tree(payload, None, 0)

    return nodes or None
# endregion
# ============================================


# ============================================
# region parse_markdown_nodes
# ============================================
def parse_markdown_nodes(markdown: str) -> list[DocumentNode]:
    """
    解析 Markdown 为节点结构

    参数:
        markdown: Markdown 内容
    返回:
        节点列表
    """

    nodes: list[DocumentNode] = []
    stack: list[tuple[int, int]] = []
    node_id = 0
    current_node: DocumentNode | None = None
    content_lines: list[str] = []

    # ============================================
    # region flush_content
    # ============================================
    def flush_content() -> None:
        nonlocal content_lines
        if current_node is None:
            return
        current_node.content = "\n".join(content_lines).strip()
        content_lines = []
    # endregion
    # ============================================

    for line in markdown.splitlines():
        level = 1
        title = ""
        parent_id = None
        match = HEADING_PATTERN.match(line.strip())
        if match:
            flush_content()
            level = len(match.group(1))
            title = match.group(2).strip()

            while stack and stack[-1][0] >= level:
                stack.pop()

            parent_id = stack[-1][1] if stack else None
            node_id += 1
            current_node = DocumentNode(
                node_id=node_id,
                parent_id=parent_id,
                level=level,
                title=title,
                content="",
                path=[],
            )
            nodes.append(current_node)
            stack.append((level, node_id))
            continue

        if current_node is None:
            node_id += 1
            current_node = DocumentNode(
                node_id=node_id,
                parent_id=None,
                level=1,
                title="Document",
                content="",
                path=[],
            )
            nodes.append(current_node)
            stack = [(1, node_id)]

        content_lines.append(line)

    flush_content()
    return nodes
# endregion
# ============================================


# ============================================
# region persist_document
# ============================================
def persist_document(
    conn: Connection,
    title: str | None,
    file_name: str | None,
    party_a_name: str | None,
    party_a_credit_code: str | None,
    party_a_source: str | None,
    nodes: list[DocumentNode],
) -> int:
    """
    持久化文档节点

    参数:
        conn: 数据库连接
        title: 文档标题
        file_name: 文件名
        nodes: 节点列表
    返回:
        文档ID
    """

    now = datetime.utcnow()
    metadata: dict[str, object] = {}
    if party_a_name:
        metadata["party_a_name"] = party_a_name
    if party_a_credit_code:
        metadata["party_a_credit_code"] = party_a_credit_code
    if party_a_source:
        metadata["party_a_source"] = party_a_source

    row = conn.execute(
        """
        INSERT INTO documents (
            title,
            file_name,
            metadata,
            created_at
        )
        VALUES (%s, %s, %s, %s)
        RETURNING doc_id
        """,
        (title, file_name, metadata, now),
    ).fetchone()

    if not row:
        raise RuntimeError("Failed to persist document")

    doc_id = row[0]
    id_map: dict[int, int] = {}
    for order_index, node in enumerate(nodes):
        parent_id = id_map.get(node.parent_id) if node.parent_id is not None else None
        row = conn.execute(
            """
            INSERT INTO document_nodes (
                doc_id,
                parent_id,
                level,
                title,
                content,
                path,
                order_index,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING node_id
            """,
            (
                doc_id,
                parent_id,
                node.level,
                node.title,
                node.content,
                node.path,
                order_index,
                now,
            ),
        ).fetchone()
        if not row:
            raise RuntimeError("Failed to persist document node")
        id_map[node.node_id] = row[0]

    conn.commit()
    return doc_id
# endregion
# ============================================


# ============================================
# region parse_document
# ============================================
def parse_document(
    file_path: str,
    file_name: str | None,
    include_markdown: bool,
    persist: bool,
    use_model_structure: bool = True,
    conn: Connection | None = None,
) -> DocumentParseResponse:
    """
    解析文档并结构化

    参数:
        file_path: 文件路径
        file_name: 文件名
        include_markdown: 是否返回 Markdown
        persist: 是否持久化
        use_model_structure: 是否调用模型结构化
        conn: 数据库连接
    返回:
        文档解析响应
    """

    markdown, title = convert_to_markdown(file_path)
    nodes = parse_markdown_nodes(markdown)
    doc_id = None
    structure_result = None
    structure_error = None
    structure_payload: dict[str, object] | None = None
    party_a_name = None
    party_a_source = None
    party_a_credit_code = None

    if use_model_structure:
        try:
            node_payload = [
                {
                    "node_id": node.node_id,
                    "parent_id": node.parent_id,
                    "level": node.level,
                    "title": node.title,
                    "content": node.content,
                }
                for node in nodes
            ]
            response = structure_document(markdown, node_payload)
            structure_result = response.content
            if structure_result:
                json_text = _extract_json_text(structure_result)
                payload = json.loads(json_text)
                structure_payload = payload if isinstance(payload, dict) else None
                structured_nodes = _build_nodes_from_structure(payload)
                if structured_nodes:
                    nodes = structured_nodes
        except Exception as exc:
            structure_error = str(exc)

    party_a_name, party_a_source = extract_party_a(markdown, file_name)

    party_a_credit_code = None

    doc_title = title
    if not doc_title or str(doc_title).startswith("tmp"):
        doc_title = Path(file_name or file_path).stem
    _populate_node_paths(nodes, doc_title, party_a_name)

    if persist:
        if conn is None:
            raise RuntimeError("Database connection is required for persistence")
        doc_id = persist_document(
            conn,
            doc_title,
            file_name,
            party_a_name,
            None,
            party_a_source,
            nodes,
        )
        persist_structure(
            conn,
            doc_id,
            get_settings().model.doc_structure_model,
            structure_payload,
            structure_result,
            structure_error,
        )

    stats = DocumentParseStats(
        node_count=len(nodes),
        line_count=len(markdown.splitlines()),
        content_length=len(markdown),
    )

    return DocumentParseResponse(
        doc_id=doc_id,
        title=title,
        file_name=file_name,
        party_a_name=party_a_name,
        party_a_credit_code=party_a_credit_code,
        party_a_source=party_a_source,
        markdown=markdown if include_markdown else None,
        structure_result=structure_result,
        structure_error=structure_error,
        nodes=nodes,
        stats=stats,
    )
# endregion
# ============================================
