from __future__ import annotations

from typing import Any

from src.adapters.file import ExternalFileExtractor, ExtractorRequest
from src.utils.metrics import record_file_pipeline


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
    if reason_key in {"extractor_disabled", "extractor_unconfigured", "ocr_unconfigured"}:
        return "已收到文件，但当前未开启解析能力，请稍后再试或补充文字说明。"
    if reason_key.startswith("extractor_auth_failed"):
        return "已收到文件，但解析服务鉴权失败，请联系管理员检查 API 凭证配置。"
    if reason_key.startswith("extractor_endpoint_not_found"):
        return "已收到文件，但解析服务地址配置可能不正确，请稍后再试。"
    if reason_key.startswith("extractor_timeout"):
        return "已收到文件，但解析超时，请稍后重试或补充文字说明。"
    if reason_key.startswith("extractor_rate_limited"):
        return "已收到文件，但解析服务当前较忙，请稍后重试。"
    return "已收到文件，但暂时无法完成解析。请稍后重试或直接描述你的问题。"


def _status_from_reason(reason: str) -> str:
    key = str(reason or "").strip().lower()
    if key.endswith("_fail_open"):
        key = key[: -len("_fail_open")]
    if key in {"extractor_disabled", "ocr_disabled"}:
        return "disabled"
    if key in {"extractor_unconfigured", "ocr_unconfigured"}:
        return "unconfigured"
    return "fail"


async def resolve_file_markdown(
    attachments: list[Any],
    settings: Any,
    message_type: str = "file",
) -> tuple[str, str, str]:
    metrics_enabled = bool(getattr(getattr(settings, "file_pipeline", None), "metrics_enabled", True))

    def _record(stage: str, status: str, provider: str = "none") -> None:
        if not metrics_enabled:
            return
        record_file_pipeline(stage, status, provider)

    if not attachments:
        _record("extract", "skipped", "none")
        return "", "", "none"

    attachment = attachments[0]
    if not attachment.accepted:
        _record("extract", "skipped", "none")
        return "", build_file_unavailable_guidance(attachment.reject_reason), "none"

    extractor = ExternalFileExtractor(
        settings=settings.file_extractor,
        timeout_seconds=int(settings.file_pipeline.timeout_seconds),
        ocr_settings=getattr(settings, "ocr", None),
    )
    result = await extractor.extract(
        ExtractorRequest(
            file_key=attachment.file_key,
            file_name=attachment.file_name,
            file_type=attachment.file_type,
            source_url=attachment.source_url,
            message_type=message_type,
        )
    )
    if result.success:
        _record("extract", "success", result.provider)
        return result.markdown, "", result.provider

    _record("extract", _status_from_reason(result.reason), result.provider)
    return "", build_file_unavailable_guidance(result.reason), result.provider
