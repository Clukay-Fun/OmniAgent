"""
描述: 该文件负责初始化和配置BitableAdapter，用于与Bitable进行交互。
主要功能:
    - 初始化BitableAdapter
    - 提供BitableAdapter的实例以供其他模块使用
"""

from __future__ import annotations

from src.core.skills.bitable_adapter import BitableAdapter as TableAdapter

# region 初始化BitableAdapter
table_adapter = TableAdapter()
# endregion
