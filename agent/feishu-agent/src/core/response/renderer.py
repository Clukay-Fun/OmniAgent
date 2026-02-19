from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

import yaml

from src.core.response.models import Block, RenderedResponse


DEFAULT_TEMPLATES: Dict[str, str] = {
    "success": "已完成 {skill_name}",
    "failure": "处理失败：{skill_name}",
}


class ResponseRenderer:
    def __init__(
        self,
        templates: Mapping[str, str] | None = None,
        templates_path: str | Path | None = None,
        assistant_name: str = "assistant",
    ) -> None:
        if templates is not None:
            self._templates = dict(templates)
        else:
            self._templates = self._load_templates(templates_path)
        self._assistant_name = assistant_name

    def render(self, skill_result: Any) -> RenderedResponse:
        payload = self._to_mapping(skill_result)
        success = bool(payload.get("success", False))
        skill_name = str(payload.get("skill_name") or "unknown")

        reply_text = payload.get("reply_text")
        message = payload.get("message")
        chosen_text = reply_text if self._is_non_blank(reply_text) else message

        template_key = "success" if success else "failure"
        template_text = self._templates.get(template_key) or DEFAULT_TEMPLATES[template_key]
        rendered_template = template_text.format(skill_name=skill_name)
        text_fallback = chosen_text if self._is_non_blank(chosen_text) else rendered_template

        blocks = [Block(type="paragraph", content={"text": str(text_fallback)})]

        data = payload.get("data")
        if isinstance(data, Mapping) and data:
            items = [{"key": str(key), "value": str(value)} for key, value in data.items()]
            blocks.append(Block(type="kv_list", content={"items": items}))

        return RenderedResponse(
            text_fallback=str(text_fallback),
            blocks=blocks,
            meta={"assistant_name": self._assistant_name, "skill_name": skill_name},
        )

    def _load_templates(self, templates_path: str | Path | None) -> Dict[str, str]:
        path = Path(templates_path) if templates_path else self._default_template_path()
        if not path.exists():
            return dict(DEFAULT_TEMPLATES)

        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return dict(DEFAULT_TEMPLATES)

        if not isinstance(parsed, Mapping):
            return dict(DEFAULT_TEMPLATES)

        merged = dict(DEFAULT_TEMPLATES)
        for key in ("success", "failure"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                merged[key] = value
        return merged

    def _default_template_path(self) -> Path:
        return Path(__file__).resolve().parents[3] / "config" / "responses.yaml"

    def _to_mapping(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "__dict__") and isinstance(value.__dict__, dict):
            return dict(value.__dict__)
        if hasattr(value, "dict") and callable(value.dict):
            return dict(value.dict())
        if hasattr(value, "model_dump") and callable(value.model_dump):
            return dict(value.model_dump())
        return {}

    def _is_non_blank(self, value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip())
