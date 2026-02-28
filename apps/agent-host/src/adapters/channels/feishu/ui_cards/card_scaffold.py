"""
描述: 提供构建消息卡片和文本消息的工具函数
主要功能:
    - 构建包含元素的卡片消息
    - 构建纯文本消息
"""

from __future__ import annotations

from typing import Any

def build_card_payload(
    elements: list[dict[str, Any]],
    wrapper: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    构建一个包含元素的卡片消息

    功能:
        - 过滤并验证输入的元素列表
        - 根据提供的包装器数据构建卡片结构
        - 设置默认配置并合并用户提供的配置
        - 添加可选的头部、卡片链接、国际化元素和头部
        - 处理并合并用户提供的主体数据
    """
    safe_elements = [item for item in elements if isinstance(item, dict)]
    if not safe_elements:
        return None

    wrapper_data = wrapper if isinstance(wrapper, dict) else {}
    card: dict[str, Any] = {
        "schema": str(wrapper_data.get("schema") or "2.0"),
        "body": {"elements": safe_elements},
    }

    raw_config = wrapper_data.get("config")
    config = dict(raw_config) if isinstance(raw_config, dict) else {}
    config.setdefault("update_multi", True)
    if config:
        card["config"] = config

    for key in ("header", "card_link", "i18n_elements", "i18n_header"):
        value = wrapper_data.get(key)
        if value is not None:
            card[key] = value

    body_raw = wrapper_data.get("body")
    if isinstance(body_raw, dict):
        body = dict(body_raw)
        body["elements"] = safe_elements
        card["body"] = body

    return {
        "msg_type": "interactive",
        "card": card,
    }


def build_text_payload(text: str) -> dict[str, Any]:
    """
    构建一个纯文本消息

    功能:
        - 将输入的文本格式化为消息内容
    """
    return {
        "msg_type": "text",
        "content": {"text": str(text or "")},
    }
