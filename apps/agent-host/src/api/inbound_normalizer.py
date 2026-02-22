"""
描述: 入站消息标准化
主要功能:
    - 统一提取 text/post/file/audio/image 的文本表示
    - 将渠道原始内容转换为标准输入结构
    - 提供轻量的聚合上限控制
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


_SUPPORTED_FILE_EXTENSIONS = {
    "pdf",
    "doc",
    "docx",
    "txt",
    "md",
    "markdown",
    "csv",
}


@dataclass
class NormalizedAttachment:
    """标准化附件元数据。"""

    file_key: str
    file_name: str
    file_type: str
    source_url: str
    file_size: int | None = None
    accepted: bool = True
    reject_reason: str = ""


@dataclass
class NormalizedInput:
    """标准化入站内容。"""

    text: str
    message_type: str
    segments: list[str]
    segment_count: int
    attachments: list[NormalizedAttachment] = field(default_factory=list)
    truncated: bool = False


def normalize_content(
    message_type: str,
    content: str,
    max_segments: int = 5,
    max_chars: int = 500,
    file_pipeline_enabled: bool = False,
    max_file_bytes: int = 5 * 1024 * 1024,
) -> NormalizedInput:
    """将飞书消息 content 标准化为文本输入。"""
    normalized_type = str(message_type or "").strip().lower()
    segments: list[str]

    attachments: list[NormalizedAttachment] = []

    if normalized_type == "text":
        segments = [_extract_text(content)]
    elif normalized_type == "post":
        segments = _extract_post_texts(content)
    elif normalized_type == "file":
        segments = ["[收到文件消息]"]
        if file_pipeline_enabled:
            attachments = _extract_attachment(content, max_file_bytes=max_file_bytes)
    elif normalized_type == "audio":
        segments = ["[收到语音消息]"]
        if file_pipeline_enabled:
            attachments = _extract_attachment(content, max_file_bytes=max_file_bytes)
    elif normalized_type == "image":
        segments = ["[收到图片消息]"]
        if file_pipeline_enabled:
            attachments = _extract_attachment(content, max_file_bytes=max_file_bytes)
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
        attachments=attachments,
        truncated=truncated,
    )


def _extract_text(content: str) -> str:
    if not content:
        return ""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("text") or "")


def _extract_post_texts(content: str) -> list[str]:
    if not content:
        return []
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []

    post_raw = payload.get("post")
    post = post_raw if isinstance(post_raw, dict) else {}
    zh_cn_block = post.get("zh_cn")
    if isinstance(zh_cn_block, dict):
        lang_block = zh_cn_block
    else:
        lang_block = next((value for value in post.values() if isinstance(value, dict)), {})
    content_raw = lang_block.get("content") if isinstance(lang_block, dict) else None
    content_rows = content_raw if isinstance(content_raw, list) else []

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


def _extract_attachment(content: str, max_file_bytes: int) -> list[NormalizedAttachment]:
    if not content:
        return []

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []

    file_key = str(
        payload.get("file_key")
        or payload.get("fileKey")
        or payload.get("image_key")
        or payload.get("imageKey")
        or payload.get("audio_key")
        or payload.get("audioKey")
        or ""
    ).strip()
    file_name = str(payload.get("file_name") or payload.get("fileName") or "").strip()
    source_url = str(payload.get("source_url") or payload.get("url") or "").strip()

    raw_size = payload.get("file_size")
    file_size: int | None = None
    try:
        if raw_size is not None and str(raw_size).strip() != "":
            file_size = int(raw_size)
    except (TypeError, ValueError):
        file_size = None

    file_type = _resolve_file_type(file_name=file_name, payload=payload)
    accepted = True
    reject_reason = ""
    if file_size is not None and file_size > max(1, int(max_file_bytes)):
        accepted = False
        reject_reason = "file_too_large"
    elif file_type and file_type not in _SUPPORTED_FILE_EXTENSIONS:
        accepted = False
        reject_reason = "unsupported_file_type"

    return [
        NormalizedAttachment(
            file_key=file_key,
            file_name=file_name,
            file_type=file_type,
            source_url=source_url,
            file_size=file_size,
            accepted=accepted,
            reject_reason=reject_reason,
        )
    ]


def _resolve_file_type(file_name: str, payload: dict[str, object]) -> str:
    explicit_type = str(payload.get("file_type") or payload.get("fileType") or "").strip().lower()
    if explicit_type:
        return explicit_type

    lowered = file_name.lower().strip()
    if "." not in lowered:
        return ""
    return lowered.rsplit(".", 1)[-1]
