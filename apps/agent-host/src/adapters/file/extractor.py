"""
描述: 提供外部文件提取功能，支持OCR和ASR服务
主要功能:
    - 根据文件类型选择合适的OCR或ASR服务进行文件内容提取
    - 处理请求重试、错误记录和成本监控
"""

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
    """
    外部文件提取器类，负责处理文件内容的提取工作

    功能:
        - 初始化提取器设置和相关服务提供者
        - 提供文件内容提取的主要接口
        - 处理请求重试、错误记录和成本监控
    """

    def __init__(
        self,
        settings: FileExtractorSettings,
        timeout_seconds: int = 12,
        ocr_settings: OCRSettings | None = None,
        asr_settings: ASRSettings | None = None,
    ) -> None:
        """
        初始化外部文件提取器

        功能:
            - 设置提取器的基本配置
            - 初始化OCR和ASR服务提供者
        """
        self._settings = settings
        self._timeout_seconds = max(2, int(timeout_seconds))
        self._ocr = ocr_settings or OCRSettings()
        self._asr = asr_settings or ASRSettings()
        self._ocr_provider = OCRProvider()
        self._asr_provider = ASRProvider()

    async def extract(self, request: ExtractorRequest) -> ExtractorResult:
        """
        提取文件内容

        功能:
            - 选择合适的OCR或ASR服务
            - 检查成本监控状态
            - 发送请求并处理响应
            - 记录操作时间和错误信息
        """
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
        """
        发送请求并处理重试逻辑

        功能:
            - 尝试发送请求并处理超时和错误
            - 根据错误类型决定是否重试
        """
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
        """
        发送单次请求并处理响应

        功能:
            - 构建请求头和负载
            - 发送HTTP请求并处理响应
            - 解析响应内容并处理错误
            - 对 MinerU 异步任务支持轮询逻辑
        """
        headers = self._build_headers(api_key=api_key, mode=mode)
        payload = self._build_payload(request, mode=mode, provider=provider)
        endpoint = self._resolve_endpoint(provider=provider, api_base=api_base, mode=mode)
        timeout = httpx.Timeout(self._timeout_seconds)
        
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            if provider not in {"mineru", "llm"}:
                raise ValueError(f"unsupported_provider:{provider}")
            
            # 1. 提交抽取任务
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

            # 如果不是 mineru，直接提取 Markdown
            if provider != "mineru":
                markdown = self._extract_markdown(data)
                if markdown:
                    return markdown
                raise _ProviderError(error_type="extractor_empty_content", retryable=False)

            # 2. 如果是 mineru，需要轮询任务状态
            task_id = ""
            if isinstance(data, dict):
                task_id_info = data.get("data")
                if isinstance(task_id_info, dict):
                    task_id = task_id_info.get("task_id", "")
                elif isinstance(data.get("task_id"), str):
                    task_id = data.get("task_id", "")
            
            if not task_id:
                raise _ProviderError(error_type="extractor_missing_task_id", retryable=False)
            
            # 构建轮询 URL: https://mineru.net/api/v4/extract/task/{task_id}
            poll_endpoint = f"{endpoint.rstrip('/')}/{task_id}"
            
            max_polls = max(1, self._timeout_seconds // 2)
            logger.info(f"MinerU task submitted: {task_id}. Polling {poll_endpoint} (max {max_polls} attempts).")
            for i in range(max_polls):
                await asyncio.sleep(2.0)
                poll_resp = await client.get(poll_endpoint, headers=headers)
                poll_data = self._safe_json(poll_resp)
                logger.debug(f"MinerU poll {i+1}/{max_polls}: status={poll_resp.status_code}")
                
                poll_mapped_error = self._map_http_error(response=poll_resp, payload=poll_data)
                if poll_mapped_error:
                    if poll_mapped_error == "extractor_rate_limited":
                        retry_after = self._parse_retry_after_seconds(poll_resp)
                        raise _ProviderError(
                            error_type=poll_mapped_error,
                            retryable=True,
                            retry_after_seconds=retry_after,
                        )
                    raise _ProviderError(error_type=poll_mapped_error, retryable=False)
                
                if isinstance(poll_data, dict) and isinstance(poll_data.get("data"), dict):
                    poll_data_inner = poll_data.get("data", {})
                    # MinerU uses 'state' for the task state
                    status = str(poll_data_inner.get("state", "")).strip().lower()
                    
                    if status == "done":
                        # 3. 轮询成功，提取 Markdown
                        # MinerU v4 返回的内容通常在 data -> extra_info -> markdown，或类似层级
                        # 根据测试，返回格式包含 'full_zip_url'
                        zip_url = poll_data_inner.get("full_zip_url", "")
                        if zip_url:
                            import zipfile
                            import io
                            try:
                                import subprocess

                                def download_zip():
                                    # Python's OpenSSL may be incompatible with some CDN TLS configs.
                                    # System curl (SecureTransport/LibreSSL) handles it reliably.
                                    result = subprocess.run(
                                        ["curl", "-sS", "-L", "-k", "--connect-timeout", "15",
                                         "--max-time", "60", zip_url],
                                        capture_output=True,
                                        timeout=90
                                    )
                                    if result.returncode != 0:
                                        stderr = result.stderr.decode("utf-8", errors="replace").strip()
                                        raise RuntimeError(f"curl failed (exit {result.returncode}): {stderr}")
                                    if not result.stdout:
                                        raise RuntimeError("curl returned empty response")
                                    return result.stdout

                                zip_content = await asyncio.to_thread(download_zip)
                                with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
                                    md_files = [f for f in z.namelist() if f.endswith('.md')]
                                    if md_files:
                                        # 读取找到的第一个 md 文件
                                        with z.open(md_files[0]) as f:
                                            return f.read().decode('utf-8')
                            except Exception as e:
                                import traceback
                                logger.error(f"Failed to download or extract mineru zip: {e}\n{traceback.format_exc()}")
                                raise _ProviderError(error_type="extractor_provider_error", retryable=False)
                        
                        markdown = self._extract_markdown(poll_data)
                        if markdown:
                            return markdown
                        raise _ProviderError(error_type="extractor_empty_content", retryable=False)
                    elif status == "failed" or status == "error":
                        raise _ProviderError(error_type="extractor_provider_error", retryable=False)
                    # 如果是 processing / pending 等状态，继续轮询
            
            # 超出轮询次数
            raise _ProviderError(error_type="extractor_timeout", retryable=True)

    def _build_payload(self, request: ExtractorRequest, mode: str, provider: str = "") -> dict[str, Any]:
        """
        构建请求负载

        功能:
            - 根据请求和模式构建请求负载
        """
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
        """
        解析请求端点

        功能:
            - 根据提供者和模式解析请求端点
        """
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
        """
        构建请求头

        功能:
            - 根据API密钥和模式构建请求头
        """
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
        """
        从响应中提取Markdown内容

        功能:
            - 递归解析响应内容以提取Markdown文本
        """
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
        """
        安全解析JSON响应

        功能:
            - 尝试解析响应为JSON，处理解析错误
        """
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
        """
        映射HTTP错误到内部错误类型

        功能:
            - 根据响应状态码和负载映射错误类型
        """
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
        """
        解析重试时间

        功能:
            - 从响应头中解析重试时间
        """
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
        """
        提取提供者错误代码

        功能:
            - 从响应负载中提取错误代码
        """
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
        """
        规范化提供者名称

        功能:
            - 将提供者名称转换为标准格式
        """
        normalized = str(provider or "none").strip().lower()
        if normalized not in {"none", "mineru", "llm"}:
            return "none"
        return normalized

    def _select_provider(self, request: ExtractorRequest) -> tuple[str, str]:
        """
        选择合适的提供者

        功能:
            - 根据请求类型选择合适的OCR或ASR提供者
        """
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
