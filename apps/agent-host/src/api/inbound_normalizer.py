"""
描述: 入站消息标准化
主要功能:
    - 统一提取 text/post/file/audio/image 的文本表示
    - 将渠道原始内容转换为标准输入结构
    - 提供轻量的聚合上限控制
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class NormalizedInput:
    """标准化入站内容。"""

    text: str
    message_type: str
    segments: list[str]
    segment_count: int
    truncated: bool = False


def normalize_content(
    message_type: str,
    content: str,
    max_segments: int = 5,
    max_chars: int = 500,
) -> NormalizedInput:
    """将飞书消息 content 标准化为文本输入。"""
    normalized_type = str(message_type or "").strip().lower()
    segments: list[str]

    if normalized_type == "text":
        segments = [_extract_text(content)]
    elif normalized_type == "post":
        segments = _extract_post_texts(content)
    elif normalized_type == "file":
        segments = ["[收到文件消息]"]
    elif normalized_type == "audio":
        segments = ["[收到语音消息]"]
    elif normalized_type == "image":
        segments = ["[收到图片消息]"]
    else:
        segments = [_extract_text(content)]

    compact_segments = [seg.strip() for seg in segments if isinstance(seg, str) and seg.strip()]
    truncated = False
    if len(compact_segments) > max_segments:
        compact_segments = compact_segments[:max_segments]
        truncated = True

    merged = "\n\n".join(compact_segments).strip()
    if len(merged) > max_chars:
        merged = merged[:max_chars].rstrip()
        truncated = True

    return NormalizedInput(
        text=merged,
        message_type=normalized_type,
        segments=compact_segments,
        segment_count=len(compact_segments),
        truncated=truncated,
    )


def _extract_text(content: str) -> str:
    if not content:
        return ""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return ""
    return str(payload.get("text") or "")


def _extract_post_texts(content: str) -> list[str]:
    if not content:
        return []
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return []

    post = payload.get("post") if isinstance(payload.get("post"), dict) else {}
    lang_block = post.get("zh_cn") if isinstance(post.get("zh_cn"), dict) else next(
        (value for value in post.values() if isinstance(value, dict)),
        {},
    )
    content_rows = lang_block.get("content") if isinstance(lang_block.get("content"), list) else []

    texts: list[str] = []
    for row in content_rows:
        if not isinstance(row, list):
            continue
        parts: list[str] = []
        for item in row:
            if not isinstance(item, dict):
                continue
            if item.get("tag") == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
        if parts:
            texts.append("".join(parts))

    return texts
