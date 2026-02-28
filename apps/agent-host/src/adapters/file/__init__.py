"""
描述: 提供文件提取功能的适配器模块
主要功能:
    - 定义文件提取请求和结果的数据结构
    - 提供外部文件提取器的接口
"""

from __future__ import annotations

from src.adapters.file.extractor import (
    ExtractorRequest,
    ExtractorResult,
    ExternalFileExtractor,
)

__all__ = [
    "ExtractorRequest",
    "ExtractorResult",
    "ExternalFileExtractor",
]
