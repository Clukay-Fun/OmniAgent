"""
描述: 提供文件处理相关的适配器，包括语音识别和光学字符识别功能
主要功能:
    - 提供语音识别适配器 (ASRProvider)
    - 提供光学字符识别适配器 (OCRProvider)
"""

from __future__ import annotations

from src.adapters.file.providers.asr import ASRProvider
from src.adapters.file.providers.ocr import OCRProvider

__all__ = ["ASRProvider", "OCRProvider"]

# region 类定义
class ASRProvider:
    """
    语音识别适配器

    功能:
        - 提供语音转文本的功能
    """

class OCRProvider:
    """
    光学字符识别适配器

    功能:
        - 提供图像文本提取的功能
    """
# endregion

# region 导出配置
__all__ = ["ASRProvider", "OCRProvider"]
# endregion
