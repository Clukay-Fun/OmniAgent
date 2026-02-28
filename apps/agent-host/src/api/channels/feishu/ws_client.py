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
from pathlib import Path as _Path

# 必须在导入 config 之前加载 .env
# run_dev.py agent-ws 会把 cwd 设为 apps/agent-host/，
# 而 .env 在仓库根目录，因此需要显式向上搜索。
# ws_client.py 位于 src/api/channels/feishu/ws_client.py
# parents: [0]=feishu/ [1]=channels/ [2]=api/ [3]=src/ [4]=agent-host/
_agent_host_root = _Path(__file__).resolve().parents[4]  # apps/agent-host/
_repo_root = _agent_host_root.parent.parent               # 仓库根目录
for _env_path in [_agent_host_root / ".env", _repo_root / ".env"]:
    if _env_path.is_file():
        load_dotenv(_env_path, override=False)
load_dotenv()  # 兜底：加载 cwd 下的 .env（如果有）

# Windows 兼容性：设置事件循环策略
# lark_oapi 在导入时会创建事件循环，需要先设置策略
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from src.adapters.channels.feishu.protocol.event_adapter import FeishuEventAdapter
from src.adapters.channels.feishu.protocol.formatter import FeishuFormatter
from src.adapters.channels.feishu.actions.processing_status import create_reaction_status_emitter
from src.adapters.channels.feishu.skills.bitable_writer import BitableWriter
from src.api.inbound.chunk_assembler import ChunkAssembler
from src.api.inbound.conversation_scope import build_session_key
from src.api.inbound.file_pipeline import (
    build_ocr_completion_text,
    build_file_unavailable_guidance,
    build_processing_status_text,
    is_file_pipeline_message,
    resolve_file_markdown,
)
from src.api.core.inbound_normalizer import normalize_content
from src.config import get_settings
from src.core.brain.orchestration.orchestrator import AgentOrchestrator
from src.core.expression.response.models import RenderedResponse
from src.core.runtime.state.session import SessionManager
from src.infra.llm.provider import create_llm_client
from src.infra.mcp.client import MCPClient
from src.utils.observability.metrics import record_inbound_message
from src.utils.observability.logger import setup_logging

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
    state_manager=agent_core._state_manager,
    window_seconds=float(settings.webhook.chunk_assembler.window_seconds),
    stale_window_seconds=float(settings.webhook.chunk_assembler.stale_window_seconds),
    max_segments=int(settings.webhook.chunk_assembler.max_segments),
    max_chars=int(settings.webhook.chunk_assembler.max_chars),
)
_pending_chunk_flush_tasks: dict[str, asyncio.Task[Any]] = {}


def _flush_orphan_chunks(session_key: str) -> None:
    async def _drain() -> None:
        decision = await chunk_assembler.drain(session_key)
        if decision.should_process:
            logger.warning(
                "检测到会话过期残留分片，已执行兜底冲刷",
                extra={
                    "event_code": "ws.chunk_assembler.orphan_flushed",
                    "session_key": session_key,
                    "text_len": len(decision.text),
                },
            )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_drain())
    except RuntimeError:
        asyncio.run(_drain())


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
        file_reason = ""
        first_attachment = attachments[0] if attachments else None
        direct_reply_text = ""
        ocr_completion_text = ""

        if (
            message_type in {"image", "audio"}
            and attachments
            and bool(getattr(attachments[0], "accepted", False))
        ):
            try:
                await send_reply(
                    chat_id,
                    "text",
                    {"text": build_processing_status_text(message_type)},
                    message_id,
                )
            except Exception as exc:
                logger.warning(
                    "发送长连接多模态处理中提示失败: %s",
                    exc,
                    extra={"event_code": "ws.reply.media_status_failed"},
                )

        if attachments:
            resolved = await resolve_file_markdown(
                attachments=attachments,
                settings=settings,
                message_type=message_type,
            )
            if isinstance(resolved, tuple):
                file_markdown = str(resolved[0] or "") if len(resolved) > 0 else ""
                guidance = str(resolved[1] or "") if len(resolved) > 1 else ""
                file_provider = str(resolved[2] or "none") if len(resolved) > 2 else "none"
                file_reason = str(resolved[3] or "") if len(resolved) > 3 else ""
            else:
                file_markdown, guidance, file_provider, file_reason = "", "", "none", ""
            if not file_reason and first_attachment is not None:
                file_reason = str(getattr(first_attachment, "reject_reason", "") or "").strip()
            if guidance and not file_markdown and message_type in {"file", "audio", "image"}:
                direct_reply_text = guidance
            elif message_type == "audio" and file_markdown:
                text = file_markdown
                file_markdown = ""
            elif message_type == "image" and file_markdown:
                ocr_completion_text = build_ocr_completion_text(file_markdown)
        elif message_type in {"file", "audio", "image"}:
            reason = "extractor_disabled"
            if message_type == "audio":
                reason = "asr_unconfigured"
            elif message_type == "image":
                reason = "ocr_unconfigured"
            guidance = build_file_unavailable_guidance(reason)
            file_reason = reason
            direct_reply_text = guidance

        # 调用 Agent 处理
        if direct_reply_text:
            if message_type in {"file", "audio", "image"}:
                reply = _build_upload_result_reply(
                    guidance_text=direct_reply_text,
                    message_type=message_type,
                    provider=file_provider,
                    reason_code=file_reason,
                    attachment=first_attachment,
                )
            else:
                reply = {"type": "text", "text": direct_reply_text}
        elif message_type in {"file", "audio", "image"} and text.startswith("已收到文件"):
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
            if ocr_completion_text:
                reply = _prepend_reply_text(reply, ocr_completion_text)
        
        # 发送回复
        outbound = reply.get("outbound") if isinstance(reply, dict) else None
        prefer_card = False
        payload = _build_send_payload(
            reply,
            card_enabled=False,
            prefer_card=prefer_card,
        )
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


def _upload_status_from_reason(reason_code: str) -> str:
    code = str(reason_code or "").strip().lower()
    if not code:
        return "failed"
    if code in {"file_too_large", "unsupported_file_type"}:
        return "rejected"
    if code in {"extractor_disabled", "ocr_disabled", "asr_disabled"}:
        return "disabled"
    if code in {"extractor_unconfigured", "ocr_unconfigured", "asr_unconfigured"}:
        return "unconfigured"
    return "failed"


def _upload_status_text(status: str) -> tuple[str, str, str]:
    normalized = str(status or "").strip().lower()
    if normalized == "rejected":
        return "⚠️", "不符合要求", "⚠️ 文件不符合要求"
    if normalized == "disabled":
        return "⚠️", "未开启", "⚠️ 文件解析未开启"
    if normalized == "unconfigured":
        return "⚠️", "未配置", "⚠️ 文件解析未配置"
    if normalized == "success":
        return "✅", "已完成", "✅ 文件解析成功"
    return "❌", "失败", "❌ 文件解析失败"


def _upload_provider_label(provider: str) -> str:
    labels = {
        "none": "未使用外部解析",
        "mineru": "MinerU",
        "llm": "LLM Extractor",
        "ocr": "OCR Provider",
        "asr": "ASR Provider",
    }
    key = str(provider or "none").strip().lower()
    return labels.get(key, key or "none")


def _upload_message_type_label(message_type: str) -> str:
    labels = {
        "file": "文件",
        "image": "图片",
        "audio": "语音",
    }
    key = str(message_type or "").strip().lower()
    return labels.get(key, "文件")


def _upload_reason_text(reason_code: str) -> str:
    key = str(reason_code or "").strip().lower()
    if key.endswith("_fail_open"):
        key = key[: -len("_fail_open")]
    reason_labels = {
        "file_too_large": "文件体积超过当前限制",
        "unsupported_file_type": "文件类型暂不支持",
        "extractor_disabled": "解析能力已关闭",
        "extractor_unconfigured": "解析服务尚未配置",
        "ocr_unconfigured": "OCR 服务尚未配置",
        "ocr_disabled": "OCR 服务已关闭",
        "asr_unconfigured": "ASR 服务尚未配置",
        "asr_disabled": "ASR 服务已关闭",
        "extractor_timeout": "解析服务响应超时",
        "extractor_rate_limited": "解析服务限流",
        "extractor_auth_failed": "解析服务鉴权失败",
        "extractor_malformed_response": "解析服务响应格式异常",
        "extractor_empty_content": "未识别到有效内容",
        "ocr_empty_text": "未识别到有效图片文字",
        "asr_empty_transcript": "未识别到有效语音文本",
        "extractor_provider_error": "解析服务异常",
        "extractor_network_error": "解析服务网络异常",
        "extractor_connect_failed": "解析服务连接失败",
        "cost_circuit_breaker_open": "预算达到当日阈值",
    }
    return reason_labels.get(key, "")


def _format_file_size(file_size: Any) -> str:
    try:
        size = int(file_size)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _build_upload_result_text(
    *,
    guidance_text: str,
    message_type: str,
    provider: str,
    reason_code: str,
    file_name: str,
    file_type: str,
    file_size: Any,
) -> str:
    status = _upload_status_from_reason(reason_code)
    icon, status_label, title = _upload_status_text(status)
    message_label = _upload_message_type_label(message_type)
    provider_label = _upload_provider_label(provider)
    reason_text = _upload_reason_text(reason_code)
    size_text = _format_file_size(file_size)

    lines = [
        title,
        f"- 状态: {icon} {status_label}",
        f"- 来源类型: {message_label}",
        f"- 解析通道: {provider_label}",
    ]
    if file_name:
        lines.append(f"- 文件: {file_name}")
    if file_type:
        lines.append(f"- 类型: {file_type}")
    if size_text:
        lines.append(f"- 大小: {size_text}")
    if reason_text:
        lines.append(f"- 原因: {reason_text}")
    if guidance_text:
        lines.append(f"- 说明: {guidance_text}")

    return "\n".join(lines)


def _build_upload_result_reply(
    *,
    guidance_text: str,
    message_type: str,
    provider: str,
    reason_code: str,
    attachment: Any,
) -> dict[str, Any]:
    file_name = str(getattr(attachment, "file_name", "") or "").strip()
    file_type = str(getattr(attachment, "file_type", "") or "").strip()
    file_size = getattr(attachment, "file_size", None)
    result_text = _build_upload_result_text(
        guidance_text=guidance_text,
        message_type=message_type,
        provider=provider,
        reason_code=reason_code,
        file_name=file_name,
        file_type=file_type,
        file_size=file_size,
    )
    return {
        "type": "text",
        "text": result_text,
        "outbound": {
            "text_fallback": result_text,
            "meta": {
                "skill_name": "UploadPipeline",
                "source": "file_pipeline",
            },
        },
    }


def _build_send_payload(reply: dict[str, Any], card_enabled: bool = False, *, prefer_card: bool = False) -> dict[str, Any]:
    text_fallback = _pick_reply_text(reply)
    outbound = reply.get("outbound") if isinstance(reply, dict) else None
    rendered = RenderedResponse.from_outbound(
        outbound if isinstance(outbound, dict) else None,
        fallback_text=text_fallback,
    )

    formatter = FeishuFormatter(card_enabled=card_enabled)
    try:
        return formatter.format(rendered, prefer_card=prefer_card)
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


def _prepend_reply_text(reply: dict[str, Any], prefix: str) -> dict[str, Any]:
    text_prefix = str(prefix or "").strip()
    if not text_prefix:
        return reply
    merged = dict(reply)
    old_text = str(merged.get("text") or "").strip()
    merged["text"] = text_prefix if not old_text else f"{text_prefix}\n\n{old_text}"
    outbound = merged.get("outbound")
    if isinstance(outbound, dict):
        text_fallback = str(outbound.get("text_fallback") or "").strip()
        outbound["text_fallback"] = text_prefix if not text_fallback else f"{text_prefix}\n\n{text_fallback}"
        blocks = outbound.get("blocks")
        if isinstance(blocks, list):
            blocks.insert(0, {"type": "paragraph", "content": {"text": text_prefix}})
    return merged


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
        decision = await chunk_assembler.drain(scope_key)
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
    from src.utils.platform.feishu.feishu_api import send_message
    
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
        - 注册 bot 进群/加好友等事件的兜底处理器
        - 配置解密 Key 与校验 Token
    """
    handler = (
        lark.EventDispatcherHandler.builder(
            encrypt_key=settings.feishu.encrypt_key or "",
            verification_token=settings.feishu.verification_token or "",
        )
        .register_p2_im_message_receive_v1(on_message_receive)
        .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(
            _noop_event_handler("im.chat.access_event.bot_p2p_chat_entered_v1")
        )
        .register_p2_im_chat_member_bot_added_v1(
            _noop_event_handler("im.chat.member.bot.added_v1")
        )
        .build()
    )

    return handler


def _noop_event_handler(event_type: str):
    def _handler(_data: Any) -> None:
        try:
            logger.debug(
                "收到无需处理的 WS 事件: %s",
                event_type,
                extra={
                    "event_code": "ws.event.noop",
                    "event_type": event_type,
                },
            )
        except Exception:
            return

    return _handler


async def _handle_message_receive_event(event: Any) -> None:
    """异步处理标准化后的 WS 消息事件。"""
    try:
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
        chunk_decision = await chunk_assembler.ingest(scope_key=scoped_user_id, text=text)
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

        await handle_message_async(
            scoped_user_id,
            chat_id,
            chat_type,
            text,
            message_id,
            message_type=normalized.message_type,
            attachments=normalized.attachments,
        )

    except Exception as e:
        logger.error(
            "处理长连接事件失败: %s",
            e,
            extra={"event_code": "ws.event.handle_error"},
            exc_info=True,
        )


def on_message_receive(data: P2ImMessageReceiveV1) -> None:
    """
    处理接收到的消息事件
    
    参数:
        data: 飞书事件数据对象
    """
    event = FeishuEventAdapter.from_ws_event(data)
    if event is None:
        return

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_handle_message_receive_event(event))
    except RuntimeError:
        asyncio.run(_handle_message_receive_event(event))


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
