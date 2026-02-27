"""
描述: 提供记录和日志使用情况的功能
主要功能:
    - 定义使用记录的数据结构
    - 实现使用记录的日志记录功能
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Callable

logger = logging.getLogger(__name__)

@dataclass
class UsageRecord:
    """
    表示单个使用记录的数据结构

    功能:
        - 存储使用记录的详细信息
        - 提供将记录转换为 JSON 字符串的方法
    """
    ts: str
    user_id: str
    conversation_id: str
    model: str
    skill: str
    token_count: int
    cost: float
    usage_source: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    business_metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """
        将使用记录转换为 JSON 字符串

        功能:
            - 构建包含记录信息的字典
            - 将字典转换为 JSON 字符串
        """
        payload: dict[str, Any] = {
            "ts": self.ts,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "model": self.model,
            "skill": self.skill,
            "token_count": max(0, int(self.token_count)),
            "prompt_tokens": max(0, int(self.prompt_tokens)),
            "completion_tokens": max(0, int(self.completion_tokens)),
            "cost": float(self.cost),
            "usage_source": self.usage_source,
            "estimated": bool(self.estimated),
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        if self.business_metadata:
            payload["business_metadata"] = self.business_metadata
        return json.dumps(payload, ensure_ascii=False)


class UsageLogger:
    """
    提供使用记录的日志记录功能

    功能:
        - 初始化日志记录器
        - 记录使用记录到文件
        - 处理记录写入后的回调
    """
    def __init__(
        self,
        enabled: bool,
        path_template: str,
        fail_open: bool = True,
        on_record_written: Callable[[UsageRecord], None] | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._path_template = str(path_template or "workspace/usage/usage_log-{date}.jsonl")
        self._fail_open = bool(fail_open)
        self._on_record_written = on_record_written

    def log(self, record: UsageRecord) -> bool:
        """
        记录使用记录到文件

        功能:
            - 检查日志记录是否启用
            - 解析日志文件路径
            - 创建文件目录（如果不存在）
            - 将记录写入文件
            - 调用记录写入后的回调（如果存在）
        """
        if not self._enabled:
            return False

        path = self._resolve_path(record.ts)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(record.to_json())
                f.write("\n")
            if callable(self._on_record_written):
                try:
                    self._on_record_written(record)
                except Exception as exc:
                    logger.warning(
                        "usage post-write hook failed: %s",
                        exc,
                        extra={"event_code": "usage_log.post_write_hook_failed"},
                    )
            return True
        except Exception as exc:
            logger.warning(
                "usage log write failed: %s",
                exc,
                extra={"event_code": "usage_log.write_failed", "path": str(path)},
            )
            if self._fail_open:
                return False
            raise

    def _resolve_path(self, timestamp: str) -> Path:
        """
        解析日志文件路径

        功能:
            - 从时间戳中提取日期
            - 将日期插入路径模板中
            - 返回解析后的路径
        """
        day = _extract_date(timestamp)
        rendered = self._path_template.replace("{date}", day)
        return Path(rendered)


# region 辅助函数
def now_iso() -> str:
    """
    获取当前时间的 ISO 格式字符串

    功能:
        - 获取当前时间
        - 格式化为 ISO 格式字符串
    """
    return datetime.now().isoformat(timespec="seconds")


def _extract_date(ts: str) -> str:
    """
    从时间戳中提取日期

    功能:
        - 检查时间戳格式
        - 提取日期部分
        - 返回日期字符串
    """
    value = str(ts or "")
    if len(value) >= 10:
        candidate = value[:10]
        if candidate[4:5] == "-" and candidate[7:8] == "-":
            return candidate
    return datetime.now().strftime("%Y-%m-%d")
# endregion
