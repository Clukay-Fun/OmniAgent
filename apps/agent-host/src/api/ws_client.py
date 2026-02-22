"""
描述: 飞书 WebSocket 长连接客户端
主要功能:
    - 使用 lark-oapi SDK 建立长连接接收事件
    - 无需公网 IP 或 内网穿透 (适合开发与内网部署)
    - 异步分发消息至 Agent 核心
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from dotenv import load_dotenv

# 必须在导入 config 之前加载 .env
load_dotenv()

# Windows 兼容性：设置事件循环策略
# lark_oapi 在导入时会创建事件循环，需要先设置策略
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from src.adapters.channels.feishu.event_adapter import FeishuEventAdapter
from src.adapters.channels.feishu.formatter import FeishuFormatter
from src.adapters.channels.feishu.processing_status import create_reaction_status_emitter
from src.adapters.channels.feishu.skills.bitable_writer import BitableWriter
from src.api.chunk_assembler import ChunkAssembler
from src.api.conversation_scope import build_session_key
from src.api.file_pipeline import (
    build_file_unavailable_guidance,
    is_file_pipeline_message,
    resolve_file_markdown,
)
from src.api.inbound_normalizer import normalize_content
from src.config import get_settings
from src.core.orchestrator import AgentOrchestrator
from src.core.response.models import RenderedResponse
from src.core.session import SessionManager
from src.llm.provider import create_llm_client
from src.mcp.client import MCPClient
from src.utils.metrics import record_inbound_message
from src.utils.logger import setup_logging

logger = logging.getLogger(__name__)


# ============================================
# region 初始化
# ============================================
settings = get_settings()
setup_logging(settings.logging)

session_manager = SessionManager(settings.session)
mcp_client = MCPClient(settings)
llm_client = create_llm_client(settings.llm)

agent_core = AgentOrchestrator(
    settings=settings,
    session_manager=session_manager,
    mcp_client=mcp_client,
    llm_client=llm_client,
    skills_config_path="config/skills.yaml",
    data_writer=BitableWriter(mcp_client),
)
chunk_assembler = ChunkAssembler(
    enabled=bool(settings.webhook.chunk_assembler.enabled),
    window_seconds=float(settings.webhook.chunk_assembler.window_seconds),
    stale_window_seconds=float(settings.webhook.chunk_assembler.stale_window_seconds),
    max_segments=int(settings.webhook.chunk_assembler.max_segments),
    max_chars=int(settings.webhook.chunk_assembler.max_chars),
)
_pending_chunk_flush_tasks: dict[str, asyncio.Task[Any]] = {}


def _flush_orphan_chunks(session_key: str) -> None:
    decision = chunk_assembler.drain(session_key)
    if decision.should_process:
        logger.warning(
            "检测到会话过期残留分片，已执行兜底冲刷",
            extra={
                "event_code": "ws.chunk_assembler.orphan_flushed",
                "session_key": session_key,
                "text_len": len(decision.text),
            },
        )


session_manager.register_expire_listener(_flush_orphan_chunks)
# endregion
# ============================================


# endregion


# region 消息处理逻辑
async def handle_message_async(
    user_id: str,
    chat_id: str,
    chat_type: str,
    text: str,
    message_id: str,
    message_type: str = "text",
    attachments: list[Any] | None = None,
) -> None:
    """
    异步处理消息并发送回复
    
    参数:
        user_id: 用户 ID
        chat_id: 会话 ID
        text: 消息文本
        message_id: 消息 ID（用于回复）
    """
    try:
        logger.info(
            "通过长连接处理消息",
            extra={
                "event_code": "ws.message.processing",
                "user_id": user_id,
                "text": text,
            },
        )
        
        file_markdown = ""
        file_provider = "none"
        if attachments:
            file_markdown, guidance, file_provider = await resolve_file_markdown(
                attachments=attachments,
                settings=settings,
                message_type=message_type,
            )
            if guidance and not file_markdown and message_type in {"file", "audio", "image"}:
                text = guidance
        elif message_type in {"file", "audio", "image"}:
            text = build_file_unavailable_guidance("extractor_disabled")

        # 调用 Agent 处理
        if message_type in {"file", "audio", "image"} and text.startswith("已收到文件"):
            reply = {"type": "text", "text": text}
        else:
            reply = await agent_core.handle_message(
                user_id,
                text,
                chat_id=chat_id,
                chat_type=chat_type,
                file_markdown=file_markdown,
                file_provider=file_provider,
                status_emitter=create_reaction_status_emitter(settings, message_id),
            )
        
        # 发送回复
        payload = _build_send_payload(reply, card_enabled=bool(getattr(settings.reply, "card_enabled", True)))
        msg_type = str(payload.get("msg_type") or "text")
        if msg_type == "interactive":
            card_payload = payload.get("card")
            if isinstance(card_payload, dict):
                content = dict(card_payload)
            else:
                content = {}
        else:
            msg_type = "text"
            content_payload = payload.get("content")
            if isinstance(content_payload, dict):
                content = dict(content_payload)
            else:
                content = {"text": _pick_reply_text(reply)}
        await send_reply(chat_id, msg_type, content, message_id)
        record_inbound_message("ws", message_type, "processed")
            
    except Exception as e:
        logger.error(
            "长连接消息处理失败: %s",
            e,
            extra={"event_code": "ws.message.processing_error"},
            exc_info=True,
        )
        error_text = settings.reply.templates.error.format(message=str(e))
        await send_reply(chat_id, "text", {"text": error_text}, message_id)
        record_inbound_message("ws", message_type, "error")


def _pick_reply_text(reply: dict[str, Any]) -> str:
    outbound = reply.get("outbound") if isinstance(reply, dict) else None
    if isinstance(outbound, dict):
        raw_text = outbound.get("text_fallback")
        if isinstance(raw_text, str) and raw_text.strip():
            return raw_text
    return str(reply.get("text") or "")


def _build_send_payload(reply: dict[str, Any], card_enabled: bool = True) -> dict[str, Any]:
    text_fallback = _pick_reply_text(reply)
    outbound = reply.get("outbound") if isinstance(reply, dict) else None
    rendered = RenderedResponse.from_outbound(
        outbound if isinstance(outbound, dict) else None,
        fallback_text=text_fallback,
    )

    formatter = FeishuFormatter(card_enabled=card_enabled)
    try:
        return formatter.format(rendered)
    except Exception as exc:
        logger.warning(
            "格式化 outbound 失败，降级文本: %s",
            exc,
            extra={"event_code": "ws.reply.format_fallback"},
        )
        return {
            "msg_type": "text",
            "content": {"text": text_fallback},
        }


def _cancel_pending_chunk_flush(scope_key: str) -> None:
    task = _pending_chunk_flush_tasks.pop(scope_key, None)
    if task is not None and not task.done():
        task.cancel()


async def _flush_buffered_message_after_window(
    scope_key: str,
    user_id: str,
    chat_id: str,
    chat_type: str,
    message_id: str,
    message_type: str,
    attachments: list[Any],
) -> None:
    delay_seconds = max(float(settings.webhook.chunk_assembler.window_seconds), 0.1) + 0.05
    try:
        await asyncio.sleep(delay_seconds)
        decision = chunk_assembler.drain(scope_key)
        if not decision.should_process:
            return

        logger.info(
            "长连接分片窗口到期，执行自动冲刷处理",
            extra={
                "event_code": "ws.chunk_assembler.window_flush",
                "chat_id": chat_id,
                "user_id": user_id,
                "text_len": len(decision.text),
            },
        )
        record_inbound_message("ws", message_type, "accepted")
        await handle_message_async(
            user_id,
            chat_id,
            chat_type,
            decision.text,
            message_id,
            message_type=message_type,
            attachments=attachments,
        )
    except asyncio.CancelledError:
        return
    finally:
        task = _pending_chunk_flush_tasks.get(scope_key)
        if task is asyncio.current_task():
            _pending_chunk_flush_tasks.pop(scope_key, None)


async def send_reply(
    chat_id: str,
    msg_type: str,
    content: dict[str, Any],
    reply_message_id: str | None = None,
) -> None:
    """
    发送回复消息
    
    参数:
        chat_id: 会话 ID
        msg_type: 消息类型（text/interactive）
        content: 消息内容
        reply_message_id: 原消息 ID（可选，用于引用回复）
    """
    from src.utils.feishu_api import send_message
    
    await send_message(
        settings,
        chat_id,
        msg_type,
        content,
        reply_message_id=reply_message_id,
    )
# endregion


# region 事件分发器
def create_event_handler() -> lark.EventDispatcherHandler:
    """
    创建事件分发处理器
    
    功能:
        - 注册 im.message.receive_v1 事件监听
        - 配置解密 Key 与校验 Token
    """
    handler = lark.EventDispatcherHandler.builder(
        encrypt_key=settings.feishu.encrypt_key or "",
        verification_token=settings.feishu.verification_token or "",
    ).register_p2_im_message_receive_v1(on_message_receive).build()
    
    return handler


def on_message_receive(data: P2ImMessageReceiveV1) -> None:
    """
    处理接收到的消息事件
    
    参数:
        data: 飞书事件数据对象
    """
    try:
        event = FeishuEventAdapter.from_ws_event(data)
        if event is None:
            return

        message_id = event.message_id
        chat_id = event.chat_id
        message_type = event.message_type
        chat_type = event.chat_type
        
        # 仅处理私聊文本消息
        if chat_type != "p2p":
            logger.debug(
                "忽略非私聊消息: chat_type=%s",
                chat_type,
                extra={"event_code": "ws.message.ignored_non_p2p"},
            )
            record_inbound_message("ws", message_type, "ignored")
            return
        
        allow_file_pipeline = bool(settings.file_pipeline.enabled) and is_file_pipeline_message(message_type)
        if message_type not in settings.webhook.filter.allowed_message_types and not allow_file_pipeline:
            logger.debug(
                "忽略不支持消息类型: message_type=%s",
                message_type,
                extra={"event_code": "ws.message.ignored_type"},
            )
            record_inbound_message("ws", message_type, "ignored")
            return

        if settings.webhook.filter.ignore_bot_message and event.sender_type == "bot":
            record_inbound_message("ws", message_type, "ignored")
            return

        normalized = normalize_content(
            message_type=message_type,
            content=event.content,
            file_pipeline_enabled=bool(settings.file_pipeline.enabled),
            max_file_bytes=int(settings.file_pipeline.max_bytes),
            metrics_enabled=bool(getattr(settings.file_pipeline, "metrics_enabled", True)),
        )
        text = normalized.text
        if not text:
            record_inbound_message("ws", message_type, "ignored")
            return

        if event.sender_open_id:
            user_id = event.sender_open_id
        elif event.sender_user_id:
            user_id = f"user:{event.sender_user_id}"
        elif chat_id and message_id:
            user_id = f"chat:{chat_id}:msg:{message_id}"
        elif chat_id:
            user_id = f"chat:{chat_id}:anon"
        else:
            user_id = "unknown"
        
        scoped_user_id = build_session_key(
            user_id=user_id,
            chat_id=chat_id,
            chat_type=chat_type,
            channel_type="feishu",
        )
        chunk_decision = chunk_assembler.ingest(scope_key=scoped_user_id, text=text)
        if not chunk_decision.should_process:
            logger.info(
                "长连接消息分片已缓存，等待聚合窗口",
                extra={
                    "event_code": "ws.chunk_assembler.buffering",
                    "chat_id": chat_id,
                    "user_id": scoped_user_id,
                },
            )
            record_inbound_message("ws", message_type, "buffered")
            if scoped_user_id not in _pending_chunk_flush_tasks:
                _pending_chunk_flush_tasks[scoped_user_id] = asyncio.create_task(
                    _flush_buffered_message_after_window(
                        scope_key=scoped_user_id,
                        user_id=scoped_user_id,
                        chat_id=chat_id,
                        chat_type=chat_type,
                        message_id=message_id,
                        message_type=normalized.message_type,
                        attachments=normalized.attachments,
                    )
                )
            return
        _cancel_pending_chunk_flush(scoped_user_id)
        text = chunk_decision.text

        logger.info(
            "收到长连接消息",
            extra={
                "event_code": "ws.message.received",
                "user_id": scoped_user_id,
                "chat_id": chat_id,
                "text": text[:50],
            },
        )
        record_inbound_message("ws", message_type, "accepted")
        
        # 异步处理消息
        asyncio.create_task(
            handle_message_async(
                scoped_user_id,
                chat_id,
                chat_type,
                text,
                message_id,
                message_type=normalized.message_type,
                attachments=normalized.attachments,
            )
        )
        
    except Exception as e:
        logger.error(
            "处理长连接事件失败: %s",
            e,
            extra={"event_code": "ws.event.handle_error"},
            exc_info=True,
        )


# endregion


# region WebSocket 客户端
def create_ws_client() -> lark.ws.Client:
    """
    创建 WebSocket 长连接客户端
    
    返回:
        lark.ws.Client: 客户端实例
    """
    event_handler = create_event_handler()
    
    client = lark.ws.Client(
        app_id=settings.feishu.app_id,
        app_secret=settings.feishu.app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG if settings.logging.level == "DEBUG" else lark.LogLevel.INFO,
    )
    
    return client


def start_ws_client() -> None:
    """
    启动 WebSocket 长连接客户端（阻塞运行）
    """
    logger.info("启动飞书长连接客户端", extra={"event_code": "ws.client.start"})
    logger.info(
        "当前应用 ID 前缀: %s...",
        settings.feishu.app_id[:8],
        extra={"event_code": "ws.client.app_id"},
    )
    
    client = create_ws_client()
    
    try:
        client.start()
    except KeyboardInterrupt:
        logger.info("用户主动停止长连接客户端", extra={"event_code": "ws.client.stopped"})
    except Exception as e:
        logger.error(
            "长连接客户端异常: %s",
            e,
            extra={"event_code": "ws.client.error"},
            exc_info=True,
        )
        raise
# endregion


# region 程序入口
if __name__ == "__main__":
    start_ws_client()
# endregion
