from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

import src.api.file_pipeline as file_pipeline_module
from src.api.inbound_normalizer import normalize_content
from src.core.orchestrator import AgentOrchestrator
import src.core.orchestrator as orchestrator_module


def test_normalizer_records_ingress_metrics_for_file_statuses(monkeypatch) -> None:
    recorded: list[tuple[str, str, str]] = []

    def _fake_record(stage: str, status: str, provider: str = "none") -> None:
        recorded.append((stage, status, provider))

    monkeypatch.setattr("src.api.inbound_normalizer.record_file_pipeline", _fake_record)

    accepted_payload = {
        "file_key": "f1",
        "file_name": "ok.pdf",
        "file_size": 10,
    }
    normalize_content(
        "file",
        json.dumps(accepted_payload),
        file_pipeline_enabled=True,
        max_file_bytes=100,
        metrics_enabled=True,
    )
    normalize_content(
        "file",
        json.dumps({"file_key": "f2", "file_name": "big.pdf", "file_size": 999}),
        file_pipeline_enabled=True,
        max_file_bytes=100,
        metrics_enabled=True,
    )

    assert ("ingress", "accepted", "none") in recorded
    assert ("ingress", "rejected", "none") in recorded


def test_resolve_file_markdown_records_extract_metrics(monkeypatch) -> None:
    recorded: list[tuple[str, str, str]] = []

    def _fake_record(stage: str, status: str, provider: str = "none") -> None:
        recorded.append((stage, status, provider))

    class _FakeExtractor:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        async def extract(self, request):
            del request
            return SimpleNamespace(success=True, markdown="# md", reason="", provider="mineru")

    monkeypatch.setattr(file_pipeline_module, "record_file_pipeline", _fake_record)
    monkeypatch.setattr(file_pipeline_module, "ExternalFileExtractor", _FakeExtractor)

    settings = SimpleNamespace(
        file_pipeline=SimpleNamespace(timeout_seconds=3, metrics_enabled=True),
        file_extractor=SimpleNamespace(),
        ocr=SimpleNamespace(),
    )
    attachment = SimpleNamespace(
        accepted=True,
        file_key="f1",
        file_name="a.pdf",
        file_type="pdf",
        source_url="https://example.com/a.pdf",
        reject_reason="",
    )

    markdown, guidance, provider, reason = asyncio.run(
        file_pipeline_module.resolve_file_markdown([attachment], settings=settings, message_type="file")
    )

    assert markdown == "# md"
    assert guidance == ""
    assert provider == "mineru"
    assert reason == ""
    assert ("extract", "success", "mineru") in recorded


def test_build_file_context_records_applied_and_truncated_metrics(monkeypatch) -> None:
    recorded: list[tuple[str, str, str]] = []

    def _fake_record(stage: str, status: str, provider: str = "none") -> None:
        recorded.append((stage, status, provider))

    monkeypatch.setattr(orchestrator_module, "record_file_pipeline", _fake_record)

    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._file_context_enabled = True
    orchestrator._file_context_max_chars = 8
    orchestrator._file_context_max_tokens = 99

    short_value = orchestrator._build_file_context(user_id="u1", file_markdown="hello", provider="llm")
    long_value = orchestrator._build_file_context(user_id="u1", file_markdown="0123456789ABCDEFGHIJKLMN", provider="llm")

    assert short_value == "hello"
    assert long_value.endswith("...")
    assert ("context", "applied", "llm") in recorded
    assert ("context", "truncated", "llm") in recorded


def test_file_pipeline_media_guidance_and_status_text() -> None:
    assert file_pipeline_module.build_processing_status_text("image").startswith("正在识别图片内容")
    assert file_pipeline_module.build_processing_status_text("audio").startswith("正在识别语音内容")
    assert "图片识别失败" in file_pipeline_module.build_file_unavailable_guidance("ocr_timeout")
    assert file_pipeline_module.build_file_unavailable_guidance("asr_unconfigured") == "语音识别失败，请发送文字。"
    assert "连接失败" in file_pipeline_module.build_file_unavailable_guidance("extractor_connect_failed")
    assert "网络异常" in file_pipeline_module.build_file_unavailable_guidance("extractor_network_error")
    assert "服务异常" in file_pipeline_module.build_file_unavailable_guidance("extractor_provider_error")
