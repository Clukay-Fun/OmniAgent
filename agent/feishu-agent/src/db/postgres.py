"""PostgreSQL client for reminder storage."""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg


class PostgresClient:
    def __init__(self, settings: Any) -> None:
        self._settings = {
            "dsn": getattr(settings, "dsn", ""),
            "min_size": int(getattr(settings, "min_size", 1)),
            "max_size": int(getattr(settings, "max_size", 5)),
            "timeout": int(getattr(settings, "timeout", 10)),
        }
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool:
            return self._pool

        async with self._lock:
            if self._pool:
                return self._pool

            self._pool = await asyncpg.create_pool(
                dsn=self._settings["dsn"],
                min_size=self._settings["min_size"],
                max_size=self._settings["max_size"],
                command_timeout=self._settings["timeout"],
            )
            return self._pool

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def create_reminder(
        self,
        user_id: str,
        content: str,
        due_at: Any | None,
        priority: str,
        status: str = "pending",
        source: str = "manual",
        case_id: str | None = None,
    ) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO reminders (user_id, content, due_at, priority, status, source, case_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                user_id,
                content,
                due_at,
                priority,
                status,
                source,
                case_id,
            )
            return int(row["id"]) if row else 0

    async def list_reminders(
        self,
        user_id: str,
        status: str = "pending",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, content, due_at, priority, status
                FROM reminders
                WHERE user_id = $1 AND status = $2
                ORDER BY due_at NULLS LAST, id DESC
                LIMIT $3
                """,
                user_id,
                status,
                limit,
            )
            return [dict(row) for row in rows]

    async def update_status(self, reminder_id: int, user_id: str, status: str) -> bool:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE reminders
                SET status = $1, updated_at = NOW()
                WHERE id = $2 AND user_id = $3
                """,
                status,
                reminder_id,
                user_id,
            )
            return result.startswith("UPDATE")

    async def delete_reminder(self, reminder_id: int, user_id: str) -> bool:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM reminders WHERE id = $1 AND user_id = $2",
                reminder_id,
                user_id,
            )
            return result.startswith("DELETE")
