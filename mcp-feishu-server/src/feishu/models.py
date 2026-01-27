"""
Feishu API response models.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class FeishuResponse(BaseModel):
    code: int | None = None
    msg: str | None = None
    data: dict[str, Any] | None = None
