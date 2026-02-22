from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.config import load_settings


def test_file_pipeline_defaults_safe() -> None:
    settings = load_settings(config_path="/path/not/exists/config.yaml")

    assert settings.file_pipeline.enabled is False
    assert settings.file_pipeline.metrics_enabled is True
    assert settings.file_extractor.enabled is False
    assert settings.file_extractor.provider == "none"
    assert settings.file_extractor.mineru_path == "/v1/convert"
    assert settings.file_extractor.llm_path == "/v1/document/convert"
    assert settings.file_extractor.auth_style == "bearer"
    assert settings.file_extractor.fail_open is True
    assert settings.file_context.injection_enabled is False
    assert settings.usage_log.enabled is False
    assert settings.usage_log.fail_open is True
    assert settings.usage_log.model_pricing_path == ""
    assert settings.usage_log.model_pricing_json == ""
    assert settings.ab_routing.enabled is False
    assert settings.ab_routing.ratio == 0.0
    assert settings.ocr.enabled is False
    assert settings.ocr.provider == "none"
    assert settings.ocr.mineru_path == "/v1/convert"
    assert settings.ocr.llm_path == "/v1/document/convert"


def test_file_pipeline_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("FILE_PIPELINE_ENABLED", "true")
    monkeypatch.setenv("FILE_PIPELINE_MAX_BYTES", "4096")
    monkeypatch.setenv("FILE_PIPELINE_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("FILE_PIPELINE_METRICS_ENABLED", "false")
    monkeypatch.setenv("FILE_EXTRACTOR_ENABLED", "true")
    monkeypatch.setenv("FILE_EXTRACTOR_PROVIDER", "mineru")
    monkeypatch.setenv("FILE_EXTRACTOR_API_KEY", "k")
    monkeypatch.setenv("FILE_EXTRACTOR_API_BASE", "https://api.example.com")
    monkeypatch.setenv("FILE_EXTRACTOR_MINERU_PATH", "/mineru/convert")
    monkeypatch.setenv("FILE_EXTRACTOR_LLM_PATH", "/llm/convert")
    monkeypatch.setenv("FILE_EXTRACTOR_AUTH_STYLE", "x_api_key")
    monkeypatch.setenv("FILE_EXTRACTOR_API_KEY_HEADER", "X-MinerU-Key")
    monkeypatch.setenv("FILE_EXTRACTOR_API_KEY_PREFIX", "Token ")
    monkeypatch.setenv("FILE_CONTEXT_INJECTION_ENABLED", "true")
    monkeypatch.setenv("FILE_CONTEXT_MAX_CHARS", "999")
    monkeypatch.setenv("FILE_CONTEXT_MAX_TOKENS", "111")
    monkeypatch.setenv("USAGE_LOG_ENABLED", "true")
    monkeypatch.setenv("USAGE_LOG_PATH", "workspace/usage/custom-{date}.jsonl")
    monkeypatch.setenv("USAGE_LOG_FAIL_OPEN", "false")
    monkeypatch.setenv("USAGE_MODEL_PRICING_PATH", "config/model_pricing.yaml")
    monkeypatch.setenv("USAGE_MODEL_PRICING_JSON", '{"models":{"m":{"per_1k":0.1}}}')
    monkeypatch.setenv("AB_ROUTING_ENABLED", "true")
    monkeypatch.setenv("AB_ROUTING_RATIO", "0.2")
    monkeypatch.setenv("AB_ROUTING_MODEL_A", "model-small")
    monkeypatch.setenv("AB_ROUTING_MODEL_B", "model-large")
    monkeypatch.setenv("OCR_ENABLED", "true")
    monkeypatch.setenv("OCR_PROVIDER", "mineru")
    monkeypatch.setenv("OCR_API_KEY", "ocr-key")
    monkeypatch.setenv("OCR_API_BASE", "https://ocr.example.com")
    monkeypatch.setenv("OCR_MINERU_PATH", "/ocr/mineru")
    monkeypatch.setenv("OCR_LLM_PATH", "/ocr/llm")
    monkeypatch.setenv("OCR_AUTH_STYLE", "x_api_key")
    monkeypatch.setenv("OCR_API_KEY_HEADER", "X-OCR-Key")
    monkeypatch.setenv("OCR_API_KEY_PREFIX", "Token ")

    settings = load_settings(config_path="/path/not/exists/config.yaml")

    assert settings.file_pipeline.enabled is True
    assert settings.file_pipeline.max_bytes == 4096
    assert settings.file_pipeline.timeout_seconds == 7
    assert settings.file_pipeline.metrics_enabled is False
    assert settings.file_extractor.enabled is True
    assert settings.file_extractor.provider == "mineru"
    assert settings.file_extractor.mineru_path == "/mineru/convert"
    assert settings.file_extractor.llm_path == "/llm/convert"
    assert settings.file_extractor.auth_style == "x_api_key"
    assert settings.file_extractor.api_key_header == "X-MinerU-Key"
    assert settings.file_extractor.api_key_prefix == "Token "
    assert settings.file_context.injection_enabled is True
    assert settings.file_context.max_chars == 999
    assert settings.file_context.max_tokens == 111
    assert settings.usage_log.enabled is True
    assert settings.usage_log.path == "workspace/usage/custom-{date}.jsonl"
    assert settings.usage_log.fail_open is False
    assert settings.usage_log.model_pricing_path == "config/model_pricing.yaml"
    assert settings.usage_log.model_pricing_json == '{"models":{"m":{"per_1k":0.1}}}'
    assert settings.ab_routing.enabled is True
    assert settings.ab_routing.ratio == 0.2
    assert settings.ab_routing.model_a == "model-small"
    assert settings.ab_routing.model_b == "model-large"
    assert settings.ocr.enabled is True
    assert settings.ocr.provider == "mineru"
    assert settings.ocr.api_key == "ocr-key"
    assert settings.ocr.api_base == "https://ocr.example.com"
    assert settings.ocr.mineru_path == "/ocr/mineru"
    assert settings.ocr.llm_path == "/ocr/llm"
    assert settings.ocr.auth_style == "x_api_key"
    assert settings.ocr.api_key_header == "X-OCR-Key"
    assert settings.ocr.api_key_prefix == "Token "
