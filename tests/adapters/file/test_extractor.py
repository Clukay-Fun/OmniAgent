import asyncio
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.file.extractor import ExternalFileExtractor, ExtractorRequest
from src.config import FileExtractorSettings, OCRSettings


def test_extractor_returns_unavailable_when_missing_credentials() -> None:
    extractor = ExternalFileExtractor(
        settings=FileExtractorSettings(
            enabled=True,
            provider="mineru",
            api_key="",
            api_base="",
            fail_open=True,
        ),
    )

    result = asyncio.run(
        extractor.extract(
            ExtractorRequest(file_key="f", file_name="a.pdf", file_type="pdf", source_url="https://example.com/a.pdf")
        )
    )

    assert result.success is False
    assert result.available is False
    assert result.reason == "extractor_unconfigured"


def test_extractor_normalizes_unknown_provider_to_none() -> None:
    extractor = ExternalFileExtractor(
        settings=FileExtractorSettings(enabled=True, provider="unknown", api_key="k", api_base="https://x", fail_open=True),
    )

    result = asyncio.run(
        extractor.extract(
            ExtractorRequest(file_key="f", file_name="a.pdf", file_type="pdf", source_url="https://example.com/a.pdf")
        )
    )

    assert result.success is False
    assert result.provider == "none"
    assert result.reason == "extractor_disabled"


def test_extractor_uses_ocr_provider_for_image_and_missing_ocr_creds() -> None:
    extractor = ExternalFileExtractor(
        settings=FileExtractorSettings(enabled=True, provider="mineru", api_key="k", api_base="https://x", fail_open=True),
        ocr_settings=OCRSettings(enabled=True, provider="llm", api_key="", api_base=""),
    )

    result = asyncio.run(
        extractor.extract(
            ExtractorRequest(file_key="img1", file_name="", file_type="", source_url="https://example.com/img.png", message_type="image")
        )
    )

    assert result.success is False
    assert result.provider == "llm"
    assert result.reason == "ocr_unconfigured"


def test_extractor_falls_back_to_file_provider_when_ocr_disabled() -> None:
    extractor = ExternalFileExtractor(
        settings=FileExtractorSettings(enabled=True, provider="mineru", api_key="k", api_base="https://x", fail_open=True),
        ocr_settings=OCRSettings(enabled=False, provider="llm", api_key="", api_base=""),
    )

    result = asyncio.run(
        extractor.extract(
            ExtractorRequest(file_key="img1", file_name="", file_type="", source_url="https://example.com/img.png", message_type="image")
        )
    )

    assert result.provider == "mineru"
