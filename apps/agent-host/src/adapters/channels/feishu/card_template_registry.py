from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.adapters.channels.feishu.card_template_config import is_template_enabled
from src.adapters.channels.feishu.card_templates import (
    render_action_confirm_v1,
    render_error_notice_v1,
    render_query_detail_v1,
    render_query_list_v1,
    render_todo_reminder_v1,
    render_upload_result_v1,
)


class TemplateLookupError(Exception):
    pass


class TemplateValidationError(Exception):
    pass


TemplateRenderer = Callable[[dict[str, Any]], list[dict[str, Any]]]


@dataclass(frozen=True)
class CardTemplateDefinition:
    template_id: str
    version: str
    required_params: tuple[str, ...]
    renderer: TemplateRenderer


class CardTemplateRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], CardTemplateDefinition] = {}
        self._register_defaults()

    def lookup(self, template_id: str, version: str) -> CardTemplateDefinition:
        key = (template_id, version)
        definition = self._definitions.get(key)
        if definition is None:
            raise TemplateLookupError(f"template not found: {template_id}.{version}")
        if not is_template_enabled(template_id, version):
            raise TemplateLookupError(f"template disabled: {template_id}.{version}")
        return definition

    def render(self, template_id: str, version: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        definition = self.lookup(template_id, version)
        self._validate(definition, params)
        elements = definition.renderer(params)
        return [item for item in elements if isinstance(item, dict)]

    def register(self, definition: CardTemplateDefinition) -> None:
        self._definitions[(definition.template_id, definition.version)] = definition

    def _validate(self, definition: CardTemplateDefinition, params: dict[str, Any]) -> None:
        if not isinstance(params, dict):
            raise TemplateValidationError("template params must be dict")

        missing = [key for key in definition.required_params if key not in params]
        if missing:
            raise TemplateValidationError(
                f"missing required params for {definition.template_id}.{definition.version}: {missing}"
            )

    def _register_defaults(self) -> None:
        self.register(
            CardTemplateDefinition(
                template_id="query.list",
                version="v1",
                required_params=("records",),
                renderer=render_query_list_v1,
            )
        )
        self.register(
            CardTemplateDefinition(
                template_id="query.detail",
                version="v1",
                required_params=("record",),
                renderer=render_query_detail_v1,
            )
        )
        self.register(
            CardTemplateDefinition(
                template_id="action.confirm",
                version="v1",
                required_params=("message",),
                renderer=render_action_confirm_v1,
            )
        )
        self.register(
            CardTemplateDefinition(
                template_id="error.notice",
                version="v1",
                required_params=("message",),
                renderer=render_error_notice_v1,
            )
        )
        self.register(
            CardTemplateDefinition(
                template_id="todo.reminder",
                version="v1",
                required_params=("message",),
                renderer=render_todo_reminder_v1,
            )
        )
        self.register(
            CardTemplateDefinition(
                template_id="upload.result",
                version="v1",
                required_params=("file_name",),
                renderer=render_upload_result_v1,
            )
        )
