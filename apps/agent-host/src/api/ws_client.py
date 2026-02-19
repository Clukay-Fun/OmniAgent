"""
描述: 飞书 WebSocket 长连接客户端
主要功能:
    - 使用 lark-oapi SDK 建立长连接接收事件
    - 无需公网 IP 或 内网穿透 (适合开发与内网部署)
    - 异步分发消息至 Agent 核心
"""

from __future__ import annotations

import asyncio
import json
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

from src.config import get_settings
from src.core.orchestrator import AgentOrchestrator
from src.core.session import SessionManager
from src.llm.provider import create_llm_client
from src.mcp.client import MCPClient
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
)
# endregion
# ============================================


# endregion


# region 消息处理逻辑
async def handle_message_async(
    user_id: str,
    chat_id: str,
    text: str,
    message_id: str,
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
        
        # 调用 Agent 处理
        reply = await agent_core.handle_message(user_id, text)
        
        # 发送回复
        reply_text = reply.get("text", "")
        if reply_text:
            await send_reply(chat_id, reply_text, message_id)
            
    except Exception as e:
        logger.error(
            "长连接消息处理失败: %s",
            e,
            extra={"event_code": "ws.message.processing_error"},
            exc_info=True,
        )
        error_text = settings.reply.templates.error.format(message=str(e))
        await send_reply(chat_id, error_text, message_id)


async def send_reply(chat_id: str, text: str, reply_message_id: str | None = None) -> None:
    """
    发送回复消息
    
    参数:
        chat_id: 会话 ID
        text: 回复文本
        reply_message_id: 原消息 ID（可选，用于引用回复）
    """
    from src.utils.feishu_api import send_message
    
    await send_message(
        settings,
        chat_id,
        "text",
        {"text": text},
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
        event = data.event
        if not event:
            return
        
        message = event.message
        sender = event.sender
        
        if not message or not sender:
            return
        
        # 提取消息信息
        message_id = message.message_id
        chat_id = message.chat_id
        message_type = message.message_type
        chat_type = message.chat_type
        
        # 仅处理私聊文本消息
        if chat_type != "p2p":
            logger.debug(
                "忽略非私聊消息: chat_type=%s",
                chat_type,
                extra={"event_code": "ws.message.ignored_non_p2p"},
            )
            return
        
        if message_type != "text":
            logger.debug(
                "忽略非文本消息: message_type=%s",
                message_type,
                extra={"event_code": "ws.message.ignored_non_text"},
            )
            return
        
        # 忽略机器人自己的消息
        sender_type = sender.sender_type
        if sender_type == "bot":
            return
        
        # 提取文本内容
        content = message.content
        if not content:
            return
        
        text = _extract_text(content)
        if not text:
            return
        
        # 获取 user_id
        sender_id = sender.sender_id
        user_id = (
            sender_id.user_id
            if sender_id and sender_id.user_id
            else chat_id or "unknown"
        )
        
        logger.info(
            "收到长连接消息",
            extra={
                "event_code": "ws.message.received",
                "user_id": user_id,
                "chat_id": chat_id,
                "text": text[:50],
            },
        )
        
        # 异步处理消息
        asyncio.create_task(
            handle_message_async(user_id, chat_id, text, message_id)
        )
        
    except Exception as e:
        logger.error(
            "处理长连接事件失败: %s",
            e,
            extra={"event_code": "ws.event.handle_error"},
            exc_info=True,
        )


def _extract_text(content: str) -> str:
    """从消息 content 中提取文本"""
    try:
        payload = json.loads(content)
        return payload.get("text", "")
    except json.JSONDecodeError:
        return ""
    except json.JSONDecodeError:
        return ""
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
