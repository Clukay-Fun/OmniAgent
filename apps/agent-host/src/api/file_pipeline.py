from __future__ import annotations

from typing import Any

from src.adapters.file import ExternalFileExtractor, ExtractorRequest


_FILE_MESSAGE_TYPES = {"file", "audio", "image"}


def file_message_types() -> set[str]:
    return set(_FILE_MESSAGE_TYPES)


def is_file_pipeline_message(message_type: str) -> bool:
    return str(message_type or "").strip().lower() in _FILE_MESSAGE_TYPES


def build_file_unavailable_guidance(reason: str = "") -> str:
    reason_key = str(reason or "").strip().lower()
    if reason_key == "file_too_large":
        return "已收到文件，但文件体积超过当前限制，请压缩后重试。"
    if reason_key == "unsupported_file_type":
        return "已收到文件，但当前仅支持 PDF/Word/TXT/Markdown/CSV。"
    if reason_key in {"extractor_disabled", "extractor_unconfigured"}:
        return "已收到文件，但当前未开启解析能力，请稍后再试或补充文字说明。"
    return "已收到文件，但暂时无法完成解析。请稍后重试或直接描述你的问题。"


async def resolve_file_markdown(
    attachments: list[Any],
    settings: Any,
) -> tuple[str, str]:
    if not attachments:
        return "", ""

    attachment = attachments[0]
    if not attachment.accepted:
        return "", build_file_unavailable_guidance(attachment.reject_reason)

    extractor = ExternalFileExtractor(
        settings=settings.file_extractor,
        timeout_seconds=int(settings.file_pipeline.timeout_seconds),
    )
    result = await extractor.extract(
        ExtractorRequest(
            file_key=attachment.file_key,
            file_name=attachment.file_name,
            file_type=attachment.file_type,
            source_url=attachment.source_url,
        )
    )
    if result.success:
        return result.markdown, ""
    return "", build_file_unavailable_guidance(result.reason)
