"""
描述: Feishu Webhook 事件处理器
主要功能:
    - 接收并解密飞书回调事件
    - 处理 URL 验证请求 (Challenge)
    - 消息去重与过滤
    - 异步分发消息至 Agent 核心
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import time
import logging
from typing import Any, cast

from Crypto.Cipher import AES
from fastapi import APIRouter, HTTPException, Request

from src.adapters.channels.feishu.event_adapter import FeishuEventAdapter, MessageEvent
from src.adapters.channels.feishu.processing_status import create_reaction_status_emitter
from src.adapters.channels.feishu.skills.bitable_writer import BitableWriter
from src.api.chunk_assembler import ChunkAssembler
from src.api.conversation_scope import build_session_key
from src.api.file_pipeline import (
    build_ocr_completion_text,
    build_processing_status_text,
    build_file_unavailable_guidance,
    is_file_pipeline_message,
    resolve_file_markdown,
)
from src.adapters.channels.feishu.formatter import FeishuFormatter
from src.api.automation_consumer import QueueAutomationEnqueuer, create_default_automation_enqueuer
from src.api.event_router import FeishuEventRouter, get_enabled_types
from src.api.inbound_normalizer import normalize_content
from src.core.orchestrator import AgentOrchestrator
from src.core.batch_progress import BatchProgressEmitter, BatchProgressEvent, BatchProgressPhase
from src.core.errors import (
    CallbackDuplicatedError,
    PendingActionExpiredError,
    get_user_message as get_core_user_message,
)
from src.core.response.models import RenderedResponse
from src.core.skills.schema_cache import get_global_schema_cache
from src.core.session import SessionManager
from src.config import get_settings
from src.llm.provider import create_llm_client
from src.mcp.client import MCPClient
from src.utils.feishu_api import send_message, update_message
from src.utils.metrics import record_inbound_message
from src.api.callback_deduper import CallbackDeduper


router = APIRouter()
logger = logging.getLogger(__name__)
CALLBACK_DEDUP_WINDOW_SECONDS = 600


# ============================================
# region 延迟初始化（Lazy Initialization）
# ============================================
_settings: Any = None
_session_manager: Any = None
_mcp_client: Any = None
_llm_client: Any = None
_agent_core: AgentOrchestrator | None = None
_deduplicator: "EventDeduplicator | None" = None
_event_router: FeishuEventRouter | None = None
_automation_enqueuer: QueueAutomationEnqueuer | None = None
_chunk_assembler: ChunkAssembler | None = None
_chunk_expire_hook_bound: bool = False
_user_manager: Any = None  # 用户管理器
_schema_sync_bridge: Any = None
_reminder_refresh_bridge: Any = None
_callback_deduper: CallbackDeduper | None = None


class _SchemaSyncBridge:
    def __init__(self) -> None:
        self.schema_cache = get_global_schema_cache()


class _ReminderRefreshBridge:
    def enqueue_calendar_changed(self, **payload: Any) -> bool:
        logger.info(
            "calendar reminder refresh enqueued",
            extra={
                "event_code": "webhook.reminder_refresh.enqueued",
                "event_id": str(payload.get("event_id") or ""),
                "calendar_id": str(payload.get("calendar_id") or ""),
                "calendar_event_id": str(payload.get("calendar_event_id") or ""),
            },
        )
        return True


def _get_settings() -> Any:
    """延迟获取配置"""
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings


def _get_agent_core() -> AgentOrchestrator:
    """延迟初始化 Agent 编排器"""
    global _agent_core, _session_manager, _mcp_client, _llm_client
    if _agent_core is None:
        settings = _get_settings()
        _session_manager = SessionManager(settings.session)
        _mcp_client = MCPClient(settings)
        _llm_client = create_llm_client(settings.llm)
        _agent_core = AgentOrchestrator(
            settings=settings,
            session_manager=_session_manager,
            mcp_client=_mcp_client,
            llm_client=_llm_client,
            skills_config_path="config/skills.yaml",
            data_writer=BitableWriter(_mcp_client),
        )
        _bind_chunk_expire_hook()
        logger.info("Agent 编排器初始化完成", extra={"event_code": "webhook.agent_core.initialized"})
    return _agent_core


def _get_deduplicator() -> "EventDeduplicator":
    """延迟初始化去重器"""
    global _deduplicator
    if _deduplicator is None:
        settings = _get_settings()
        _deduplicator = EventDeduplicator(
            settings.webhook.dedup.ttl_seconds,
            settings.webhook.dedup.max_size,
        )
    return _deduplicator


def _get_callback_deduper() -> CallbackDeduper:
    """延迟初始化 callback 语义去重器"""
    global _callback_deduper
    if _callback_deduper is None:
        _callback_deduper = CallbackDeduper(window_seconds=CALLBACK_DEDUP_WINDOW_SECONDS)
    return _callback_deduper


def _get_event_router() -> FeishuEventRouter:
    """延迟初始化事件路由器。"""
    global _event_router
    if _event_router is None:
        settings = _get_settings()
        _event_router = FeishuEventRouter(
            enabled_types=get_enabled_types(settings),
            automation_enqueuer=_get_automation_enqueuer(),
            schema_sync=_get_schema_sync_bridge(),
            reminder_engine=_get_reminder_refresh_bridge(),
        )
    return _event_router


def _get_schema_sync_bridge() -> Any:
    global _schema_sync_bridge
    if _schema_sync_bridge is None:
        _schema_sync_bridge = _SchemaSyncBridge()
    return _schema_sync_bridge


def _get_automation_enqueuer() -> QueueAutomationEnqueuer:
    global _automation_enqueuer
    if _automation_enqueuer is None:
        _automation_enqueuer = create_default_automation_enqueuer()
    return _automation_enqueuer


def _get_reminder_refresh_bridge() -> Any:
    global _reminder_refresh_bridge
    if _reminder_refresh_bridge is None:
        _reminder_refresh_bridge = _ReminderRefreshBridge()
    return _reminder_refresh_bridge


def _get_chunk_assembler() -> ChunkAssembler:
    """延迟初始化分片聚合器。"""
    global _chunk_assembler
    if _chunk_assembler is None:
        settings = _get_settings()
        cfg = settings.webhook.chunk_assembler
        core = _get_agent_core()
        _chunk_assembler = ChunkAssembler(
            enabled=cfg.enabled,
            state_manager=core._state_manager,
            window_seconds=cfg.window_seconds,
            stale_window_seconds=cfg.stale_window_seconds,
            max_segments=cfg.max_segments,
            max_chars=cfg.max_chars,
        )
        _bind_chunk_expire_hook()
    return _chunk_assembler


def _bind_chunk_expire_hook() -> None:
    """将会话过期清理与分片兜底冲刷绑定。"""
    global _chunk_expire_hook_bound
    if _chunk_expire_hook_bound:
        return
    if _session_manager is None or _chunk_assembler is None:
        return
    assembler = _chunk_assembler

    async def _drain_expired_chunk(session_key: str) -> None:
        decision = await assembler.drain(session_key)
        if decision.should_process:
            logger.warning(
                "检测到会话过期残留分片，已执行兜底冲刷",
                extra={
                    "event_code": "webhook.chunk_assembler.orphan_flushed",
                    "session_key": session_key,
                    "text_len": len(decision.text),
                },
            )

    def _on_session_expired(session_key: str) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_drain_expired_chunk(session_key))
        except RuntimeError:
            asyncio.run(_drain_expired_chunk(session_key))

    _session_manager.register_expire_listener(_on_session_expired)
    _chunk_expire_hook_bound = True


def _get_user_manager():
    """延迟初始化用户管理器"""
    global _user_manager, _mcp_client
    if _user_manager is None:
        try:
            logger.info("开始初始化用户管理器", extra={"event_code": "webhook.user_manager.init_start"})
            from src.user.manager import UserManager
            from src.user.matcher import UserMatcher
            from src.user.cache import UserCache
            
            settings = _get_settings()
            
            # 确保 MCP 客户端已初始化
            if _mcp_client is None:
                _mcp_client = MCPClient(settings)

            # 加载 skills 配置（用于 table_identity_fields 查询）
            from src.core.intent import load_skills_config
            skills_config = load_skills_config("config/skills.yaml")
            
            # 创建匹配器
            matcher = UserMatcher(
                mcp_client=_mcp_client,
                match_field=settings.user.identity.match_field,
                min_confidence=settings.user.identity.min_confidence,
            )
            
            # 创建缓存
            cache = UserCache(
                ttl_hours=settings.user.cache.ttl_hours,
                max_size=settings.user.cache.max_size,
            )
            
            # 创建用户管理器（注入 skills_config 以支持按表字段查询）
            _user_manager = UserManager(
                settings=settings,
                matcher=matcher,
                cache=cache,
                skills_config=skills_config,
            )
            
            logger.info("用户管理器初始化完成", extra={"event_code": "webhook.user_manager.init_success"})
        except Exception as e:
            logger.error(
                "初始化用户管理器失败: %s",
                e,
                extra={"event_code": "webhook.user_manager.init_failed"},
                exc_info=True,
            )
            raise
    
    return _user_manager


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
    status = _upload_status_from_reason(reason_code)
    return {
        "type": "text",
        "text": guidance_text,
        "outbound": {
            "text_fallback": guidance_text,
            "card_template": {
                "template_id": "upload.result",
                "version": "v1",
                "params": {
                    "status": status,
                    "guidance": guidance_text,
                    "reason_code": reason_code,
                    "provider": provider,
                    "message_type": message_type,
                    "file_name": file_name,
                    "file_type": file_type,
                    "file_size": file_size,
                },
            },
            "meta": {
                "skill_name": "UploadPipeline",
                "source": "file_pipeline",
            },
        },
    }


def _build_send_payload(reply: dict[str, Any], card_enabled: bool = True, *, prefer_card: bool = False) -> dict[str, Any]:
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
            extra={"event_code": "webhook.reply.format_fallback"},
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


# 公开访问器（供 main.py 等外部模块使用）
class _AgentCoreProxy:
    """Agent Core 代理对象，延迟初始化"""
    def __getattr__(self, name):
        return getattr(_get_agent_core(), name)
    
    def reload_config(self, config_path: str):
        """重新加载配置"""
        core = _get_agent_core()
        if hasattr(core, 'reload_config'):
            core.reload_config(config_path)

    def reload_skill_metadata(self) -> dict[str, Any]:
        """手动重载 Skill 元数据。"""
        core = _get_agent_core()
        if hasattr(core, "reload_skill_metadata"):
            return core.reload_skill_metadata()
        return {
            "loaded": [],
            "failed": [
                {
                    "skill_name": "*",
                    "file_path": "",
                    "error": "reload_skill_metadata not supported",
                }
            ],
            "loaded_count": 0,
            "failed_count": 1,
        }


agent_core = _AgentCoreProxy()
# endregion
# ============================================


def _verify_reload_request(request: Request) -> None:
    """校验 /reload 运维请求。"""
    settings = _get_settings()
    expected_token = str(getattr(settings.feishu, "verification_token", "") or "").strip()
    if not expected_token:
        raise HTTPException(status_code=503, detail="reload auth is not configured")

    provided_token = str(request.headers.get("x-reload-token") or "").strip()
    if not provided_token or not hmac.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=401, detail="invalid reload token")


# region 辅助类与函数
class EventDeduplicator:
    """
    事件去重器

    功能:
        - 基于 LRU 缓存防止重复处理同一 Event ID
        - 自动清理过期记录
    """
    def __init__(self, ttl_seconds: int, max_size: int) -> None:
        """
        初始化去重器

        参数:
            ttl_seconds: 记录保留时间
            max_size: 最大缓存条目数
        """
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._items: dict[str, float] = {}

    def _cleanup(self) -> None:
        """清理过期记录"""
        now = time.time()
        self._items = {
            key: ts for key, ts in self._items.items() if now - ts <= self._ttl
        }

    def is_duplicate(self, key: str) -> bool:
        """仅检查 Key 是否已存在（不写入）"""
        self._cleanup()
        return key in self._items

    def mark(self, key: str) -> None:
        """标记 Key 为已处理"""
        self._cleanup()
        if key in self._items:
            self._items[key] = time.time()
            return
        if len(self._items) >= self._max_size:
            self._items.pop(next(iter(self._items)))
        self._items[key] = time.time()

    def remove(self, key: str) -> None:
        """移除 Key（处理失败时允许重试）"""
        self._items.pop(key, None)


# 去重器已改为延迟初始化，见 _get_deduplicator()


def _decrypt_event(encrypt_text: str, encrypt_key: str) -> dict[str, Any]:
    """
    解密飞书回调数据

    参数:
        encrypt_text: 加密密文
        encrypt_key: 解密密钥 (AES Key)
    """
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
    """判断是否为私聊消息"""
    return message.get("chat_type") == "p2p"


def _get_text_content(message: dict[str, Any]) -> str:
    """提取纯文本消息内容"""
    content = message.get("content")
    if not content:
        return ""
    try:
        payload = json.loads(content)
        return payload.get("text") or ""
    except json.JSONDecodeError:
        return ""


def _extract_card_action_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    event_raw = payload.get("event")
    event = event_raw if isinstance(event_raw, dict) else {}
    action_raw = event.get("action")
    action = action_raw if isinstance(action_raw, dict) else {}
    value_raw = action.get("value")
    value = value_raw if isinstance(value_raw, dict) else {}
    callback_action = str(value.get("callback_action") or "").strip()
    if not callback_action:
        callback_action = str(action.get("name") or "").strip()
    if not callback_action:
        return None

    operator_raw = event.get("operator")
    operator = operator_raw if isinstance(operator_raw, dict) else {}
    op_open_id = ""
    operator_id_raw = operator.get("operator_id")
    operator_id = operator_id_raw if isinstance(operator_id_raw, dict) else {}
    if operator_id:
        op_open_id = str(operator_id.get("open_id") or "").strip()
    if not op_open_id:
        op_open_id = str(operator.get("open_id") or "").strip()

    open_chat_id = str(event.get("open_chat_id") or "").strip()
    chat_type = str(event.get("chat_type") or "").strip().lower()
    message_id = str(
        event.get("open_message_id")
        or event.get("message_id")
        or action.get("open_message_id")
        or action.get("message_id")
        or ""
    ).strip()

    return {
        "callback_action": callback_action,
        "event_id": str((payload.get("header") or {}).get("event_id") or payload.get("event_id") or "").strip(),
        "open_id": op_open_id,
        "chat_id": open_chat_id,
        "chat_type": chat_type,
        "message_id": message_id,
        "value": value,
    }


def _extract_feishu_payload_content(payload: dict[str, Any]) -> tuple[str, dict[str, object]]:
    msg_type = str(payload.get("msg_type") or "text")
    if msg_type == "interactive":
        card_raw = payload.get("card")
        card = card_raw if isinstance(card_raw, dict) else {}
        return "interactive", cast(dict[str, object], card)
    content_raw = payload.get("content")
    content = content_raw if isinstance(content_raw, dict) else {"text": str(content_raw or "")}
    return "text", cast(dict[str, object], content)


async def _emit_callback_result_message(callback_payload: dict[str, Any], result: dict[str, Any]) -> None:
    chat_id = str(callback_payload.get("chat_id") or "").strip()
    if not chat_id:
        return

    outbound_raw = result.get("outbound")
    outbound = outbound_raw if isinstance(outbound_raw, dict) else None
    if outbound is None:
        return

    text = str(result.get("text") or "").strip()

    reply: dict[str, Any] = {
        "type": "text",
        "text": text or "已处理",
    }
    reply["outbound"] = outbound

    settings = _get_settings()
    prefer_card = bool(
        outbound
        and isinstance(outbound.get("blocks"), list)
        and outbound.get("blocks")
        and not isinstance(outbound.get("card_template"), dict)
    )
    if outbound and isinstance(outbound.get("card_template"), dict):
        prefer_card = True

    payload = _build_send_payload(
        reply,
        card_enabled=bool(getattr(settings.reply, "card_enabled", True)),
        prefer_card=prefer_card,
    )
    msg_type, content = _extract_feishu_payload_content(payload)
    message_id = str(callback_payload.get("message_id") or "").strip()

    if message_id:
        try:
            await update_message(settings=settings, message_id=message_id, msg_type=msg_type, content=content)
            return
        except Exception as exc:
            logger.warning(
                "更新回调原卡片失败，回退为发送新消息: %s",
                exc,
                extra={
                    "event_code": "webhook.callback.update_failed",
                    "message_id": message_id,
                    "chat_id": chat_id,
                    "msg_type": msg_type,
                },
            )

    await send_message(settings, chat_id, msg_type, content)


def _build_batch_progress_emitter(callback_payload: dict[str, Any]) -> BatchProgressEmitter | None:
    chat_id = str(callback_payload.get("chat_id") or "").strip()
    if not chat_id:
        return None

    message_id = str(callback_payload.get("message_id") or "").strip() or None
    settings = _get_settings()

    async def _emit(event: BatchProgressEvent) -> None:
        if event.phase != BatchProgressPhase.START:
            return
        total = max(0, int(event.total or 0))
        if total < 3:
            return
        await send_message(
            settings,
            chat_id,
            "text",
            {"text": f"🔄 正在执行 {total} 条操作..."},
            reply_message_id=message_id,
        )

    return _emit


async def _call_card_action_callback(
    *,
    core: Any,
    user_id: str,
    callback_action: str,
    callback_value: dict[str, Any] | None,
    batch_progress_emitter: BatchProgressEmitter | None,
) -> dict[str, Any]:
    try:
        return await core.handle_card_action_callback(
            user_id=user_id,
            callback_action=callback_action,
            callback_value=callback_value,
            batch_progress_emitter=batch_progress_emitter,
        )
    except TypeError as exc:
        text = str(exc)
        if "batch_progress_emitter" not in text:
            raise
        return await core.handle_card_action_callback(
            user_id=user_id,
            callback_action=callback_action,
            callback_value=callback_value,
        )
# endregion


# region Webhook 路由处理


@router.post("/reload")
async def reload_skill_metadata(request: Request) -> dict[str, Any]:
    """手动重载 SKILL.md 元数据缓存。"""
    _verify_reload_request(request)
    try:
        result = agent_core.reload_skill_metadata()
    except Exception as exc:
        logger.exception(
            "skill metadata reload failed",
            extra={"event_code": "webhook.reload.skill_metadata.failed"},
        )
        raise HTTPException(status_code=500, detail=f"skill metadata reload failed: {exc}") from exc

    return {
        "status": "ok",
        "scope": "skill_metadata",
        "result": result,
    }


@router.get("/feishu/webhook")
async def feishu_webhook_get() -> dict[str, str]:
    """健康检查接口"""
    return {"status": "ok"}


@router.post("/feishu/webhook")
async def feishu_webhook(request: Request) -> dict[str, str]:
    """
    接收飞书事件回调

    流程:
        1. 处理 URL 验证 (Challenge)
        2. 解密事件内容
        3. 校验 Token
        4. 去重与过滤
        5. 处理消息并回复
    """
    logger.info("收到飞书 webhook 请求", extra={"event_code": "webhook.request.received"})
    payload = await request.json()
    logger.info(
        "Webhook 载荷信息",
        extra={
            "event_code": "webhook.request.payload",
            "payload_type": payload.get("type"),
            "event_type": payload.get("header", {}).get("event_type"),
        },
    )

    if payload.get("type") == "url_verification":
        logger.info("处理 URL 验证请求", extra={"event_code": "webhook.url_verification"})
        return {"challenge": payload.get("challenge", "")}

    settings = _get_settings()
    if payload.get("encrypt"):
        logger.info("开始解密 webhook 载荷", extra={"event_code": "webhook.payload.decrypt_start"})
        if not settings.feishu.encrypt_key:
            raise HTTPException(status_code=400, detail="encrypt_key is required")
        payload = _decrypt_event(payload["encrypt"], settings.feishu.encrypt_key)

    callback_payload = _extract_card_action_payload(payload)
    if callback_payload is not None:
        expired_text = get_core_user_message(PendingActionExpiredError())
        duplicated_text = get_core_user_message(CallbackDuplicatedError())
        event_id = str(callback_payload.get("event_id") or "")
        if event_id and settings.webhook.dedup.enabled and _get_deduplicator().is_duplicate(event_id):
            return {"status": "ok", "reason": duplicated_text}
        # S4: 语义级 callback 去重（user_id + action + payload hash）
        open_id = str(callback_payload.get("open_id") or "").strip()
        if not open_id:
            return {"status": "ok", "reason": expired_text}
        cb_action = str(callback_payload.get("callback_action") or "").strip()
        if not cb_action:
            return {"status": "ok", "reason": expired_text}
        cb_deduper = _get_callback_deduper()
        cb_dedup_key = cb_deduper.build_key(
            user_id=open_id,
            action=cb_action,
            payload=callback_payload.get("value") if isinstance(callback_payload.get("value"), dict) else None,
        )
        if not cb_deduper.try_acquire(cb_dedup_key):
            logger.info(
                "重复 callback 已短路",
                extra={
                    "event_code": "callback.duplicated",
                    "open_id": open_id,
                    "callback_action": cb_action,
                },
            )
            return {"status": "ok", "reason": duplicated_text}
        chat_id = str(callback_payload.get("chat_id") or "").strip()
        user_id = build_session_key(
            user_id=open_id,
            chat_id=chat_id,
            chat_type=str(callback_payload.get("chat_type") or ("p2p" if chat_id else "")),
            channel_type="feishu",
        )
        batch_progress_emitter = _build_batch_progress_emitter(callback_payload)
        try:
            result = await _call_card_action_callback(
                core=_get_agent_core(),
                user_id=user_id,
                callback_action=cb_action,
                callback_value=callback_payload.get("value") if isinstance(callback_payload.get("value"), dict) else None,
                batch_progress_emitter=batch_progress_emitter,
            )
        except Exception:
            logger.exception(
                "处理卡片回调失败",
                extra={
                    "event_code": "webhook.callback.handle_failed",
                    "event_id": event_id,
                    "callback_action": cb_action,
                },
            )
            if event_id and settings.webhook.dedup.enabled:
                _get_deduplicator().mark(event_id)
            return {"status": "ok", "reason": expired_text}
        if event_id and settings.webhook.dedup.enabled:
            _get_deduplicator().mark(event_id)

        if str(result.get("status") or "") == "processed":
            asyncio.create_task(_emit_callback_result_message(callback_payload, result))

        text = str(result.get("text") or "")
        if str(result.get("status") or "") == "expired":
            return {"status": "ok", "reason": text or expired_text}
        return {"status": "ok", "reason": text or duplicated_text}

    header = payload.get("header") or {}
    if settings.feishu.verification_token:
        token = header.get("token") or payload.get("token")
        if token != settings.feishu.verification_token:
            logger.warning("Webhook 验证令牌不匹配", extra={"event_code": "webhook.token_mismatch"})
            raise HTTPException(status_code=401, detail="Verification failed")

    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    event_id = envelope.event_id
    logger.info(
        "Webhook 事件信息",
        extra={
            "event_code": "webhook.event.info",
            "event_id": event_id,
            "event_type": envelope.event_type,
        },
    )

    dedup_key = (envelope.message.message_id if envelope.message else "") or event_id
    deduplicator = _get_deduplicator()
    if dedup_key and settings.webhook.dedup.enabled and deduplicator.is_duplicate(dedup_key):
        logger.info("检测到重复消息，跳过处理", extra={"event_code": "webhook.message.duplicate", "dedup_key": dedup_key})
        return {"status": "duplicate"}

    route_result = _get_event_router().route(envelope)
    if route_result.status != "accepted":
        record_inbound_message("webhook", str((message.message_type if (message := envelope.message) else "unknown")), route_result.status)
        return {"status": route_result.status, "reason": route_result.reason}

    message = envelope.message
    if message is None:
        return {"status": "ignored", "reason": "missing_message"}

    if settings.webhook.filter.ignore_bot_message and message.sender_type == "bot":
        logger.info("已忽略机器人消息", extra={"event_code": "webhook.message.ignored_bot"})
        record_inbound_message("webhook", message.message_type, "ignored")
        return {"status": "ignored"}

    if settings.webhook.filter.private_chat_only and message.chat_type != "p2p":
        logger.info("已忽略非私聊消息", extra={"event_code": "webhook.message.ignored_non_private"})
        record_inbound_message("webhook", message.message_type, "ignored")
        return {"status": "ignored"}

    message_type = message.message_type
    allow_file_pipeline = bool(settings.file_pipeline.enabled) and is_file_pipeline_message(message_type)
    if message_type not in settings.webhook.filter.allowed_message_types and not allow_file_pipeline:
        logger.info(
            "已忽略不支持的消息类型",
            extra={"event_code": "webhook.message.ignored_type", "message_type": message_type},
        )
        record_inbound_message("webhook", message_type, "ignored")
        return {"status": "ignored"}

    logger.info(
        "开始处理 webhook 消息",
        extra={"event_code": "webhook.message.processing", "message_id": message.message_id},
    )
    if dedup_key and settings.webhook.dedup.enabled:
        deduplicator.mark(dedup_key)
    record_inbound_message("webhook", message_type, "accepted")
    asyncio.create_task(_process_message_event_with_dedup(message, dedup_key))
    return {"status": "ok"}


# endregion


# region 消息处理逻辑
async def _process_message_with_dedup(
    message: dict[str, Any],
    sender: dict[str, Any],
    dedup_key: str | None,
) -> None:
    """执行消息处理并在失败时回滚去重标记"""
    processed = await _process_message(message, sender)
    settings = _get_settings()
    if not processed and dedup_key and settings.webhook.dedup.enabled:
        _get_deduplicator().remove(dedup_key)


async def _process_message_event_with_dedup(
    message_event: MessageEvent,
    dedup_key: str | None,
) -> None:
    """基于标准事件对象处理并在失败时回滚去重标记。"""
    message, sender = _to_legacy_message_sender(message_event)
    processed = await _process_message(message, sender)
    settings = _get_settings()
    if not processed and dedup_key and settings.webhook.dedup.enabled:
        _get_deduplicator().remove(dedup_key)


def _to_legacy_message_sender(message_event: MessageEvent) -> tuple[dict[str, Any], dict[str, Any]]:
    """将标准事件对象转换为现有处理流程使用的结构。"""
    message = {
        "message_id": message_event.message_id,
        "chat_id": message_event.chat_id,
        "chat_type": message_event.chat_type,
        "message_type": message_event.message_type,
        "content": message_event.content,
    }
    sender = {
        "sender_type": message_event.sender_type,
        "sender_id": {
            "open_id": message_event.sender_open_id,
            "user_id": message_event.sender_user_id,
        },
    }
    return message, sender


async def _process_message(message: dict[str, Any], sender: dict[str, Any]) -> bool:
    """
    异步处理消息

    参数:
        message: 消息体
        sender: 发送者信息
    """
    logger.info("开始执行消息处理流程", extra={"event_code": "webhook.pipeline.start"})
    settings = _get_settings()
    file_pipeline_cfg = getattr(settings, "file_pipeline", None)
    file_pipeline_enabled = bool(getattr(file_pipeline_cfg, "enabled", False))
    max_file_bytes = int(getattr(file_pipeline_cfg, "max_bytes", 5 * 1024 * 1024))
    normalized = normalize_content(
        message_type=str(message.get("message_type") or ""),
        content=str(message.get("content") or ""),
        file_pipeline_enabled=file_pipeline_enabled,
        max_file_bytes=max_file_bytes,
        metrics_enabled=bool(getattr(file_pipeline_cfg, "metrics_enabled", True)),
    )
    text = normalized.text
    logger.info(
        "提取文本完成",
        extra={
            "event_code": "webhook.message.text_extracted",
            "text": text,
            "message_type": normalized.message_type,
            "segment_count": normalized.segment_count,
            "truncated": normalized.truncated,
        },
    )
    if not text:
        logger.warning("消息无文本内容，结束处理", extra={"event_code": "webhook.message.empty_text"})
        record_inbound_message("webhook", str(normalized.message_type or "unknown"), "ignored")
        return False

    chat_id = message.get("chat_id")
    chat_type = message.get("chat_type")
    message_id = message.get("message_id")
    sender_id = sender.get("sender_id", {})
    open_id = str(sender_id.get("open_id") or "").strip()
    inner_user_id = str(sender_id.get("user_id") or "").strip()
    if open_id:
        user_id = open_id
    elif inner_user_id:
        user_id = f"user:{inner_user_id}"
    elif chat_id and message_id:
        user_id = f"chat:{chat_id}:msg:{message_id}"
    elif chat_id:
        user_id = f"chat:{chat_id}:anon"
    else:
        user_id = "unknown"
    scoped_user_id = build_session_key(
        user_id=user_id,
        chat_id=str(chat_id or ""),
        chat_type=str(chat_type or ""),
        channel_type="feishu",
    )

    if sender_id.get("open_id"):
        logger.info(
            "Webhook 发送者 open_id 已识别",
            extra={"event_code": "webhook.sender.open_id", "open_id": sender_id.get("open_id")},
        )

    if not chat_id:
        logger.warning("缺少 chat_id，结束处理", extra={"event_code": "webhook.message.missing_chat_id"})
        record_inbound_message("webhook", str(normalized.message_type or "unknown"), "error")
        return False

    if (
        normalized.message_type in {"image", "audio"}
        and normalized.attachments
        and normalized.attachments[0].accepted
        and not str(chat_id).startswith("test-")
    ):
        try:
            await send_message(
                settings,
                chat_id,
                "text",
                {"text": build_processing_status_text(normalized.message_type)},
                reply_message_id=message_id,
            )
        except Exception as exc:
            logger.warning(
                "发送多模态处理中提示失败: %s",
                exc,
                extra={"event_code": "webhook.reply.media_status_failed"},
            )

    logger.info(
        "消息上下文已就绪",
        extra={
            "event_code": "webhook.message.context_ready",
            "chat_id": chat_id,
            "user_id": scoped_user_id,
            "text": text,
        },
    )

    chunk_decision = await _get_chunk_assembler().ingest(
        scope_key=scoped_user_id,
        text=text,
    )
    if not chunk_decision.should_process:
        logger.info(
            "消息分片已缓存，等待聚合窗口",
            extra={
                "event_code": "webhook.chunk_assembler.buffering",
                "chat_id": chat_id,
                "user_id": scoped_user_id,
                "reason": chunk_decision.reason,
            },
        )
        record_inbound_message("webhook", str(normalized.message_type or "unknown"), "buffered")
        return True
    text = chunk_decision.text
    
    agent_core = _get_agent_core()
    user_manager = _get_user_manager()
    file_markdown = ""
    file_provider = "none"
    file_reason = ""
    first_attachment = normalized.attachments[0] if normalized.attachments else None
    direct_reply_text = ""
    ocr_completion_text = ""
    if normalized.attachments:
        resolved = await resolve_file_markdown(
            attachments=normalized.attachments,
            settings=settings,
            message_type=normalized.message_type,
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
        if guidance and not file_markdown and normalized.message_type in {"file", "audio", "image"}:
            direct_reply_text = guidance
        elif normalized.message_type == "audio" and file_markdown:
            text = file_markdown
            file_markdown = ""
        elif normalized.message_type == "image" and file_markdown:
            ocr_completion_text = build_ocr_completion_text(file_markdown)
    elif normalized.message_type in {"file", "audio", "image"}:
        reason = "extractor_disabled"
        if normalized.message_type == "audio":
            reason = "asr_unconfigured"
        elif normalized.message_type == "image":
            reason = "ocr_unconfigured"
        file_reason = reason
        direct_reply_text = build_file_unavailable_guidance(reason)
    
    try:
        # 静默获取用户信息（仅用于"我的案件"识别）
        open_id = sender_id.get("open_id")
        user_profile = None
        if open_id:
            try:
                user_profile = await user_manager.get_or_create_profile(
                    open_id=open_id,
                    chat_id=chat_id,
                    auto_match=False,  # 不自动匹配，只获取姓名
                )
                logger.info(
                    "用户身份识别完成",
                    extra={"event_code": "webhook.user_profile.resolved", "user_name": user_profile.name},
                )
            except Exception as e:
                logger.warning(
                    "获取用户档案失败: %s",
                    e,
                    extra={"event_code": "webhook.user_profile.resolve_failed"},
                )
        
        # 处理消息
        if direct_reply_text:
            if bool(getattr(settings.reply, "card_enabled", True)) and normalized.message_type in {"file", "audio", "image"}:
                reply = _build_upload_result_reply(
                    guidance_text=direct_reply_text,
                    message_type=normalized.message_type,
                    provider=file_provider,
                    reason_code=file_reason,
                    attachment=first_attachment,
                )
            else:
                reply = {"type": "text", "text": direct_reply_text}
        else:
            reply = await agent_core.handle_message(
                scoped_user_id,
                text,
                chat_id=chat_id,
                chat_type=chat_type,
                user_profile=user_profile,  # 传递用户档案
                file_markdown=file_markdown,
                file_provider=file_provider,
                status_emitter=create_reaction_status_emitter(settings, str(message_id or "")),
            )
            if ocr_completion_text:
                reply = _prepend_reply_text(reply, ocr_completion_text)
        
        if chat_id.startswith("test-"):
            logger.info(
                "测试会话已抑制回复发送",
                extra={"event_code": "webhook.reply.suppressed_test_chat", "reply_text": reply.get("text", "")},
            )
            return True
        
        # 发送回复消息
        outbound = reply.get("outbound") if isinstance(reply, dict) else None
        prefer_card = bool(
            isinstance(outbound, dict)
            and isinstance(outbound.get("blocks"), list)
            and outbound.get("blocks")
            and not isinstance(outbound.get("card_template"), dict)
        )
        if isinstance(outbound, dict) and isinstance(outbound.get("card_template"), dict):
            template_id = str(outbound.get("card_template", {}).get("template_id") or "").strip()
            if template_id == "upload.result":
                prefer_card = True
        payload = _build_send_payload(
            reply,
            card_enabled=bool(getattr(settings.reply, "card_enabled", True)),
            prefer_card=prefer_card,
        )
        msg_type = str(payload.get("msg_type") or "text")
        if msg_type == "interactive":
            content = cast(dict[str, object], payload.get("card") if isinstance(payload.get("card"), dict) else {})
        else:
            msg_type = "text"
            content = cast(dict[str, object], payload.get("content") if isinstance(payload.get("content"), dict) else {"text": _pick_reply_text(reply)})

        text_obj = content.get("text")
        text_len = len(text_obj) if isinstance(text_obj, str) else 0

        logger.info(
            "准备发送回复",
            extra={
                "event_code": "webhook.reply.sending",
                "msg_type": msg_type,
                "text_len": text_len,
                "has_card": bool(reply.get("card")),
            },
        )
        sent = await send_message(settings, chat_id, msg_type, content, reply_message_id=message_id)
        logger.info(
            "回复发送成功",
            extra={"event_code": "webhook.reply.sent", "message_id": sent.get("message_id", "")},
        )
        record_inbound_message("webhook", str(normalized.message_type or "unknown"), "processed")
        return True

    except Exception as exc:
        logger.error(
            "处理消息失败: %s",
            exc,
            extra={"event_code": "webhook.message.process_failed"},
            exc_info=True,
        )
        record_inbound_message("webhook", str(normalized.message_type or "unknown"), "error")
        return False
# endregion
