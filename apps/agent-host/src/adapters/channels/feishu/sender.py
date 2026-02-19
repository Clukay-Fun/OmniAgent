from __future__ import annotations

from typing import Any, Callable, Dict

from adapters.channels.feishu.formatter import FeishuFormatter
from core.response.models import RenderedResponse


class FeishuSender:
    def __init__(
        self,
        send_callable: Callable[[Dict[str, Any]], Any],
        formatter: FeishuFormatter,
    ) -> None:
        self._send_callable = send_callable
        self._formatter = formatter

    def send(self, rendered: RenderedResponse) -> Any:
        payload = self._formatter.format(rendered)
        return self._send_callable(payload)
