"""
描述: 该模块提供了一个自动语音识别（ASR）结果的文本处理工具。
主要功能:
    - 提供将原始文本转换为标准化转录的功能
    - 确保转录文本不超过指定的最大字符数
"""

class ASRProvider:
    """
    自动语音识别结果的文本处理工具

    功能:
        - 将原始文本转换为标准化转录
        - 确保转录文本不超过指定的最大字符数
    """
    def to_transcript(self, raw_text: str, max_chars: int = 4000) -> str:
        """
        将原始文本转换为标准化转录，并确保不超过最大字符数

        功能:
            - 去除原始文本的前后空白字符
            - 将文本中的多个连续空格合并为一个空格
            - 如果文本长度超过最大字符数，则截取到最大字符数并去除末尾空白字符
        """
        text = str(raw_text or "").strip()
        if not text:
            return ""
        normalized = " ".join(text.split())
        limit = max(40, int(max_chars))
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip()
