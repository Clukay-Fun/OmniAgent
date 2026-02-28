"""
描述: Discord 网关客户端
主要功能:
    - 消费 Discord 入站消息并交给 AgentOrchestrator
    - 复用 RenderedResponse + DiscordFormatter 渲染回复
    - 支持确认/取消按钮交互并回调 pending_action
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

from src.adapters.channels.discord.event_adapter import DiscordEventAdapter, DiscordMessageEvent, strip_bot_mention
from src.adapters.channels.discord.formatter import (
    DiscordComponentButtonPayload,
    DiscordEmbedPayload,
    DiscordFormatter,
    DiscordResponsePayload,
)
from src.api.conversation_scope import build_session_key
from src.config import DiscordSettings, Settings, get_settings
from src.adapters.channels.feishu.skills.bitable_writer import BitableWriter
from src.core.orchestrator import AgentOrchestrator
from src.core.response.models import RenderedResponse
from src.core.session import SessionManager
from src.llm.provider import create_llm_client
from src.mcp.client import MCPClient
from src.utils.logger import setup_logging


logger = logging.getLogger(__name__)

try:
    import discord
except Exception:  # pragma: no cover - import guard for environments without discord.py
    discord = None



load_dotenv()


_CONFIRM_CANCEL_TOKENS = {
    "确认",
    "确认删除",
    "确认创建",
    "确认修改",
    "取消",
    "是",
    "是的",
    "ok",
    "okay",
    "yes",
    "no",
}

_REMINDER_TOKENS = {
    "提醒",
    "待办",
    "提醒列表",
    "我的提醒",
    "取消提醒",
    "日历",
    "别忘了",
    "记得",
    "自动提醒",
    "自动化提醒",
    "定时提醒",
    "周期提醒",
    "每天",
    "每日",
    "每周",
    "工作日",
    "cron",
}

_QUERY_OPERATION_TOKENS = {
    "查",
    "查询",
    "找",
    "搜索",
    "检索",
}

_WRITE_OPERATION_TOKENS = {
    "新增",
    "新建",
    "创建",
    "添加",
    "录入",
    "登记",
    "更新",
    "修改",
    "改为",
    "改成",
    "删除",
    "删掉",
    "移除",
    "去掉",
    "关闭",
    "结案",
}

_DOMAIN_OBJECT_HINTS = {
    "案件",
    "案号",
    "项目",
    "记录",
    "表格",
    "台账",
    "开庭",
    "庭审",
    "当事人",
    "委托人",
    "法院",
    "主办",
    "协办",
    "进展",
    "状态",
    "合同",
    "招投标",
    "飞书",
}

_PAGINATION_TOKENS = {
    "下一页",
    "继续",
    "更多",
    "下页",
}

_ORDINAL_DETAIL_PATTERN = re.compile(r"第\s*(\d+|[一二三四五六七八九十百]+)\s*(个|条)?\s*(详情|明细|记录|案件)?")

_CLEAR_HISTORY_COMMANDS = {
    "清空会话",
    "清空聊天记录",
    "删除聊天记录",
    "重置会话",
    "重置记忆",
    "清除记忆",
    "清空记忆",
    "reset",
    "/reset",
    "clear",
    "/clear",
}


def _is_guild_allowed(event: DiscordMessageEvent, config: DiscordSettings) -> bool:
    allowlist = [str(item).strip() for item in config.guild_allowlist if str(item).strip()]
    if not allowlist:
        return True
    if event.chat_type != "group":
        return True
    return event.guild_id in allowlist


def _is_user_allowed(event: DiscordMessageEvent, config: DiscordSettings) -> bool:
    allowlist = [str(item).strip() for item in config.allowed_user_ids if str(item).strip()]
    if not allowlist:
        return True
    return event.sender_id in allowlist


def should_process_event(event: DiscordMessageEvent, *, config: DiscordSettings) -> bool:
    """根据配置判断消息是否应进入编排流程。"""

    if event.sender_is_bot and not bool(config.allow_bots):
        return False
    if bool(config.private_chat_only) and event.chat_type != "p2p":
        return False
    if event.chat_type == "group" and bool(config.require_mention) and not bool(event.mentions_bot):
        return False
    if not _is_guild_allowed(event, config):
        return False
    if not _is_user_allowed(event, config):
        return False
    return True


def extract_user_text(event: DiscordMessageEvent, *, bot_user_id: str, require_mention: bool) -> str:
    """提取用户可消费文本（必要时移除 @bot）。"""

    raw_text = str(event.text or "").strip()
    if not raw_text:
        return ""
    if event.chat_type == "group" and bot_user_id:
        return strip_bot_mention(raw_text, bot_user_id)
    return raw_text


def _split_text_chunks(text: str, *, chunk_limit: int, max_lines: int) -> list[str]:
    limit = max(200, min(int(chunk_limit or 1800), 1900))
    line_limit = max(5, int(max_lines or 30))
    chunks: list[str] = []
    current_lines: list[str] = []
    current_len = 0

    for line in str(text or "").splitlines() or [str(text or "")]:
        next_len = current_len + len(line) + (1 if current_lines else 0)
        if current_lines and (next_len > limit or len(current_lines) >= line_limit):
            chunks.append("\n".join(current_lines))
            current_lines = [line]
            current_len = len(line)
            continue
        current_lines.append(line)
        current_len = next_len

    if current_lines:
        chunks.append("\n".join(current_lines))
    return [chunk for chunk in chunks if chunk.strip()]


def is_feishu_operation_query(text: str) -> bool:
    """判断是否为应进入 Feishu 业务链路的操作请求。"""

    raw = str(text or "").strip()
    if not raw:
        return False
    normalized = raw.lower().replace(" ", "")

    if normalized in _CONFIRM_CANCEL_TOKENS:
        return True

    if any(token in normalized for token in _REMINDER_TOKENS):
        return True

    if any(token in normalized for token in _PAGINATION_TOKENS):
        return True

    if _ORDINAL_DETAIL_PATTERN.search(raw):
        return True

    if any(token in normalized for token in _WRITE_OPERATION_TOKENS):
        return True

    has_verb = any(token in normalized for token in _QUERY_OPERATION_TOKENS)
    has_domain_hint = any(token in normalized for token in _DOMAIN_OBJECT_HINTS)
    if has_verb and has_domain_hint:
        return True

    return False


def is_clear_history_command(text: str) -> bool:
    """判断是否为清空会话命令。"""

    raw = str(text or "").strip()
    if not raw:
        return False

    normalized = raw.lower().replace(" ", "")
    normalized = normalized.strip("。.!！？?,，")
    return normalized in _CLEAR_HISTORY_COMMANDS


def _extract_callback_action(custom_id: str) -> str:
    raw = str(custom_id or "").strip()
    prefix = "omni:action:"
    if raw.startswith(prefix):
        return raw[len(prefix) :].strip()
    return ""


def _reply_kwargs(
    chunk: str,
    *,
    include_rich: bool,
    embed: Any = None,
    view: Any = None,
) -> dict[str, Any]:
    """构建 message.reply 参数，避免重复 reference 传参。"""

    kwargs: dict[str, Any] = {
        "content": chunk,
        "mention_author": False,
    }
    if include_rich:
        if embed is not None:
            kwargs["embed"] = embed
        if view is not None:
            kwargs["view"] = view
    return kwargs


def _discord_button_style(style: str) -> Any:
    if discord is None:
        return 2
    normalized = str(style or "secondary").strip().lower()
    mapping = {
        "primary": discord.ButtonStyle.primary,
        "secondary": discord.ButtonStyle.secondary,
        "success": discord.ButtonStyle.success,
        "danger": discord.ButtonStyle.danger,
    }
    return mapping.get(normalized, discord.ButtonStyle.secondary)


def _build_embed(payload: DiscordEmbedPayload) -> Any:
    if discord is None:
        return None
    embed = discord.Embed(title=payload.title[:256], description=payload.description[:4096])
    for field in payload.fields[:25]:
        embed.add_field(name=field.name[:256], value=field.value[:1024], inline=bool(field.inline))
    return embed


if discord is not None:

    class _ActionView(discord.ui.View):
        def __init__(self, buttons: list[DiscordComponentButtonPayload], timeout: float = 300.0) -> None:
            super().__init__(timeout=timeout)
            for button in buttons[:5]:
                self.add_item(
                    discord.ui.Button(
                        label=button.label[:80],
                        custom_id=button.custom_id[:100],
                        style=_discord_button_style(button.style),
                    )
                )

else:

    class _ActionView:  # pragma: no cover - fallback for import-only environments
        def __init__(self, _buttons: list[DiscordComponentButtonPayload], timeout: float = 300.0) -> None:
            self.timeout = timeout


@dataclass
class _Runtime:
    settings: Settings
    formatter: DiscordFormatter
    agent_core: AgentOrchestrator


def _build_agent_core(settings: Settings) -> AgentOrchestrator:
    session_manager = SessionManager(settings.session)
    mcp_client = MCPClient(settings)
    llm_client = create_llm_client(settings.llm)
    return AgentOrchestrator(
        settings=settings,
        session_manager=session_manager,
        mcp_client=mcp_client,
        llm_client=llm_client,
        data_writer=BitableWriter(mcp_client),
        skills_config_path="config/skills.yaml",
    )


if discord is not None:

    class OmniDiscordClient(discord.Client):
        def __init__(self, runtime: _Runtime) -> None:
            intents = discord.Intents.default()
            intents.message_content = True
            super().__init__(intents=intents)
            self._runtime = runtime

        async def on_ready(self) -> None:
            logger.info(
                "Discord 客户端已就绪",
                extra={
                    "event_code": "discord.client.ready",
                    "bot_user_id": str(getattr(getattr(self, "user", None), "id", "") or ""),
                },
            )

        async def on_message(self, message: Any) -> None:
            if self.user is not None and getattr(message.author, "id", None) == self.user.id:
                return

            bot_user_id = str(getattr(getattr(self, "user", None), "id", "") or "")
            event = DiscordEventAdapter.from_message(message, bot_user_id=bot_user_id)
            config = self._runtime.settings.discord
            if not should_process_event(event, config=config):
                return

            text = extract_user_text(event, bot_user_id=bot_user_id, require_mention=bool(config.require_mention)).strip()
            if not text:
                return

            session_user_id = build_session_key(
                user_id=event.sender_id,
                chat_id=event.channel_id,
                chat_type=event.chat_type,
                channel_type="discord",
            )

            if event.chat_type == "p2p" and is_clear_history_command(text):
                self._runtime.agent_core.clear_user_conversation(session_user_id)
                await message.reply(
                    "已清空当前私聊会话记忆。Discord 聊天窗口中的历史消息请手动删除。",
                    mention_author=False,
                )
                return

            is_operation = is_feishu_operation_query(text)
            user_profile = {
                "channel_type": "discord",
                "allow_open_domain_chat": True,
                "mode": "feishu_operation" if is_operation else "open_chat",
                "raw_user_id": event.sender_id,
            }

            try:
                reply = await self._runtime.agent_core.handle_message(
                    user_id=session_user_id,
                    text=text,
                    chat_id=event.channel_id,
                    chat_type=event.chat_type,
                    user_profile=user_profile,
                    force_chitchat=not is_operation,
                )
                rendered = RenderedResponse.from_outbound(
                    reply.get("outbound") if isinstance(reply, dict) else None,
                    fallback_text=str((reply or {}).get("text") or "请稍后重试。"),
                )
                payload = self._runtime.formatter.format(rendered)
                await self._send_payload(message, payload)
            except Exception as exc:
                logger.exception(
                    "Discord 消息处理失败",
                    extra={
                        "event_code": "discord.message.handle_failed",
                        "channel_id": event.channel_id,
                        "user_id": session_user_id,
                    },
                )
                await message.reply(f"处理失败，请稍后重试。({type(exc).__name__})", mention_author=False)

        async def on_interaction(self, interaction: Any) -> None:
            if not getattr(interaction, "type", None) == discord.InteractionType.component:
                return
            data = getattr(interaction, "data", None)
            custom_id = str((data or {}).get("custom_id") or "")
            callback_action = _extract_callback_action(custom_id)
            if not callback_action:
                return

            user = getattr(interaction, "user", None)
            if user is None:
                return

            channel = getattr(interaction, "channel", None)
            guild = getattr(interaction, "guild", None)
            chat_type = "group" if guild is not None else "p2p"
            channel_id = str(getattr(channel, "id", "") or "")
            user_id = str(getattr(user, "id", "") or "")

            candidates: list[str] = []
            for scoped_chat_type in (chat_type, "group", "p2p", ""):
                candidate = build_session_key(
                    user_id=user_id,
                    chat_id=channel_id,
                    chat_type=scoped_chat_type,
                    channel_type="discord",
                )
                if candidate and candidate not in candidates:
                    candidates.append(candidate)

            result: dict[str, Any] = {"status": "expired", "text": "操作已过期，请重新发起。"}
            for scoped_user_id in candidates:
                current = await self._runtime.agent_core.handle_card_action_callback(
                    user_id=scoped_user_id,
                    callback_action=callback_action,
                    callback_value=None,
                )
                if isinstance(current, dict):
                    result = current
                if str(result.get("status") or "") != "expired":
                    break

            outbound = result.get("outbound") if isinstance(result, dict) else None
            rendered = RenderedResponse.from_outbound(
                outbound if isinstance(outbound, dict) else None,
                fallback_text=str(result.get("text") or "已处理"),
            )
            payload = self._runtime.formatter.format(rendered)

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    content=payload.text[:1800],
                    embed=_build_embed(payload.embed) if payload.embed is not None else None,
                    view=_ActionView(payload.components) if payload.components else None,
                )
                return
            await interaction.followup.send(
                content=payload.text[:1800],
                embed=_build_embed(payload.embed) if payload.embed is not None else None,
                view=_ActionView(payload.components) if payload.components else None,
            )

        async def _send_payload(self, message: Any, payload: DiscordResponsePayload) -> None:
            config = self._runtime.settings.discord
            chunks = _split_text_chunks(
                payload.text,
                chunk_limit=int(config.text_chunk_limit),
                max_lines=int(config.max_lines_per_message),
            )
            if not chunks:
                chunks = ["已处理"]

            embed_obj = _build_embed(payload.embed) if payload.embed is not None else None
            view_obj = _ActionView(payload.components) if payload.components else None

            for index, chunk in enumerate(chunks):
                kwargs = _reply_kwargs(
                    chunk,
                    include_rich=index == 0,
                    embed=embed_obj,
                    view=view_obj,
                )
                await message.reply(**kwargs)

else:

    class OmniDiscordClient:  # pragma: no cover - fallback for import-only environments
        def __init__(self, _runtime: _Runtime) -> None:
            raise RuntimeError("discord.py is required. Please install discord.py>=2.4.0")


def start_discord_client() -> None:
    """启动 Discord 客户端（阻塞）。"""

    if discord is None:
        raise RuntimeError("discord.py is required. Please install discord.py>=2.4.0")

    settings = get_settings()
    setup_logging(settings.logging)

    if not bool(settings.discord.enabled):
        logger.info("Discord 通道未启用，跳过启动", extra={"event_code": "discord.client.disabled"})
        return

    token = str(settings.discord.bot_token or "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required when discord.enabled=true")

    runtime = _Runtime(
        settings=settings,
        formatter=DiscordFormatter(
            embed_enabled=bool(settings.discord.embed_enabled),
            components_enabled=bool(settings.discord.components_enabled),
        ),
        agent_core=_build_agent_core(settings),
    )

    client = OmniDiscordClient(runtime)
    logger.info("启动 Discord 客户端", extra={"event_code": "discord.client.start"})
    client.run(token)


if __name__ == "__main__":
    start_discord_client()
