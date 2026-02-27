"""
描述: MCP 工具注册入口。
主要功能:
    - 导入并注册 bitable、calendar、doc、file 工具
    - 在服务启动时完成工具发现
"""

from src.tools import automation, bitable, calendar, doc, file  # noqa: F401
