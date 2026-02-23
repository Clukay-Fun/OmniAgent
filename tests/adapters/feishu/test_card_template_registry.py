from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.card_template_registry import (
    CardTemplateRegistry,
    TemplateLookupError,
    TemplateValidationError,
)


def test_registry_lookup_success() -> None:
    registry = CardTemplateRegistry()

    definition = registry.lookup("query.list", "v1")

    assert definition.template_id == "query.list"
    assert definition.version == "v1"

    create_definition = registry.lookup("create.success", "v1")
    assert create_definition.template_id == "create.success"

    query_v2_definition = registry.lookup("query.list", "v2")
    assert query_v2_definition.version == "v2"


def test_registry_lookup_missing_template_raises() -> None:
    registry = CardTemplateRegistry()

    with pytest.raises(TemplateLookupError):
        registry.lookup("unknown.template", "v1")


def test_registry_validate_required_params() -> None:
    registry = CardTemplateRegistry()

    with pytest.raises(TemplateValidationError):
        registry.render(template_id="query.detail", version="v1", params={})
