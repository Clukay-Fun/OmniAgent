from __future__ import annotations

from src.main import app


def test_automation_routes_registered() -> None:
    routes = {getattr(route, "path", "") for route in app.routes}
    assert "/feishu/events" in routes
    assert "/automation/init" in routes
    assert "/automation/scan" in routes
