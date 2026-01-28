from __future__ import annotations

import asyncio

from src.config import Settings
from src.feishu.token import TenantAccessTokenManager


class TestTokenManager(TenantAccessTokenManager):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls = 0

    async def _fetch_token(self) -> tuple[str, int]:
        self.calls += 1
        return "token", 60


def test_token_cached_and_refreshable() -> None:
    async def run() -> None:
        settings = Settings()
        settings.feishu.token.refresh_ahead_seconds = 0
        manager = TestTokenManager(settings)

        token1 = await manager.get_token()
        token2 = await manager.get_token()
        assert token1 == token2
        assert manager.calls == 1

        manager._expires_at = 0
        token3 = await manager.get_token()
        assert token3 == "token"
        assert manager.calls == 2

    asyncio.run(run())
