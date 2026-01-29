"""
Feishu webhook handler.
"""

from __future__ import annotations

import base64
import json
import time
import logging
from typing import Any

from Crypto.Cipher import AES
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from src.core.orchestrator import AgentOrchestrator
from src.core.session import SessionManager
from src.config import get_settings
from src.llm.provider import create_llm_client
from src.mcp.client import MCPClient
from src.utils.feishu_api import send_message


router = APIRouter()
settings = get_settings()
session_manager = SessionManager(settings.session)
mcp_client = MCPClient(settings)
llm_client = create_llm_client(settings.llm)

# 初始化 Agent 编排器（使用技能系统）
agent_core = AgentOrchestrator(
    settings=settings,
    session_manager=session_manager,
    mcp_client=mcp_client,
    llm_client=llm_client,
    skills_config_path="config/skills.yaml",
)
logger = logging.getLogger(__name__)


class EventDeduplicator:
    def __init__(self, ttl_seconds: int, max_size: int) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._items: dict[str, float] = {}

    def is_duplicate(self, key: str) -> bool:
        now = time.time()
        self._items = {
            key: ts for key, ts in self._items.items() if now - ts <= self._ttl
        }
        if key in self._items:
            return True
        if len(self._items) >= self._max_size:
            self._items.pop(next(iter(self._items)))
        self._items[key] = now
        return False


deduplicator = EventDeduplicator(
    settings.webhook.dedup.ttl_seconds,
    settings.webhook.dedup.max_size,
)


def _decrypt_event(encrypt_text: str, encrypt_key: str) -> dict[str, Any]:
    raw = base64.b64decode(encrypt_text)
    key = encrypt_key.encode("utf-8")
    if len(key) != 32:
        raise ValueError("encrypt_key must be 32 bytes")
    iv = key[:16]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(raw)
    pad_len = decrypted[-1]
    content = decrypted[:-pad_len]
    return json.loads(content.decode("utf-8"))


def _is_private_chat(message: dict[str, Any]) -> bool:
    return message.get("chat_type") == "p2p"


def _get_text_content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if not content:
        return ""
    try:
        payload = json.loads(content)
        return payload.get("text") or ""
    except json.JSONDecodeError:
        return ""


@router.get("/feishu/webhook")
async def feishu_webhook_get() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/feishu/webhook")
async def feishu_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    payload = await request.json()

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    if payload.get("encrypt"):
        if not settings.feishu.encrypt_key:
            raise HTTPException(status_code=400, detail="encrypt_key is required")
        payload = _decrypt_event(payload["encrypt"], settings.feishu.encrypt_key)

    header = payload.get("header") or {}
    if settings.feishu.verification_token:
        token = header.get("token") or payload.get("token")
        if token != settings.feishu.verification_token:
            raise HTTPException(status_code=401, detail="Verification failed")

    event_id = header.get("event_id") or payload.get("event_id")

    event = payload.get("event") or {}
    message = event.get("message") or {}
    sender = event.get("sender") or {}

    message_id = message.get("message_id") or message.get("messageId")
    dedup_key = message_id or event_id
    if dedup_key and settings.webhook.dedup.enabled and deduplicator.is_duplicate(dedup_key):
        return {"status": "duplicate"}

    if settings.webhook.filter.ignore_bot_message and sender.get("sender_type") == "bot":
        return {"status": "ignored"}

    if settings.webhook.filter.private_chat_only and not _is_private_chat(message):
        return {"status": "ignored"}

    message_type = message.get("message_type")
    if message_type not in settings.webhook.filter.allowed_message_types:
        return {"status": "ignored"}

    background_tasks.add_task(_process_message, message, sender)
    return {"status": "ok"}


async def _process_message(message: dict[str, Any], sender: dict[str, Any]) -> None:
    text = _get_text_content(message)
    if not text:
        return

    chat_id = message.get("chat_id")
    message_id = message.get("message_id")
    user_id = sender.get("sender_id", {}).get("user_id") or chat_id or "unknown"

    if not chat_id:
        return

    try:
        reply = await agent_core.handle_message(user_id, text)
        if chat_id.startswith("test-"):
            logger.info("Test chat_id, reply suppressed: %s", reply.get("text", ""))
            return
        if reply.get("type") == "card":
            msg_type = "interactive"
            content = reply.get("card") or {}
        else:
            msg_type = "text"
            content = {"text": reply.get("text", "")}
        await send_message(settings, chat_id, msg_type, content, reply_message_id=message_id)
    except Exception as exc:
        error_text = settings.reply.templates.error.format(message=str(exc))
        await send_message(
            settings,
            chat_id,
            "text",
            {"text": error_text},
            reply_message_id=message_id,
        )
