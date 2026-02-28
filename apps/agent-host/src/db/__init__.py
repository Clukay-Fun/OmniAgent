"""
描述: 提供数据库客户端的模块
主要功能:
    - 提供对PostgreSQL数据库的客户端访问
"""

from __future__ import annotations

from src.db.postgres import PostgresClient

__all__ = ["PostgresClient"]
