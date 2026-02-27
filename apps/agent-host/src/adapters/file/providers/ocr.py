"""
描述: 提供OCR识别结果的格式化功能
主要功能:
    - 将原始文本格式化为Markdown格式
    - 构建简短的识别结果文本
"""

class OCRProvider:
    """
    OCR结果格式化工具

    功能:
        - 将原始文本转换为Markdown格式
        - 生成简短的识别结果文本
    """

    def to_context_markdown(self, raw_text: str) -> str:
        """
        将原始文本格式化为Markdown格式

        功能:
            - 去除原始文本的前后空白字符
            - 如果文本为空，返回空字符串
            - 否则，返回格式化的Markdown字符串
        """
        text = str(raw_text or "").strip()
        if not text:
            return ""
        return f"## 图片识别结果\n\n{text}"

    def build_completion_text(self, raw_text: str, max_chars: int = 140) -> str:
        """
        构建简短的识别结果文本

        功能:
            - 去除原始文本的前后空白字符
            - 如果文本为空，返回提示信息
            - 去除Markdown标题（如果存在）
            - 将文本中的换行符替换为空格
            - 根据最大字符数限制文本长度，并在末尾添加省略号（如果需要）
            - 返回格式化的识别结果文本
        """
        text = str(raw_text or "").strip()
        if not text:
            return "图片识别完成，但未提取到可用文本。"
        if text.startswith("## 图片识别结果"):
            text = text.replace("## 图片识别结果", "", 1).strip()
        snippet = text.replace("\n", " ").strip()
        limit = max(20, int(max_chars))
        if len(snippet) > limit:
            snippet = f"{snippet[:limit].rstrip()}..."
        return f"图片识别完成：{snippet}"
