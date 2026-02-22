from __future__ import annotations


class OCRProvider:
    def to_context_markdown(self, raw_text: str) -> str:
        text = str(raw_text or "").strip()
        if not text:
            return ""
        return f"## 图片识别结果\n\n{text}"

    def build_completion_text(self, raw_text: str, max_chars: int = 140) -> str:
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
