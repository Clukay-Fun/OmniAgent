from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.file.extractor import ExternalFileExtractor, ExtractorRequest
from src.api.file_pipeline import build_file_unavailable_guidance
from src.config import ASRSettings, FileExtractorSettings, OCRSettings


def _build_extractor(mode: str, provider: str = "llm") -> ExternalFileExtractor:
    settings = FileExtractorSettings(
        enabled=True,
        provider=provider,
        api_key="k",
        api_base="https://doc.example.com",
        fail_open=True,
    )
    if mode == "ocr":
        return ExternalFileExtractor(
            settings=settings,
            ocr_settings=OCRSettings(enabled=True, provider=provider, api_key="k2", api_base="https://ocr.example.com"),
        )
    if mode == "asr":
        return ExternalFileExtractor(
            settings=settings,
            asr_settings=ASRSettings(enabled=True, provider=provider, api_key="k3", api_base="https://asr.example.com"),
        )
    return ExternalFileExtractor(settings=settings)


def _build_request(mode: str) -> ExtractorRequest:
    message_type = "file"
    source_url = "https://example.com/a.pdf"
    if mode == "ocr":
        message_type = "image"
        source_url = "https://example.com/a.png"
    elif mode == "asr":
        message_type = "audio"
        source_url = "https://example.com/a.mp3"
    return ExtractorRequest(file_key="f1", file_name="a", file_type="", source_url=source_url, message_type=message_type)


class _FakeResponse:
    def __init__(self, status_code: int, payload: object | None = None, text: str = "", headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _QueueClient:
    def __init__(self, queue: list[object], calls: list[int]) -> None:
        self._queue = queue
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False

    async def post(self, url, headers=None, json=None):
        del url, headers, json
        self._calls.append(1)
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.mark.parametrize("mode", ["extract", "ocr", "asr"])
@pytest.mark.parametrize("provider", ["mineru", "llm"])
def test_401_unauthorized_no_retry_immediate_degrade(monkeypatch, mode: str, provider: str) -> None:
    queue: list[object] = [_FakeResponse(401, payload={"error_code": "invalid_api_key"})]
    calls: list[int] = []

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _QueueClient(queue, calls))

    extractor = _build_extractor(mode=mode, provider=provider)
    result = asyncio.run(extractor.extract(_build_request(mode)))

    assert result.success is False
    assert result.reason == "extractor_auth_failed_fail_open"
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["extract", "ocr", "asr"])
def test_429_retry_after_respected_and_retry_succeeds(monkeypatch, mode: str, caplog) -> None:
    queue: list[object] = [
        _FakeResponse(429, payload={"error_code": "rate_limit"}, headers={"Retry-After": "1.5"}),
        _FakeResponse(200, payload={"text": "hello world"}),
    ]
    calls: list[int] = []
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _QueueClient(queue, calls))
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    extractor = _build_extractor(mode=mode, provider="llm")
    result = asyncio.run(extractor.extract(_build_request(mode)))

    assert result.success is True
    assert len(calls) == 2
    assert sleeps == [1.5]
    retry_logs = [r for r in caplog.records if getattr(r, "event_code", "") == "file.extractor.retry.rate_limited"]
    assert retry_logs
    assert getattr(retry_logs[0], "attempt", 0) == 1


def test_429_without_retry_after_uses_exponential_backoff_max_two_retries(monkeypatch) -> None:
    queue: list[object] = [
        _FakeResponse(429, payload={"error": "rate_limit"}),
        _FakeResponse(429, payload={"error": "rate_limit"}),
        _FakeResponse(429, payload={"error": "rate_limit"}),
    ]
    calls: list[int] = []
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _QueueClient(queue, calls))
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    extractor = _build_extractor(mode="extract", provider="llm")
    result = asyncio.run(extractor.extract(_build_request("extract")))

    assert result.success is False
    assert result.reason == "extractor_rate_limited_fail_open"
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]


@pytest.mark.parametrize("mode", ["extract", "ocr", "asr"])
def test_timeout_no_retry_and_degrade(monkeypatch, mode: str) -> None:
    queue: list[object] = [httpx.TimeoutException("timeout")]
    calls: list[int] = []

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _QueueClient(queue, calls))

    extractor = _build_extractor(mode=mode, provider="llm")
    result = asyncio.run(extractor.extract(_build_request(mode)))

    assert result.success is False
    assert result.reason == "extractor_timeout_fail_open"
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["extract", "ocr", "asr"])
def test_empty_content_degrades_without_context(monkeypatch, mode: str) -> None:
    queue: list[object] = [_FakeResponse(200, payload={})]
    calls: list[int] = []

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _QueueClient(queue, calls))

    extractor = _build_extractor(mode=mode, provider="llm")
    result = asyncio.run(extractor.extract(_build_request(mode)))

    assert result.success is False
    assert result.markdown == ""
    assert result.reason == "extractor_empty_content_fail_open"
    assert "未能识别内容" in build_file_unavailable_guidance(result.reason)


def test_malformed_response_degrades_without_crash(monkeypatch) -> None:
    queue: list[object] = [_FakeResponse(200, payload=ValueError("invalid"), text="<html>bad gateway</html>")]
    calls: list[int] = []

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _QueueClient(queue, calls))

    extractor = _build_extractor(mode="extract", provider="mineru")
    result = asyncio.run(extractor.extract(_build_request("extract")))

    assert result.success is False
    assert result.reason == "extractor_malformed_response_fail_open"
    assert len(calls) == 1


def test_provider_error_metric_records_provider_and_error_type(monkeypatch) -> None:
    queue: list[object] = [_FakeResponse(401, payload={"error_code": "invalid_api_key"})]
    calls: list[int] = []
    recorded: list[tuple[str, str]] = []

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _QueueClient(queue, calls))
    monkeypatch.setattr(
        "src.adapters.file.extractor.record_provider_error",
        lambda provider, error_type: recorded.append((provider, error_type)),
    )

    extractor = _build_extractor(mode="extract", provider="llm")
    result = asyncio.run(extractor.extract(_build_request("extract")))

    assert result.success is False
    assert recorded == [("llm", "extractor_auth_failed")]
