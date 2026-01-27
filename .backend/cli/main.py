"""
描述: CLI 入口
主要功能:
    - 注册 Typer 子命令
    - 统一数据库连接参数
依赖: typer
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

import typer

from cli.commands.chat import app as chat_app

load_dotenv(override=True)

app = typer.Typer(help="Universal RAG CLI")
app.add_typer(chat_app, name="chat")

# ============================================
# region main
# ============================================
@app.callback()
def main(
    ctx: typer.Context,
    db_url: str = typer.Option(
        os.getenv("DATABASE_URL", ""),
        "--db-url",
        help="PostgreSQL 连接串",
    ),
) -> None:
    """
    CLI 全局入口

    参数:
        ctx: Typer 上下文
        db_url: 数据库连接串
    返回:
        None
    """

    if db_url:
        ctx.obj = {"db_url": db_url}
    else:
        ctx.obj = {}
# endregion
# ============================================


if __name__ == "__main__":
    app()
