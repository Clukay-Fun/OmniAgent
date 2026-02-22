from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from src.config import FileExtractorSettings, OCRSettings

logger = logging.getLogger(__name__)


@dataclass
class ExtractorRequest:
    file_key: str
    file_name: str
    file_type: str
    source_url: str
    message_type: str = "file"


@dataclass
class ExtractorResult:
    success: bool
    provider: str
    markdown: str = ""
    available: bool = False
    reason: str = ""


class ExternalFileExtractor:
    def __init__(
        self,
        settings: FileExtractorSettings,
        timeout_seconds: int = 12,
        ocr_settings: OCRSettings | None = None,
    ) -> None:
        self._settings = settings
        self._timeout_seconds = max(2, int(timeout_seconds))
        self._ocr = ocr_settings or OCRSettings()

    async def extract(self, request: ExtractorRequest) -> ExtractorResult:
        provider, mode = self._select_provider(request)
        if not self._settings.enabled or provider == "none":
            return ExtractorResult(
                success=False,
                available=False,
                provider=provider,
                reason="extractor_disabled",
            )

        api_key = str(self._settings.api_key or "").strip()
        api_base = str(self._settings.api_base or "").strip()
        reason_unconfigured = "extractor_unconfigured"
        if mode == "ocr":
            api_key = str(self._ocr.api_key or "").strip()
            api_base = str(self._ocr.api_base or "").strip()
            reason_unconfigured = "ocr_unconfigured"
        if not api_key or not api_base:
            return ExtractorResult(
                success=False,
                available=False,
                provider=provider,
                reason=reason_unconfigured,
            )

        start = time.perf_counter()
        try:
            markdown = await self._request_with_retry(
                provider=provider,
                request=request,
                api_base=api_base,
                api_key=api_key,
                mode=mode,
            )
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "文件转换成功",
                extra={
                    "event_code": "file.extractor.success",
                    "provider": provider,
                    "duration_ms": duration_ms,
                    "file_name": request.file_name,
                },
            )
            return ExtractorResult(success=True, available=True, provider=provider, markdown=markdown)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.warning(
                "文件转换失败: %s",
                exc,
                extra={
                    "event_code": "file.extractor.failed",
                    "provider": provider,
                    "duration_ms": duration_ms,
                },
            )
            if self._settings.fail_open:
                return ExtractorResult(
                    success=False,
                    available=False,
                    provider=provider,
                    reason="extractor_failed_fail_open",
                )
            return ExtractorResult(
                success=False,
                available=True,
                provider=provider,
                reason="extractor_failed",
            )

    async def _request_with_retry(
        self,
        provider: str,
        request: ExtractorRequest,
        api_base: str,
        api_key: str,
        mode: str,
    ) -> str:
        last_error: Exception | None = None
        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(
                    self._convert_once(
                        provider=provider,
                        request=request,
                        api_base=api_base,
                        api_key=api_key,
                        mode=mode,
                    ),
                    timeout=float(self._timeout_seconds),
                )
            except Exception as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                await asyncio.sleep(0.2 * attempt)
        assert last_error is not None
        raise last_error

    async def _convert_once(
        self,
        provider: str,
        request: ExtractorRequest,
        api_base: str,
        api_key: str,
        mode: str,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_payload(request, mode=mode)
        timeout = httpx.Timeout(self._timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            if provider == "mineru":
                response = await client.post(f"{api_base.rstrip('/')}/v1/convert", headers=headers, json=payload)
            elif provider == "llm":
                response = await client.post(f"{api_base.rstrip('/')}/v1/document/convert", headers=headers, json=payload)
            else:
                raise ValueError(f"unsupported_provider:{provider}")

        response.raise_for_status()
        data = response.json()
        markdown = self._extract_markdown(data)
        if markdown:
            return markdown
        raise ValueError("empty_markdown")

    def _build_payload(self, request: ExtractorRequest, mode: str) -> dict[str, Any]:
        payload = {
            "source_url": request.source_url,
            "file_key": request.file_key,
            "file_name": request.file_name,
            "file_type": request.file_type,
            "target_format": "markdown",
        }
        if mode == "ocr":
            payload["mode"] = "ocr"
        return payload

    def _extract_markdown(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        direct = payload.get("markdown")
        if isinstance(direct, str):
            return direct.strip()
        data = payload.get("data")
        if isinstance(data, dict):
            nested = data.get("markdown")
            if isinstance(nested, str):
                return nested.strip()
        return ""

    def _normalize_provider(self, provider: str | None) -> str:
        normalized = str(provider or "none").strip().lower()
        if normalized not in {"none", "mineru", "llm"}:
            return "none"
        return normalized

    def _select_provider(self, request: ExtractorRequest) -> tuple[str, str]:
        message_type = str(getattr(request, "message_type", "") or "").strip().lower()
        if message_type == "image" and bool(getattr(self._ocr, "enabled", False)):
            provider = self._normalize_provider(getattr(self._ocr, "provider", "none"))
            if provider != "none":
                return provider, "ocr"
        return self._normalize_provider(self._settings.provider), "extract"
