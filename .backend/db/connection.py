"""
描述: 数据库连接管理
主要功能:
    - 获取数据库连接串
    - 创建连接上下文
依赖: psycopg
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import ContextManager, Iterator

import psycopg
from psycopg import Connection

# ============================================
# region get_database_url
# ============================================
def get_database_url() -> str:
    """
    获取数据库连接串

    返回:
        数据库连接串
    """

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is required")
    return db_url
# endregion
# ============================================

# ============================================
# region get_connection
# ============================================
@contextmanager
def get_connection(db_url: str | None = None) -> ContextManager[Connection]:
    """
    获取数据库连接上下文

    参数:
        db_url: 数据库连接串
    返回:
        数据库连接上下文
    """

    conn = psycopg.connect(db_url or get_database_url())
    try:
        yield conn
    finally:
        conn.close()
# endregion
# ============================================
