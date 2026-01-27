"""
描述: 会话服务
主要功能:
    - 会话创建
    - 消息写入
    - 历史读取与截断
依赖: psycopg
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from psycopg import Connection


# ============================================
# region create_session
# ============================================
def create_session(conn: Connection, user_id: str | None) -> tuple[str, str | None]:
    session_id = str(uuid.uuid4())
    now = datetime.utcnow()
    conn.execute(
        """
        INSERT INTO chat_sessions (session_id, user_id, title, message_count, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (session_id, user_id, None, 0, now, now),
    )
    conn.commit()
    return session_id, None
# endregion
# ============================================


# ============================================
# region get_recent_sessions
# ============================================
def get_recent_sessions(conn: Connection, limit: int = 10) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT session_id, title, message_count, updated_at
        FROM chat_sessions
        ORDER BY updated_at DESC NULLS LAST
        LIMIT %s
        """,
        (limit,),
    ).fetchall()

    result = []
    for row in rows:
        updated_at = row[3].isoformat() if row[3] else None
        result.append(
            {
                "session_id": str(row[0]),
                "title": row[1],
                "message_count": int(row[2] or 0),
                "updated_at": updated_at,
            }
        )
    return result
# endregion
# ============================================


# ============================================
# region append_message
# ============================================
def append_message(
    conn: Connection,
    session_id: str,
    role: str,
    content: str,
    citations: list[dict[str, object]] | None = None,
    token_count: int | None = None,
) -> int:
    now = datetime.utcnow()
    citations_json = json.dumps(citations, ensure_ascii=False) if citations else None
    row = conn.execute(
        """
        INSERT INTO chat_messages (session_id, role, content, citations, token_count, created_at)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s)
        RETURNING message_id
        """,
        (session_id, role, content, citations_json, token_count, now),
    ).fetchone()

    if not row:
        raise RuntimeError("Failed to insert chat message")

    title = None
    if role == "user":
        title = content[:32]
        conn.execute(
            """
            UPDATE chat_sessions
            SET title = COALESCE(title, %s),
                message_count = message_count + 1,
                updated_at = %s
            WHERE session_id = %s
            """,
            (title, now, session_id),
        )
    else:
        conn.execute(
            """
            UPDATE chat_sessions
            SET message_count = message_count + 1,
                updated_at = %s
            WHERE session_id = %s
            """,
            (now, session_id),
        )

    conn.commit()
    return int(row[0])
# endregion
# ============================================


# ============================================
# region get_recent_history
# ============================================
def get_recent_history(conn: Connection, session_id: str, limit: int = 20) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT message_id, role, content, citations, created_at
        FROM chat_messages
        WHERE session_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (session_id, limit),
    ).fetchall()

    rows = list(reversed(rows))
    history = []
    for row in rows:
        citations = row[3]
        if isinstance(citations, str):
            try:
                citations = json.loads(citations)
            except json.JSONDecodeError:
                citations = []
        history.append(
            {
                "message_id": row[0],
                "role": row[1],
                "content": row[2],
                "citations": citations or [],
                "created_at": row[4].isoformat() if row[4] else None,
            }
        )
    return history
# endregion
# ============================================


# ============================================
# region truncate_history_by_chars
# ============================================
def truncate_history_by_chars(history: list[dict[str, object]], max_chars: int = 2000) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    total_chars = 0

    for msg in reversed(history):
        content = str(msg.get("content") or "")
        msg_chars = len(content)
        if total_chars + msg_chars > max_chars:
            break
        result.append(msg)
        total_chars += msg_chars

    return list(reversed(result))
# endregion
# ============================================
