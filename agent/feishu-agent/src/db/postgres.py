"""
PostgreSQL client.

Responsibilities:
    - Connection pool management
    - Reminder CRUD
    - Advisory locks for scheduling
Dependencies: asyncpg
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
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

    @asynccontextmanager
    async def advisory_lock(self, key: str):
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            locked = await conn.fetchval(
                "SELECT pg_try_advisory_lock(hashtext($1))",
                key,
            )
            if not locked:
                yield None
                return

            try:
                yield conn
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", key)

    async def create_reminder(
        self,
        user_id: str,
        content: str,
        due_at: Any | None,
        priority: str,
        status: str = "pending",
        source: str = "manual",
        case_id: str | None = None,
        chat_id: str | None = None,
    ) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO reminders (user_id, chat_id, content, due_at, priority, status, source, case_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                user_id,
                chat_id,
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

    async def list_due_reminders(
        self,
        conn: asyncpg.Connection,
        instance_id: str,
        lock_timeout_seconds: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = await conn.fetch(
            """
            WITH candidates AS (
                SELECT id
                FROM reminders
                WHERE status = 'pending'
                  AND due_at IS NOT NULL
                  AND due_at <= NOW()
                  AND (
                    locked_at IS NULL
                    OR locked_at < NOW() - ($1 * interval '1 second')
                  )
                ORDER BY due_at ASC
                LIMIT $2
                FOR UPDATE SKIP LOCKED
            )
            UPDATE reminders
            SET locked_by = $3,
                locked_at = NOW()
            WHERE id IN (SELECT id FROM candidates)
            RETURNING id, user_id, chat_id, content, due_at, priority, status, retry_count
            """,
            lock_timeout_seconds,
            limit,
            instance_id,
        )
        return [dict(row) for row in rows]

    async def mark_reminder_sent(self, reminder_id: int) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE reminders
                SET status = 'sent', notified_at = NOW(), updated_at = NOW()
                WHERE id = $1
                """,
                reminder_id,
            )

    async def mark_reminder_failed(self, reminder_id: int, error: str) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE reminders
                SET retry_count = COALESCE(retry_count, 0) + 1,
                    last_error = $1,
                    updated_at = NOW()
                WHERE id = $2
                """,
                error,
                reminder_id,
            )

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
