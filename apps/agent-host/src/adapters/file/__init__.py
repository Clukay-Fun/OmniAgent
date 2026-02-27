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

# region 数据结构定义
class ExtractorRequest:
    """
    文件提取请求的数据结构

    功能:
        - 定义提取请求所需的参数
    """
    pass

class ExtractorResult:
    """
    文件提取结果的数据结构

    功能:
        - 定义提取结果的返回格式
    """
    pass
# endregion

# region 外部文件提取器接口
class ExternalFileExtractor:
    """
    外部文件提取器的接口

    功能:
        - 提供提取文件的抽象方法
    """
    pass
# endregion
