"""
描述: 环境变量配置
主要功能:
    - 读取服务与模型配置
    - 提供集中化配置访问
依赖: 标准库
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class ServiceSettings:
    """
    服务配置
    """

    database_url: str
    fastapi_host: str
    fastapi_port: int
    log_level: str


@dataclass(frozen=True)
class ModelSettings:
    """
    模型配置
    """

    api_base_url: str
    api_key: str
    embedding_model: str
    reranker_model: str
    json_extract_model: str
    doc_structure_model: str
    vision_model: str
    reasoning_model: str
    chat_model: str


@dataclass(frozen=True)
class AppSettings:
    """
    全局配置
    """

    service: ServiceSettings
    model: ModelSettings


# ============================================
# region _get_env
# ============================================
def _get_env(key: str, default: str = "") -> str:
    value = os.getenv(key, default)
    return value
# endregion
# ============================================


# ============================================
# region get_settings
# ============================================
@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """
    获取应用配置

    返回:
        配置对象
    """

    service = ServiceSettings(
        database_url=_get_env("DATABASE_URL"),
        fastapi_host=_get_env("FASTAPI_HOST", "0.0.0.0"),
        fastapi_port=int(_get_env("FASTAPI_PORT", "8001")),
        log_level=_get_env("LOG_LEVEL", "INFO"),
    )

    model = ModelSettings(
        api_base_url=_get_env("MODEL_API_BASE_URL"),
        api_key=_get_env("MODEL_API_KEY"),
        embedding_model=_get_env("EMBEDDING_MODEL", "BAAI/bge-m3"),
        reranker_model=_get_env("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
        json_extract_model=_get_env("JSON_EXTRACT_MODEL", "Qwen3-8B"),
        doc_structure_model=_get_env("DOC_STRUCTURE_MODEL", "Qwen3-8B"),
        vision_model=_get_env("VISION_MODEL", "GLM-4.1V-Thinking"),
        reasoning_model=_get_env("REASONING_MODEL", "DeepSeek-R1-0528-Qwen3-8B"),
        chat_model=_get_env("CHAT_MODEL", "internlm/internlm2_5-7b-chat"),
    )

    if not service.database_url:
        raise RuntimeError("DATABASE_URL is required")
    if not model.api_base_url:
        raise RuntimeError("MODEL_API_BASE_URL is required")
    if not model.api_key:
        raise RuntimeError("MODEL_API_KEY is required")

    return AppSettings(service=service, model=model)
# endregion
# ============================================
