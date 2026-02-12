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
import json
import time
import logging
from typing import Any, cast

from Crypto.Cipher import AES
from fastapi import APIRouter, HTTPException, Request

from src.core.orchestrator import AgentOrchestrator
from src.core.session import SessionManager
from src.config import get_settings
from src.llm.provider import create_llm_client
from src.mcp.client import MCPClient
from src.utils.feishu_api import send_message


router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================
# region 延迟初始化（Lazy Initialization）
# ============================================
_settings: Any = None
_session_manager: Any = None
_mcp_client: Any = None
_llm_client: Any = None
_agent_core: AgentOrchestrator | None = None
_deduplicator: "EventDeduplicator | None" = None
_user_manager: Any = None  # 用户管理器


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
        )
        logger.info("AgentOrchestrator initialized")
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


def _get_user_manager():
    """延迟初始化用户管理器"""
    global _user_manager, _mcp_client
    if _user_manager is None:
        try:
            logger.info("Initializing UserManager...")
            from src.user.manager import UserManager
            from src.user.matcher import UserMatcher
            from src.user.cache import UserCache
            
            settings = _get_settings()
            
            # 确保 MCP 客户端已初始化
            if _mcp_client is None:
                _mcp_client = MCPClient(settings)
            
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
            
            # 创建用户管理器
            _user_manager = UserManager(
                settings=settings,
                matcher=matcher,
                cache=cache,
            )
            
            logger.info("UserManager initialized")
        except Exception as e:
            logger.error(f"Failed to initialize UserManager: {e}", exc_info=True)
            raise
    
    return _user_manager


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


agent_core = _AgentCoreProxy()
# endregion
# ============================================


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
# endregion


# region Webhook 路由处理


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
    logger.info("=== Received Feishu webhook request ===")
    payload = await request.json()
    logger.info(f"Payload type: {payload.get('type')}, event_type: {payload.get('header', {}).get('event_type')}")

    if payload.get("type") == "url_verification":
        logger.info("URL verification request")
        return {"challenge": payload.get("challenge", "")}

    settings = _get_settings()
    if payload.get("encrypt"):
        logger.info("Decrypting payload...")
        if not settings.feishu.encrypt_key:
            raise HTTPException(status_code=400, detail="encrypt_key is required")
        payload = _decrypt_event(payload["encrypt"], settings.feishu.encrypt_key)

    header = payload.get("header") or {}
    if settings.feishu.verification_token:
        token = header.get("token") or payload.get("token")
        if token != settings.feishu.verification_token:
            logger.warning("Verification token mismatch")
            raise HTTPException(status_code=401, detail="Verification failed")

    event_id = header.get("event_id") or payload.get("event_id")
    logger.info(f"Event ID: {event_id}")

    event = payload.get("event") or {}
    message = event.get("message") or {}
    sender = event.get("sender") or {}

    message_id = message.get("message_id") or message.get("messageId")
    dedup_key = message_id or event_id
    deduplicator = _get_deduplicator()
    if dedup_key and settings.webhook.dedup.enabled and deduplicator.is_duplicate(dedup_key):
        logger.info(f"Duplicate message: {dedup_key}")
        return {"status": "duplicate"}

    if settings.webhook.filter.ignore_bot_message and sender.get("sender_type") == "bot":
        logger.info("Ignored bot message")
        return {"status": "ignored"}

    if settings.webhook.filter.private_chat_only and not _is_private_chat(message):
        logger.info("Ignored non-private chat message")
        return {"status": "ignored"}

    message_type = message.get("message_type")
    if message_type not in settings.webhook.filter.allowed_message_types:
        logger.info(f"Ignored message type: {message_type}")
        return {"status": "ignored"}

    logger.info(f"Processing message: {message_id}")
    if dedup_key and settings.webhook.dedup.enabled:
        deduplicator.mark(dedup_key)
    asyncio.create_task(_process_message_with_dedup(message, sender, dedup_key))
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


async def _process_message(message: dict[str, Any], sender: dict[str, Any]) -> bool:
    """
    异步处理消息

    参数:
        message: 消息体
        sender: 发送者信息
    """
    logger.info("=== Starting _process_message ===")
    text = _get_text_content(message)
    logger.info(f"Extracted text: {text}")
    if not text:
        logger.warning("No text content, returning")
        return False

    chat_id = message.get("chat_id")
    chat_type = message.get("chat_type")
    message_id = message.get("message_id")
    sender_id = sender.get("sender_id", {})
    user_id = sender_id.get("open_id") or sender_id.get("user_id") or chat_id or "unknown"
    if sender_id.get("open_id"):
        logger.info("Webhook sender open_id: %s", sender_id.get("open_id"))

    if not chat_id:
        logger.warning("No chat_id, returning")
        return False

    logger.info(f"chat_id: {chat_id}, user_id: {user_id}, text: {text}")
    
    settings = _get_settings()
    agent_core = _get_agent_core()
    user_manager = _get_user_manager()
    
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
                logger.info(f"User identified: {user_profile.name}")
            except Exception as e:
                logger.warning(f"Failed to get user profile: {e}")
        
        # 处理消息
        reply = await agent_core.handle_message(
            user_id,
            text,
            chat_id=chat_id,
            chat_type=chat_type,
            user_profile=user_profile,  # 传递用户档案
        )
        
        if chat_id.startswith("test-"):
            logger.info("Test chat_id, reply suppressed: %s", reply.get("text", ""))
            return True
        
        # 发送回复消息
        content: dict[str, object]
        if reply.get("type") == "card":
            msg_type = "interactive"
            card_payload = reply.get("card")
            content = cast(dict[str, object], card_payload) if isinstance(card_payload, dict) else {}
        else:
            msg_type = "text"
            content = cast(dict[str, object], {"text": str(reply.get("text") or "")})

        text_obj = content.get("text")
        text_len = len(text_obj) if isinstance(text_obj, str) else 0

        logger.info(
            "Sending reply: msg_type=%s, text_len=%s, has_card=%s",
            msg_type,
            text_len,
            bool(reply.get("card")),
        )
        sent = await send_message(settings, chat_id, msg_type, content, reply_message_id=message_id)
        logger.info("Reply sent, message_id: %s", sent.get("message_id", ""))
        return True

    except Exception as exc:
        logger.error("Error processing message: %s", exc, exc_info=True)
        return False
# endregion
