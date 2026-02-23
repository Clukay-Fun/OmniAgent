from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from src.adapters.file.providers import ASRProvider, OCRProvider
from src.config import ASRSettings, FileExtractorSettings, OCRSettings
from src.core.cost_monitor import get_cost_monitor
from src.utils.metrics import observe_file_extractor_duration, record_provider_error

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


@dataclass
class _ProviderError(Exception):
    error_type: str
    retryable: bool = False
    retry_after_seconds: float = 0.0


class ExternalFileExtractor:
    def __init__(
        self,
        settings: FileExtractorSettings,
        timeout_seconds: int = 12,
        ocr_settings: OCRSettings | None = None,
        asr_settings: ASRSettings | None = None,
    ) -> None:
        self._settings = settings
        self._timeout_seconds = max(2, int(timeout_seconds))
        self._ocr = ocr_settings or OCRSettings()
        self._asr = asr_settings or ASRSettings()
        self._ocr_provider = OCRProvider()
        self._asr_provider = ASRProvider()

    async def extract(self, request: ExtractorRequest) -> ExtractorResult:
        provider, mode = self._select_provider(request)
        cost_monitor = get_cost_monitor()
        if cost_monitor is not None:
            operation = "file_convert"
            if mode == "ocr":
                operation = "ocr"
            elif mode == "asr":
                operation = "asr"
            allowed, guidance = cost_monitor.check_call_allowed(operation)
            if not allowed:
                logger.warning(
                    "文件解析调用被成本熔断拦截",
                    extra={
                        "event_code": "file.extractor.circuit_breaker.blocked",
                        "provider": provider,
                        "operation": operation,
                    },
                )
                return ExtractorResult(
                    success=False,
                    available=False,
                    provider=provider,
                    reason="cost_circuit_breaker_open",
                    markdown=guidance,
                )
        if not self._settings.enabled or provider == "none":
            disabled_reason = "extractor_disabled"
            if mode == "ocr":
                disabled_reason = "ocr_disabled"
            elif mode == "asr":
                disabled_reason = "asr_disabled"
            return ExtractorResult(
                success=False,
                available=False,
                provider=provider,
                reason=disabled_reason,
            )

        api_key = str(self._settings.api_key or "").strip()
        api_base = str(self._settings.api_base or "").strip()
        reason_unconfigured = "extractor_unconfigured"
        if mode == "ocr":
            api_key = str(self._ocr.api_key or "").strip()
            api_base = str(self._ocr.api_base or "").strip()
            reason_unconfigured = "ocr_unconfigured"
        if mode == "asr":
            api_key = str(self._asr.api_key or "").strip()
            api_base = str(self._asr.api_base or "").strip()
            reason_unconfigured = "asr_unconfigured"
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
            if mode == "ocr":
                markdown = self._ocr_provider.to_context_markdown(markdown)
                if not markdown:
                    raise ValueError("ocr_empty_text")
            elif mode == "asr":
                markdown = self._asr_provider.to_transcript(markdown)
                if not markdown:
                    raise ValueError("asr_empty_transcript")
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            observe_file_extractor_duration(provider, float(duration_ms) / 1000.0)
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
            observe_file_extractor_duration(provider, float(duration_ms) / 1000.0)
            reason = str(exc).strip() or "extractor_failed"
            error_type = reason
            if reason.endswith("_fail_open"):
                error_type = reason[: -len("_fail_open")]
            record_provider_error(provider=provider, error_type=error_type)
            logger.warning(
                "文件转换失败: %s",
                exc,
                extra={
                    "event_code": "file.extractor.failed",
                    "provider": provider,
                    "duration_ms": duration_ms,
                    "reason": reason,
                },
            )
            if self._settings.fail_open:
                return ExtractorResult(
                    success=False,
                    available=False,
                    provider=provider,
                    reason=f"{reason}_fail_open",
                )
            return ExtractorResult(
                success=False,
                available=True,
                provider=provider,
                reason=reason,
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
        attempt = 0
        max_retries = 2
        while True:
            attempt += 1
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
            except asyncio.TimeoutError:
                last_error = ValueError("extractor_timeout")
                break
            except _ProviderError as exc:
                last_error = ValueError(exc.error_type)
                if not exc.retryable or attempt > max_retries:
                    break
                delay_seconds = max(0.0, float(exc.retry_after_seconds))
                if delay_seconds <= 0:
                    delay_seconds = 0.5 * (2 ** (attempt - 1))
                logger.warning(
                    "文件转换请求触发限流重试",
                    extra={
                        "event_code": "file.extractor.retry.rate_limited",
                        "provider": provider,
                        "attempt": attempt,
                        "delay_seconds": delay_seconds,
                    },
                )
                await asyncio.sleep(delay_seconds)
            except Exception as exc:
                if isinstance(exc, httpx.TimeoutException):
                    exc = ValueError("extractor_timeout")
                    last_error = exc
                    break
                elif isinstance(exc, httpx.ConnectError):
                    exc = ValueError("extractor_connect_failed")
                elif isinstance(exc, httpx.NetworkError):
                    exc = ValueError("extractor_network_error")
                last_error = exc
                if attempt >= max_retries + 1:
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
        headers = self._build_headers(api_key=api_key, mode=mode)
        payload = self._build_payload(request, mode=mode, provider=provider)
        endpoint = self._resolve_endpoint(provider=provider, api_base=api_base, mode=mode)
        timeout = httpx.Timeout(self._timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            if provider not in {"mineru", "llm"}:
                raise ValueError(f"unsupported_provider:{provider}")
            response = await client.post(endpoint, headers=headers, json=payload)

        data = self._safe_json(response)
        mapped_error = self._map_http_error(response=response, payload=data)
        if mapped_error:
            if mapped_error == "extractor_rate_limited":
                retry_after = self._parse_retry_after_seconds(response)
                raise _ProviderError(
                    error_type=mapped_error,
                    retryable=True,
                    retry_after_seconds=retry_after,
                )
            raise _ProviderError(error_type=mapped_error, retryable=False)

        markdown = self._extract_markdown(data)
        if markdown:
            return markdown
        raise _ProviderError(error_type="extractor_empty_content", retryable=False)

    def _build_payload(self, request: ExtractorRequest, mode: str, provider: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if provider == "mineru" and mode not in ("ocr", "asr"):
            # Official MinerU v4 Extract API format
            payload["url"] = request.source_url
            payload["model_version"] = "vlm"
        else:
            payload = {
                "source_url": request.source_url,
                "file_key": request.file_key,
                "file_name": request.file_name,
                "file_type": request.file_type,
                "target_format": "markdown",
            }
            if mode == "ocr":
                payload["mode"] = "ocr"
            if mode == "asr":
                payload["mode"] = "asr"
                payload["target_format"] = "text"
        return payload

    def _resolve_endpoint(self, provider: str, api_base: str, mode: str) -> str:
        source = self._settings
        if mode == "ocr":
            source = self._ocr
        if mode == "asr":
            source = self._asr
        default_path = "/v1/convert" if provider == "mineru" else "/v1/document/convert"
        path_attr = "mineru_path" if provider == "mineru" else "llm_path"
        configured_path = str(getattr(source, path_attr, "") or "").strip()
        path = configured_path or default_path
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{api_base.rstrip('/')}{normalized_path}"

    def _build_headers(self, api_key: str, mode: str) -> dict[str, str]:
        source = self._settings
        if mode == "ocr":
            source = self._ocr
        if mode == "asr":
            source = self._asr
        auth_style = str(getattr(source, "auth_style", "bearer") or "bearer").strip().lower()
        api_key_header = str(getattr(source, "api_key_header", "X-API-Key") or "X-API-Key").strip() or "X-API-Key"
        api_key_prefix = str(getattr(source, "api_key_prefix", "Bearer ") or "Bearer ")
        headers = {
            "Content-Type": "application/json",
        }
        if auth_style == "none":
            return headers
        if auth_style == "x_api_key":
            headers[api_key_header] = api_key
            return headers
        if api_key_prefix and not api_key_prefix.endswith(" "):
            api_key_prefix = f"{api_key_prefix} "
        headers["Authorization"] = f"{api_key_prefix}{api_key}"
        return headers

    def _extract_markdown(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload.strip()
        if isinstance(payload, list):
            for item in payload:
                parsed = self._extract_markdown(item)
                if parsed:
                    return parsed
            return ""
        if not isinstance(payload, dict):
            return ""

        direct_keys = (
            "transcript",
            "transcribed_text",
            "markdown",
            "md",
            "text_markdown",
            "content_markdown",
            "content",
            "text",
            "full_text",
            "result_markdown",
            "output_markdown",
        )
        for key in direct_keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        for key in ("data", "result", "output", "document", "response", "message"):
            nested = payload.get(key)
            parsed = self._extract_markdown(nested)
            if parsed:
                return parsed

        choices = payload.get("choices")
        parsed = self._extract_markdown(choices)
        if parsed:
            return parsed

        llm_message = payload.get("message")
        if isinstance(llm_message, dict):
            parsed = self._extract_markdown(llm_message.get("content"))
            if parsed:
                return parsed
        return ""

    def _safe_json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception:
            text = str(response.text or "").strip()
            if not text:
                return {}
            try:
                return json.loads(text)
            except Exception:
                return {"_malformed_response": True, "raw_text": text}

    def _map_http_error(self, response: httpx.Response, payload: Any) -> str:
        status = int(response.status_code)
        if isinstance(payload, dict) and bool(payload.get("_malformed_response")):
            return "extractor_malformed_response"
        provider_code = self._extract_provider_error_code(payload)
        if provider_code in {"invalid_api_key", "unauthorized", "auth_failed"}:
            return "extractor_auth_failed"
        if provider_code in {"rate_limit", "too_many_requests"}:
            return "extractor_rate_limited"
        if provider_code in {"timeout", "request_timeout"}:
            return "extractor_timeout"
        if provider_code in {"unsupported_format", "unsupported_file_type"}:
            return "unsupported_file_type"

        if status < 400:
            return ""
        if status in {401, 403}:
            return "extractor_auth_failed"
        if status == 404:
            return "extractor_endpoint_not_found"
        if status == 429:
            return "extractor_rate_limited"
        if status in {408, 504}:
            return "extractor_timeout"
        if status >= 500:
            return "extractor_provider_error"
        if status == 400:
            return "extractor_bad_request"
        return "extractor_http_error"

    def _parse_retry_after_seconds(self, response: httpx.Response) -> float:
        raw_value = ""
        try:
            raw_value = str(response.headers.get("Retry-After") or "").strip()
        except Exception:
            raw_value = ""
        if not raw_value:
            return 0.0
        try:
            return max(0.0, float(raw_value))
        except Exception:
            return 0.0

    def _extract_provider_error_code(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        candidates = (
            payload.get("code"),
            payload.get("error_code"),
            payload.get("error"),
            payload.get("status"),
        )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip().lower()
            if isinstance(candidate, dict):
                nested_code = candidate.get("code")
                if isinstance(nested_code, str) and nested_code.strip():
                    return nested_code.strip().lower()
        return ""

    def _normalize_provider(self, provider: str | None) -> str:
        normalized = str(provider or "none").strip().lower()
        if normalized not in {"none", "mineru", "llm"}:
            return "none"
        return normalized

    def _select_provider(self, request: ExtractorRequest) -> tuple[str, str]:
        message_type = str(getattr(request, "message_type", "") or "").strip().lower()
        if message_type == "audio" and bool(getattr(self._asr, "enabled", False)):
            provider = self._normalize_provider(getattr(self._asr, "provider", "none"))
            if provider != "none":
                return provider, "asr"
        if message_type == "image" and bool(getattr(self._ocr, "enabled", False)):
            provider = self._normalize_provider(getattr(self._ocr, "provider", "none"))
            if provider != "none":
                return provider, "ocr"
        return self._normalize_provider(self._settings.provider), "extract"
