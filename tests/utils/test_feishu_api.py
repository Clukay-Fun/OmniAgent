from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.utils import feishu_api


def test_is_action_unsupported_error_matches_known_tokens() -> None:
    assert feishu_api._is_action_unsupported_error("not support tag: action_list") is True
    assert feishu_api._is_action_unsupported_error("unsupported tag action") is True
    assert feishu_api._is_action_unsupported_error("schema V2 no longer support this capability") is True
    assert feishu_api._is_action_unsupported_error("another error") is False


def test_convert_card_v2_to_legacy_action_card() -> None:
    card = {
        "schema": "2.0",
        "body": {
            "elements": [
                {"tag": "markdown", "content": "hello"},
                {"tag": "action", "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "ok"}}]},
            ]
        },
        "config": {"update_multi": True},
    }

    converted = feishu_api._convert_card_v2_to_legacy(card)
    assert isinstance(converted, dict)
    assert "schema" not in converted
    assert isinstance(converted.get("elements"), list)
    assert converted["elements"][1]["tag"] == "action"


def test_convert_card_v2_to_legacy_action_list_card() -> None:
    card = {
        "schema": "2.0",
        "body": {
            "elements": [
                {"tag": "markdown", "content": "hello"},
                {
                    "tag": "action_list",
                    "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "ok"}}],
                },
            ]
        },
    }

    converted = feishu_api._convert_card_v2_to_legacy(card)
    assert isinstance(converted, dict)
    assert converted["elements"][1]["tag"] == "action"


def test_convert_card_v2_to_legacy_returns_none_without_actions() -> None:
    card = {
        "schema": "2.0",
        "body": {"elements": [{"tag": "markdown", "content": "hello"}]},
    }

    assert feishu_api._convert_card_v2_to_legacy(card) is None
