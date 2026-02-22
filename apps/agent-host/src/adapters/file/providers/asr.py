from __future__ import annotations


class ASRProvider:
    def to_transcript(self, raw_text: str, max_chars: int = 4000) -> str:
        text = str(raw_text or "").strip()
        if not text:
            return ""
        normalized = " ".join(text.split())
        limit = max(40, int(max_chars))
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip()
