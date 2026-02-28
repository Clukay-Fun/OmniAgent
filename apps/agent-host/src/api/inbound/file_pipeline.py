"""
描述: 提供文件处理相关的功能，包括文件类型检查、处理状态构建、OCR文本构建以及文件内容解析。
主要功能:
    - 文件类型检查
    - 处理状态文本构建
    - OCR文本构建
    - 文件内容解析
"""

from __future__ import annotations

from typing import Any

from src.adapters.file import ExternalFileExtractor, ExtractorRequest
from src.adapters.file.providers import OCRProvider
from src.utils.metrics import record_file_pipeline


_FILE_MESSAGE_TYPES = {"file", "audio", "image"}
_ocr_provider = OCRProvider()


def file_message_types() -> set[str]:
    """
    返回支持的文件消息类型集合。

    功能:
        - 返回一个包含支持的文件消息类型的集合
    """
    return set(_FILE_MESSAGE_TYPES)


def is_file_pipeline_message(message_type: str) -> bool:
    """
    检查消息类型是否为文件处理管道支持的类型。

    功能:
        - 检查消息类型是否在支持的文件消息类型集合中
    """
    return str(message_type or "").strip().lower() in _FILE_MESSAGE_TYPES


def build_file_unavailable_guidance(reason: str = "") -> str:
    """
    根据原因构建文件不可用的指导信息。

    功能:
        - 根据不同的原因返回相应的指导信息
    """
    reason_key = str(reason or "").strip().lower()
    if reason_key.endswith("_fail_open"):
        reason_key = reason_key[: -len("_fail_open")]
    if reason_key == "file_too_large":
        return "已收到文件，但文件体积超过当前限制，请压缩后重试。"
    if reason_key == "unsupported_file_type":
        return "已收到文件，但当前仅支持 PDF/Word/TXT/Markdown/CSV。"
    if reason_key in {"extractor_disabled", "extractor_unconfigured", "ocr_unconfigured", "ocr_disabled"}:
        return "已收到文件，但当前未开启解析能力，请稍后再试或补充文字说明。"
    if reason_key in {"asr_disabled", "asr_unconfigured", "asr_empty_transcript"}:
        return "语音识别失败，请发送文字。"
    if reason_key in {"ocr_empty_text", "extractor_empty_markdown", "extractor_empty_content", "extractor_malformed_response"}:
        return "未能识别内容，请补充文字说明。"
    if reason_key.startswith("ocr_"):
        return "图片识别失败，请稍后重试或补充文字说明。"
    if reason_key.startswith("asr_"):
        return "语音识别失败，请发送文字。"
    if reason_key.startswith("extractor_auth_failed"):
        return "已收到文件，但解析服务鉴权失败，请联系管理员检查 API 凭证配置。"
    if reason_key.startswith("extractor_endpoint_not_found"):
        return "已收到文件，但解析服务地址配置可能不正确，请稍后再试。"
    if reason_key.startswith("extractor_timeout"):
        return "已收到文件，但解析超时，请稍后重试或补充文字说明。"
    if reason_key.startswith("extractor_connect_failed"):
        return "已收到文件，但解析服务连接失败，请稍后重试。"
    if reason_key.startswith("extractor_network_error"):
        return "已收到文件，但解析服务网络异常，请稍后重试。"
    if reason_key.startswith("extractor_provider_error"):
        return "已收到文件，但解析服务异常，请稍后重试或补充文字说明。"
    if reason_key.startswith("extractor_rate_limited"):
        return "已收到文件，但解析服务当前较忙，请稍后重试。"
    if reason_key.startswith("cost_circuit_breaker_open"):
        return "当前服务预算达到当日阈值，暂不支持新的文件解析请求，请稍后再试或直接发送文字。"
    return "已收到文件，但暂时无法完成解析。请稍后重试或直接描述你的问题。"


def _status_from_reason(reason: str) -> str:
    """
    根据原因返回处理状态。

    功能:
        - 根据不同的原因返回相应的处理状态
    """
    key = str(reason or "").strip().lower()
    if key.endswith("_fail_open"):
        key = key[: -len("_fail_open")]
    if key in {"extractor_disabled", "ocr_disabled", "asr_disabled"}:
        return "disabled"
    if key in {"extractor_unconfigured", "ocr_unconfigured", "asr_unconfigured"}:
        return "unconfigured"
    return "fail"


def build_processing_status_text(message_type: str) -> str:
    """
    根据消息类型构建处理状态文本。

    功能:
        - 根据不同的消息类型返回相应的处理状态文本
    """
    normalized = str(message_type or "").strip().lower()
    if normalized == "image":
        return "正在识别图片内容，请稍候..."
    if normalized == "audio":
        return "正在识别语音内容，请稍候..."
    return "正在解析文件内容，请稍候..."


def build_ocr_completion_text(markdown: str) -> str:
    """
    构建OCR完成文本。

    功能:
        - 使用OCR提供者构建完成文本
    """
    return _ocr_provider.build_completion_text(markdown)


async def resolve_file_markdown(
    attachments: list[Any],
    settings: Any,
    message_type: str = "file",
) -> tuple[str, str, str, str]:
    """
    解析文件内容并返回Markdown文本。

    功能:
        - 检查附件是否存在及是否被接受
        - 使用ExternalFileExtractor解析文件内容
        - 根据解析结果记录处理状态并返回相应的文本
    """
    metrics_enabled = bool(getattr(getattr(settings, "file_pipeline", None), "metrics_enabled", True))

    def _record(stage: str, status: str, provider: str = "none") -> None:
        """
        记录文件处理管道的指标。

        功能:
            - 如果指标记录功能启用，则记录处理阶段、状态和提供者
        """
        if not metrics_enabled:
            return
        record_file_pipeline(stage, status, provider)

    if not attachments:
        _record("extract", "skipped", "none")
        return "", "", "none", "no_attachment"

    attachment = attachments[0]
    if not attachment.accepted:
        _record("extract", "skipped", "none")
        reject_reason = str(attachment.reject_reason or "").strip()
        return "", build_file_unavailable_guidance(reject_reason), "none", reject_reason

    extractor = ExternalFileExtractor(
        settings=settings.file_extractor,
        timeout_seconds=int(settings.file_pipeline.timeout_seconds),
        ocr_settings=getattr(settings, "ocr", None),
        asr_settings=getattr(settings, "asr", None),
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
        return result.markdown, "", result.provider, ""

    _record("extract", _status_from_reason(result.reason), result.provider)
    reason = str(result.reason or "").strip()
    return "", build_file_unavailable_guidance(reason), result.provider, reason
