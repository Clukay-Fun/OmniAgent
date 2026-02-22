from pathlib import Path
import sys

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.response.models import Block, RenderedResponse


@pytest.mark.parametrize("value", ["", " ", "\n\t"])
def test_rendered_response_text_fallback_must_not_be_blank(value):
    with pytest.raises(ValidationError):
        RenderedResponse(text_fallback=value)


@pytest.mark.parametrize(
    "block_type",
    ["heading", "paragraph", "bullet_list", "kv_list", "callout", "divider"],
)
def test_block_accepts_valid_types(block_type):
    block = Block(type=block_type)
    assert block.type == block_type


def test_block_rejects_invalid_type():
    with pytest.raises(ValidationError):
        Block(type="button")


def test_rendered_response_defaults_blocks_and_meta():
    response = RenderedResponse(text_fallback="hello")
    assert response.blocks == []
    assert response.meta == {}


def test_rendered_response_text_fallback_allows_surrounding_whitespace_when_non_empty():
    response = RenderedResponse(text_fallback="  hello  ")
    assert response.text_fallback == "  hello  "


def test_rendered_response_from_outbound_parses_card_template() -> None:
    response = RenderedResponse.from_outbound(
        outbound={
            "text_fallback": "ok",
            "blocks": [{"type": "paragraph", "content": {"text": "ok"}}],
            "card_template": {
                "template_id": "query.list",
                "version": "v1",
                "params": {"records": []},
            },
        },
        fallback_text="fallback",
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "query.list"


def test_rendered_response_from_outbound_ignores_invalid_card_template() -> None:
    response = RenderedResponse.from_outbound(
        outbound={
            "text_fallback": "ok",
            "blocks": [{"type": "paragraph", "content": {"text": "ok"}}],
            "card_template": {"template_id": "", "version": "v1", "params": {}},
        },
        fallback_text="fallback",
    )

    assert response.card_template is None
