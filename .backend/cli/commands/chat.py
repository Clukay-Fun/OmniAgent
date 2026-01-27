"""
描述: 终端对话 CLI
主要功能:
    - SSE 交互式对话
依赖: typer
"""

from __future__ import annotations

import json
import os
import sys
from typing import Iterable
from urllib.request import Request, urlopen

import typer

app = typer.Typer(help="Chat session commands", invoke_without_command=True)


# ============================================
# region _get_base_url
# ============================================
def _get_base_url(api_base: str | None) -> str:
    if api_base:
        return api_base.rstrip("/")
    host = os.getenv("FASTAPI_HOST", "127.0.0.1")
    port = os.getenv("FASTAPI_PORT", "8001")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"http://{host}:{port}"
# endregion
# ============================================


# ============================================
# region _post_json
# ============================================
def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=60) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}
# endregion
# ============================================


# ============================================
# region _get_json
# ============================================
def _get_json(url: str) -> dict[str, object]:
    request = Request(url, method="GET")
    with urlopen(request, timeout=60) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}
# endregion
# ============================================


# ============================================
# region _get_json_list
# ============================================
def _get_json_list(url: str) -> list[dict[str, str | int | None]]:
    request = Request(url, method="GET")
    with urlopen(request, timeout=60) as response:
        body = response.read().decode("utf-8")
    data = json.loads(body) if body else []
    if not isinstance(data, list):
        return []
    sessions: list[dict[str, str | int | None]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        session_id = item.get("session_id")
        if session_id is None:
            continue
        title = item.get("title")
        message_count = item.get("message_count")
        updated_at = item.get("updated_at")
        sessions.append(
            {
                "session_id": str(session_id),
                "title": str(title) if title is not None else None,
                "message_count": message_count if isinstance(message_count, int) else 0,
                "updated_at": str(updated_at) if updated_at is not None else None,
            }
        )
    return sessions
# endregion
# ============================================


# ============================================
# region _format_citation_line
# ============================================
def _format_citation_line(index: int, citation: dict[str, object]) -> str:
    filename = citation.get("filename") or citation.get("file_name")
    if not filename:
        document_id = citation.get("document_id")
        filename = str(document_id) if document_id is not None else "unknown"
    preview = citation.get("preview")
    preview_text = None
    if isinstance(preview, str):
        preview_text = preview.strip()
    score_value = citation.get("score")
    score_text = None
    if isinstance(score_value, (int, float)):
        score_text = f"{float(score_value):.2f}"
    elif isinstance(score_value, str):
        try:
            score_text = f"{float(score_value):.2f}"
        except ValueError:
            score_text = None
    parts = [f"[{index}] {filename}"]
    if preview_text:
        parts.append(f'"{preview_text}"')
    if score_text:
        parts.append(f"({score_text})")
    return " ".join(parts)
# endregion
# ============================================


# ============================================
# region _iter_sse
# ============================================
def _iter_sse(response) -> Iterable[tuple[str, str]]:
    event = "message"
    data_lines: list[str] = []

    while True:
        line = response.readline()
        if not line:
            break
        decoded = line.decode("utf-8").rstrip("\n")

        if decoded.startswith("event:"):
            event = decoded[len("event:") :].strip()
            continue
        if decoded.startswith("data:"):
            data_lines.append(decoded[len("data:") :].strip())
            continue
        if decoded.strip() == "":
            data = "\n".join(data_lines).strip()
            yield event, data
            event = "message"
            data_lines = []

    if data_lines:
        data = "\n".join(data_lines).strip()
        yield event, data
# endregion
# ============================================


# ============================================
# region _send_sse_message
# ============================================
def _send_sse_message(
    base_url: str,
    session_id: str,
    content: str,
    top_k: int,
    doc_id: int | None,
    assistant_id: str | None,
) -> None:
    url = f"{base_url}/chat/sessions/{session_id}/messages"
    payload: dict[str, object] = {"content": content, "top_k": top_k, "doc_id": doc_id}
    if assistant_id:
        payload["assistant_id"] = assistant_id
    data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})

    with urlopen(request, timeout=300) as response:
        for event, data in _iter_sse(response):
            if event == "status":
                payload = json.loads(data)
                msg = payload.get("message", "")
                typer.echo(f"[{payload.get('state')}] {payload.get('step')}/{payload.get('total')} {msg}")
            elif event == "chunk":
                payload = json.loads(data)
                typer.echo(payload.get("content", ""), nl=False)
            elif event == "message":
                payload = json.loads(data)
                typer.echo("")
                citations = payload.get("citations", [])
                if isinstance(citations, list) and citations:
                    typer.echo("Citations:")
                    for index, cite in enumerate(citations, start=1):
                        if isinstance(cite, dict):
                            typer.echo(_format_citation_line(index, cite))
            elif event == "error":
                payload = json.loads(data)
                typer.echo(f"Error: {payload.get('code')}: {payload.get('message')}")
            elif event == "done":
                payload = json.loads(data)
                message_id = payload.get("message_id")
                if message_id is not None:
                    typer.echo(f"message_id={message_id}")
# endregion
# ============================================


# ============================================
# region _print_sessions
# ============================================
def _print_sessions(base_url: str, limit: int = 10) -> None:
    url = f"{base_url}/chat/sessions?limit={limit}"
    sessions = _get_json_list(url)
    if not sessions:
        typer.echo("No sessions found")
        return
    for session in sessions:
        session_id = session.get("session_id")
        title = session.get("title")
        message_count = session.get("message_count")
        updated_at = session.get("updated_at")
        typer.echo(
            f"{session_id} | title={title} | messages={message_count} | updated_at={updated_at}"
        )
# endregion
# ============================================


# ============================================
# region _print_history
# ============================================
def _print_history(base_url: str, session_id: str) -> None:
    url = f"{base_url}/chat/sessions/{session_id}/history"
    data = _get_json(url)
    raw_messages = data.get("messages", [])
    messages = raw_messages if isinstance(raw_messages, list) else []
    for msg in messages:
        if isinstance(msg, dict):
            typer.echo(f"{msg.get('role')}: {msg.get('content')}")
# endregion
# ============================================


# ============================================
# region chat
# ============================================
@app.callback()
def chat(
    api_base: str | None = typer.Option(None, help="API Base URL"),
    list_sessions: bool = typer.Option(False, "--list", help="列出会话"),
    session_id: str | None = typer.Option(None, "--session", help="会话ID"),
    user_id: str | None = typer.Option(None, help="用户ID"),
    doc_id: int | None = typer.Option(None, help="文档ID"),
    assistant_id: str | None = typer.Option(None, "--assistant", help="助手ID"),
    top_k: int = typer.Option(5, help="召回数量"),
) -> None:
    """
    SSE 交互式对话
    """

    base_url = _get_base_url(api_base)

    if list_sessions:
        _print_sessions(base_url)
        return

    if not session_id:
        payload = _post_json(f"{base_url}/chat/sessions", {"user_id": user_id})
        session_id = str(payload.get("session_id"))
        typer.echo(f"Session: {session_id}")

    typer.echo("Enter message (/exit /new /history)")
    while True:
        try:
            content = typer.prompt(">")
        except (EOFError, KeyboardInterrupt):
            typer.echo("\nBye")
            break

        if content.strip() == "":
            continue
        if content == "/exit":
            typer.echo("Bye")
            break
        if content == "/new":
            payload = _post_json(f"{base_url}/chat/sessions", {"user_id": user_id})
            session_id = str(payload.get("session_id"))
            typer.echo(f"Session: {session_id}")
            continue
        if content == "/history":
            _print_history(base_url, session_id)
            continue

        _send_sse_message(base_url, session_id, content, top_k, doc_id, assistant_id)
# endregion
# ============================================
