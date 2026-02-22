import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.api.inbound_normalizer import normalize_content


def test_normalize_text_content() -> None:
    normalized = normalize_content("text", json.dumps({"text": "请帮我查案件"}, ensure_ascii=False))

    assert normalized.text == "请帮我查案件"
    assert normalized.message_type == "text"
    assert normalized.segment_count == 1
    assert normalized.truncated is False


def test_normalize_post_content_with_paragraphs() -> None:
    content = {
        "post": {
            "zh_cn": {
                "content": [
                    [{"tag": "text", "text": "第一段"}],
                    [{"tag": "text", "text": "第二段"}],
                ]
            }
        }
    }

    normalized = normalize_content("post", json.dumps(content, ensure_ascii=False))

    assert normalized.message_type == "post"
    assert normalized.segment_count == 2
    assert normalized.text == "第一段\n\n第二段"


def test_normalize_file_message_to_placeholder() -> None:
    normalized = normalize_content("file", "{}")

    assert normalized.text == "[收到文件消息]"
    assert normalized.segment_count == 1
