"""
描述: 用户身份匹配器
主要功能:
    - 姓名匹配多个人员字段（如"主办律师"、"协办律师"等）
    - 调用 MCP 搜索验证
    - 匹配置信度评估
"""

from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any

from src.infra.mcp.client import MCPClient


logger = logging.getLogger(__name__)


# ============================================
# region 身份匹配器
# ============================================
class UserMatcher:
    """
    用户身份匹配器

    功能:
        - 通过姓名匹配一个或多个人员字段（如"主办律师"、"协办律师"）
        - 跨字段、跨表格匹配（只要有任一字段匹配成功即认为有效）
        - 返回匹配结果与置信度
    """

    def __init__(
        self,
        mcp_client: MCPClient,
        match_field: str | list[str] = "主办律师",
        min_confidence: float = 0.8,
    ):
        """
        初始化匹配器

        参数:
            mcp_client: MCP 客户端
            match_field: 匹配字段名，支持:
                - 单个字段名字符串: "主办律师"
                - 逗号分隔的多字段字符串: "主办律师,协办律师"
                - 字符串列表: ["主办律师", "协办律师"]
            min_confidence: 最小置信度阈值
        """
        self._mcp = mcp_client
        self._match_fields = self._parse_fields(match_field)
        self._min_confidence = min_confidence

    @staticmethod
    def _parse_fields(match_field: str | list[str]) -> list[str]:
        """解析字段名配置为字段名列表"""
        if isinstance(match_field, list):
            return [f.strip() for f in match_field if str(f).strip()]
        # 字符串形式：支持逗号或中文逗号分隔
        raw = str(match_field or "").replace("，", ",")
        fields = [f.strip() for f in raw.split(",") if f.strip()]
        return fields if fields else ["主办律师"]

    async def match_by_name(
        self,
        name: str,
        extra_fields: list[str] | None = None,
    ) -> tuple[bool, float, Optional[List[Dict[str, Any]]]]:
        """
        通过姓名匹配人员身份

        按顺序依此搜索所有配置的字段，只要有任意一个字段匹配成功即返回。

        参数:
            name: 用户姓名
            extra_fields: 临时覆盖字段列表（不传则使用初始化时的 match_fields）

        返回:
            (是否匹配成功, 置信度, 匹配到的记录列表)
        """
        if not name or not name.strip():
            return False, 0.0, None

        fields_to_search = extra_fields if extra_fields else self._match_fields
        all_records: list[dict[str, Any]] = []
        matched_field: str | None = None

        for field in fields_to_search:
            try:
                result = await self._mcp.call_tool(
                    "search_exact",
                    {
                        "field": field,
                        "value": name.strip(),
                        "limit": 10,
                    },
                )

                if not result or not isinstance(result, dict):
                    logger.warning(
                        "MCP search_exact returned invalid result for field '%s': %s",
                        field,
                        result,
                    )
                    continue

                records = result.get("records", [])
                total = result.get("total", 0)

                if total > 0:
                    all_records.extend(records)
                    matched_field = field
                    logger.info(
                        "Matched user '%s' in field '%s': found %d records",
                        name,
                        field,
                        total,
                    )
                    break  # 找到即停，不必继续搜其余字段
                else:
                    logger.debug("No match for '%s' in field '%s'", name, field)

            except Exception as exc:
                logger.error(
                    "Error matching name '%s' in field '%s': %s",
                    name,
                    field,
                    exc,
                    exc_info=True,
                )
                continue

        if not all_records:
            logger.info(
                "No records found for user '%s' across fields: %s",
                name,
                self._match_fields,
            )
            return False, 0.0, None

        confidence = 1.0
        is_matched = confidence >= self._min_confidence
        logger.info(
            "Identity match result for '%s': field='%s', confidence=%.2f, matched=%s",
            name,
            matched_field,
            confidence,
            is_matched,
        )
        return is_matched, confidence, all_records

    async def verify_binding(self, name: str) -> bool:
        """
        验证绑定是否有效（简化版：检查是否能匹配到记录）

        参数:
            name: 用户姓名

        返回:
            是否有效
        """
        is_matched, _, _ = await self.match_by_name(name)
        return is_matched
# endregion
