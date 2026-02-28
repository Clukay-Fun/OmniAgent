"""
描述: 该模块定义了用于处理和验证渲染响应的Pydantic模型。
主要功能:
    - 定义了Block、CardTemplateSpec和RenderedResponse三个核心模型。
    - 提供了从外部数据构建RenderedResponse实例的方法。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal

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
    """
    表示一个内容块的模型。

    功能:
        - 定义了块的类型、内容、ID和元数据字段。
    """
    type: BlockType
    content: Dict[str, Any] = Field(default_factory=dict)
    id: str | None = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class CardTemplateSpec(BaseModel):
    """
    表示卡片模板规范的模型。

    功能:
        - 定义了模板ID、版本和参数字段。
        - 提供了验证模板ID和版本不为空的方法。
    """
    template_id: str
    version: str = "v1"
    params: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("template_id", "version")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("template_id/version must not be empty")
        return value


class RenderedResponse(BaseModel):
    """
    表示渲染响应的模型。

    功能:
        - 定义了文本回退、内容块列表、元数据和卡片模板字段。
        - 提供了验证文本回退不为空的方法。
        - 提供了从外部数据构建RenderedResponse实例的方法。
        - 提供了将实例转换为字典的方法。
    """
    text_fallback: str
    blocks: List[Block] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)
    card_template: CardTemplateSpec | None = None

    @field_validator("text_fallback")
    @classmethod
    def validate_text_fallback_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text_fallback must not be empty")
        return value

    @classmethod
    def from_outbound(
        cls,
        outbound: dict[str, Any] | None,
        fallback_text: str,
    ) -> "RenderedResponse":
        """
        从外部数据构建RenderedResponse实例。

        功能:
            - 从外部数据中提取文本回退、内容块列表、元数据和卡片模板。
            - 验证并处理提取的数据。
            - 返回构建的RenderedResponse实例。
        """
        text_fallback = str((outbound or {}).get("text_fallback") or fallback_text or "请稍后重试。")

        raw_blocks = (outbound or {}).get("blocks")
        blocks: List[Block] = []
        if isinstance(raw_blocks, list):
            for block in raw_blocks:
                if isinstance(block, dict):
                    try:
                        blocks.append(Block.model_validate(block))
                    except Exception:
                        continue

        if not blocks:
            blocks = [Block(type="paragraph", content={"text": text_fallback})]

        meta = (outbound or {}).get("meta")
        safe_meta = dict(meta) if isinstance(meta, dict) else {}

        card_template = (outbound or {}).get("card_template")
        safe_card_template = None
        if isinstance(card_template, dict):
            try:
                safe_card_template = CardTemplateSpec.model_validate(card_template)
            except Exception:
                safe_card_template = None

        return cls(
            text_fallback=text_fallback,
            blocks=blocks,
            meta=safe_meta,
            card_template=safe_card_template,
        )

    def to_dict(self) -> dict[str, Any]:
        """
        将RenderedResponse实例转换为字典。

        功能:
            - 使用Pydantic的model_dump方法将实例转换为字典。
        """
        return self.model_dump()
