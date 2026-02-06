"""
描述: 用户身份匹配器
主要功能:
    - 姓名匹配"主办律师"字段
    - 调用 MCP 搜索验证
    - 匹配置信度评估
"""

from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any

from src.mcp.client import MCPClient


logger = logging.getLogger(__name__)


# ============================================
# region 身份匹配器
# ============================================
class UserMatcher:
    """
    用户身份匹配器
    
    功能:
        - 通过姓名匹配"主办律师"字段
        - 返回匹配结果与置信度
    """
    
    def __init__(
        self,
        mcp_client: MCPClient,
        match_field: str = "主办律师",
        min_confidence: float = 0.8,
    ):
        """
        初始化匹配器
        
        参数:
            mcp_client: MCP 客户端
            match_field: 匹配字段名（默认"主办律师"）
            min_confidence: 最小置信度阈值
        """
        self._mcp = mcp_client
        self._match_field = match_field
        self._min_confidence = min_confidence
    
    async def match_by_name(self, name: str) -> tuple[bool, float, Optional[List[Dict[str, Any]]]]:
        """
        通过姓名匹配律师身份
        
        参数:
            name: 用户姓名
            
        返回:
            (是否匹配成功, 置信度, 匹配到的记录列表)
        """
        if not name or not name.strip():
            return False, 0.0, None
        
        try:
            # 调用 MCP search_exact 查询
            result = await self._mcp.call_tool(
                "search_exact",
                {
                    "field": self._match_field,
                    "value": name.strip(),
                    "limit": 10,  # 最多返回 10 条
                }
            )
            
            if not result or not isinstance(result, dict):
                logger.warning(f"MCP search_exact returned invalid result: {result}")
                return False, 0.0, None
            
            records = result.get("records", [])
            total = result.get("total", 0)
            
            if total == 0:
                logger.info(f"No records found for lawyer name: {name}")
                return False, 0.0, None
            
            # 计算置信度
            # 规则：找到记录即为高置信度（因为是精确匹配）
            confidence = 1.0 if total > 0 else 0.0
            
            # 如果找到记录，认为匹配成功
            is_matched = confidence >= self._min_confidence
            
            logger.info(
                f"Matched lawyer '{name}': found {total} records, "
                f"confidence={confidence:.2f}, matched={is_matched}"
            )
            
            return is_matched, confidence, records
            
        except Exception as e:
            logger.error(f"Error matching lawyer name '{name}': {e}", exc_info=True)
            return False, 0.0, None
    
    async def verify_binding(self, name: str) -> bool:
        """
        验证绑定是否有效（简化版：检查是否能匹配到记录）
        
        参数:
            name: 律师姓名
            
        返回:
            是否有效
        """
        is_matched, _, _ = await self.match_by_name(name)
        return is_matched
# endregion
