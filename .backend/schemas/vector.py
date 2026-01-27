"""
描述: 向量化与检索 Schema
主要功能:
    - 向量构建请求/响应
    - 检索请求/响应
依赖: pydantic
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class VectorBuildRequest(BaseModel):
    """
    向量构建请求
    """

    doc_id: int | None = Field(None, description="文档ID")
    batch_size: int = Field(16, description="批量大小")

    model_config = ConfigDict(extra="forbid")


class VectorBuildResponse(BaseModel):
    """
    向量构建响应
    """

    doc_id: int | None
    processed: int
    updated: int

    model_config = ConfigDict(extra="forbid")


class VectorSearchRequest(BaseModel):
    """
    向量检索请求
    """

    query_text: str = Field(..., description="查询文本")
    top_k: int = Field(5, description="返回数量")
    doc_id: int | None = Field(None, description="文档ID")

    model_config = ConfigDict(extra="forbid")


class VectorSearchHit(BaseModel):
    """
    向量检索结果
    """

    doc_id: int
    node_id: int
    title: str
    content: str
    path: list[str]
    party_a_name: str | None = None
    party_a_credit_code: str | None = None
    score: float

    model_config = ConfigDict(extra="forbid")


class VectorSearchResponse(BaseModel):
    """
    向量检索响应
    """

    query_text: str
    hits: list[VectorSearchHit]

    model_config = ConfigDict(extra="forbid")
