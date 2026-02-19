from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


BlockType = Literal[
    "heading",
    "paragraph",
    "bullet_list",
    "kv_list",
    "callout",
    "divider",
]


class Block(BaseModel):
    type: BlockType
    content: Dict[str, Any] = Field(default_factory=dict)
    id: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class RenderedResponse(BaseModel):
    text_fallback: str
    blocks: List[Block] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("text_fallback")
    def validate_text_fallback_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text_fallback must not be empty")
        return value
